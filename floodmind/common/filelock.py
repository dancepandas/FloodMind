"""跨进程文件锁——统一封装 Windows msvcrt / POSIX fcntl 两种实现。

历史上 journal_writer 内嵌了一套 msvcrt 锁（约 10 秒重试即抛裸 OSError），
SessionManager / ScheduledTaskRuntime / 长期记忆则完全没有跨进程锁。
本模块提供唯一的跨进程锁实现：

- ``FileLock(path)``：可重入式（同线程内）跨进程排他锁，超时可配置；
- Windows: msvcrt.locking(LK_LOCK) 循环重试直到超时（每次约 1 秒）；
- POSIX: fcntl.flock(LOCK_EX | LOCK_NB) 循环重试直到超时（每次 50ms）；
- 超时抛 :class:`FileLockTimeoutError`（调用方决定 fail-closed 或降级）。

用法::

    with FileLock(Path("data/x.lock"), timeout=10.0):
        ...  # read-modify-write 临界区
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Union

if os.name == "nt":
    import msvcrt

logger = logging.getLogger(__name__)


class FileLockError(Exception):
    """文件锁基础异常。"""


class FileLockTimeoutError(FileLockError):
    """在 timeout 内未能获取文件锁。"""


class FileLock:
    """基于锁文件的跨进程排他锁（同线程内可重入，跨线程不共享）。"""

    def __init__(self, path: Union[str, Path], timeout: float = 10.0):
        self.path = Path(path)
        self.timeout = float(timeout)
        self._local = threading.local()

    # ── 线程内可重入支持 ────────────────────────────────────────
    @property
    def _depth(self) -> int:
        return getattr(self._local, "depth", 0)

    @_depth.setter
    def _depth(self, value: int) -> None:
        self._local.depth = value

    @property
    def _fd(self) -> int:
        return getattr(self._local, "fd", -1)

    @_fd.setter
    def _fd(self, value: int) -> None:
        self._local.fd = value

    def acquire(self) -> None:
        if self._depth > 0:
            self._depth += 1
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 追加模式打开：不存在即创建，不影响其他句柄
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + max(self.timeout, 0.0)
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                os.close(fd)
                if time.monotonic() >= deadline:
                    raise FileLockTimeoutError(
                        f"获取文件锁超时（{self.timeout}s）: {self.path}"
                    )
                time.sleep(0.05 if os.name != "nt" else 1.0)
                fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        self._fd = fd
        self._depth = 1

    def release(self) -> None:
        if self._depth == 0:
            return
        self._depth -= 1
        if self._depth > 0:
            return
        fd, self._fd = self._fd, -1
        if fd < 0:
            return
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError as exc:
            logger.warning("释放文件锁失败 %s: %s", self.path, exc)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def fsync_file(path: Union[str, Path]) -> None:
    """对已存在文件做一次 fsync（按路径打开只读句柄）。"""
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
