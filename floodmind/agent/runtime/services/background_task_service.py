"""
BackgroundTaskService — 后台任务服务

让 exec_bash 支持 run_in_background=True：命令立即返回 task_id，子进程在后台运行，
stdout/stderr 直接重定向到文件（不用 PIPE + reader 线程，无管道缓冲区死锁风险），
进程退出即文件完整，Agent 要看全文用 TaskOutput / Read。

设计要点：
- stdout/stderr 直接 open(path,'wb')；进程退出即文件完整
- 每任务一个 daemon wait() 守护线程：退出 → 更新状态 → 完成队列 → 触发 subscribe 回调
- 文件落 .floodmind/sessions/<sid>/background/<task_id>/{out.log,err.log,meta.json}
- Windows 杀进程树 taskkill /PID <pid> /T /F；POSIX os.killpg（preexec_fn=setsid）
- 护栏：单会话最大并发后台任务数（默认 8）、单任务最大存活（默认 1800s 兜底 kill，
  可被 start 参数覆盖）；会话结束/Agent 析构默认 kill 本会话存活任务（meta.json 保留供审计）
- 后台任务不受 exec_bash 同步 120s 超时限制（这正是它存在的意义），TaskKill 必须可用
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from floodmind.memory.session_manager import session_path, validate_session_id

logger = logging.getLogger(__name__)

# 护栏默认值
_DEFAULT_MAX_CONCURRENT_PER_SESSION = 8
_DEFAULT_MAX_LIFETIME_SECONDS = 30 * 60  # 30 分钟兜底 kill

# 完成通知注入时的输出尾部最大长度
_NOTIFICATION_TAIL_MAX = 4000

# 内存索引仅用于近期任务查询/通知；meta/log 文件仍完整保留供审计。
_DEFAULT_COMPLETED_RETENTION = 1000
_DEFAULT_FINALIZED_RETENTION = 4000


@dataclass
class BackgroundTask:
    """一个后台任务及其运行时状态。"""

    task_id: str
    session_id: str
    command: str
    pid: Optional[int]
    status: str  # running / completed / failed / killed
    exit_code: Optional[int]
    stdout_path: str
    stderr_path: str
    meta_path: str
    started_at: float
    max_lifetime_seconds: int
    finished_at: Optional[float] = None
    tail: str = ""
    error: str = ""

    @property
    def running(self) -> bool:
        return self.status == "running"

    def to_meta_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("tail", None)
        d.pop("error", None)
        return d


class BackgroundTaskService:
    """后台任务管理器（进程启动 / 状态跟踪 / 完成通知 / 进程树清理）。"""

    def __init__(
        self,
        base_dir: Optional[str] = None,
        max_concurrent_per_session: int = _DEFAULT_MAX_CONCURRENT_PER_SESSION,
        max_lifetime_seconds: int = _DEFAULT_MAX_LIFETIME_SECONDS,
        completed_retention: int = _DEFAULT_COMPLETED_RETENTION,
        finalized_retention: int = _DEFAULT_FINALIZED_RETENTION,
    ):
        self._base_dir = Path(base_dir) if base_dir else None
        self._max_concurrent_per_session = max(max_concurrent_per_session, 1)
        self._max_lifetime_seconds = max(max_lifetime_seconds, 60)
        self._completed_retention = max(completed_retention, 1)
        self._finalized_retention = max(finalized_retention, self._completed_retention)

        self._lock = threading.RLock()
        self._active_tasks: Dict[str, BackgroundTask] = {}
        # start() 在释放锁执行目录/文件/Popen IO 前预占的并发槽位。
        self._pending_tasks: Dict[str, str] = {}  # task_id -> session_id
        self._completed: List[BackgroundTask] = []
        # (session_id, callback)；session_id=None 是显式兼容的 legacy 全局订阅。
        self._subscribers: List[Tuple[Optional[str], Callable[[BackgroundTask], None]]] = []
        self._processes: Dict[str, subprocess.Popen] = {}
        # 已收尾的任务使用有界 FIFO + set，兼顾幂等与长期服务内存上界。
        self._finalized: set = set()
        self._finalized_order: Deque[str] = deque()
        self._platform = "win" if os.name == "nt" else "posix"

    # ── 公开 API ─────────────────────────────────────────────────────

    def start(
        self,
        session_id: str,
        command: str,
        shell_cmd: List[str],
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        popen_kwargs: Optional[Dict[str, Any]] = None,
        max_lifetime_seconds: Optional[int] = None,
    ) -> BackgroundTask:
        """启动一个后台任务，立即返回 BackgroundTask（不含阻塞等待）。

        已通过调用方（exec_bash）的全部安全管线（危险命令检查、workdir 校验、
        sandbox env/kwargs）。stdout/stderr 直接写文件。
        """
        lifetime = int(max_lifetime_seconds or self._max_lifetime_seconds)
        session_id = validate_session_id(session_id)

        task_id = uuid.uuid4().hex[:12]
        with self._lock:
            running_count = sum(
                1 for task in self._active_tasks.values()
                if task.session_id == session_id and task.running
            )
            pending_count = sum(1 for pending_session in self._pending_tasks.values() if pending_session == session_id)
            if running_count + pending_count >= self._max_concurrent_per_session:
                raise RuntimeError(
                    f"会话 {session_id} 后台任务已达上限 {self._max_concurrent_per_session} 个，"
                    f"请先 TaskKill 或等待完成后再启动"
                )
            self._pending_tasks[task_id] = session_id

        # 目录、文件和 Popen 都可能阻塞，槽位已预占后在全局锁外执行。
        out_f = None
        err_f = None
        process = None
        try:
            task_dir = self._background_dir(session_id) / task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            out_path = task_dir / "out.log"
            err_path = task_dir / "err.log"
            meta_path = task_dir / "meta.json"
            out_f = out_path.open("wb")
            err_f = err_path.open("wb")

            kwargs: Dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": out_f,
                "stderr": err_f,
                "env": env,
                "cwd": cwd,
            }
            if popen_kwargs:
                # 调用方已 wrap 的 sandbox 参数（creationflags/preexec_fn）优先保留
                for k, v in popen_kwargs.items():
                    if k not in kwargs:
                        kwargs[k] = v
            if self._platform == "posix" and "preexec_fn" not in kwargs:
                kwargs["preexec_fn"] = os.setsid  # 独立进程组，killpg 可靠

            process = subprocess.Popen(shell_cmd, **kwargs)
            task = BackgroundTask(
                task_id=task_id,
                session_id=session_id,
                command=command,
                pid=process.pid,
                status="running",
                exit_code=None,
                stdout_path=str(out_path),
                stderr_path=str(err_path),
                meta_path=str(meta_path),
                started_at=time.time(),
                max_lifetime_seconds=lifetime,
            )
            self._write_meta(task)

            with self._lock:
                self._pending_tasks.pop(task_id, None)
                self._active_tasks[task_id] = task
                self._processes[task_id] = process
        except Exception:
            for stream in (out_f, err_f):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
            if process is not None:
                self._kill_process(process)
            with self._lock:
                self._pending_tasks.pop(task_id, None)
            raise
        # 进程句柄由守护线程负责释放
        watcher = threading.Thread(
            target=self._watch,
            args=(task, process, out_f, err_f, lifetime),
            name=f"bg-task-{task_id[:8]}",
            daemon=True,
        )
        try:
            watcher.start()
        except Exception:
            task.status = "killed"
            self._kill_process(process)
            for stream in (out_f, err_f):
                try:
                    stream.close()
                except Exception:
                    pass
            task.finished_at = time.time()
            self._write_meta(task)
            self._finalize(task)
            raise
        logger.info("BackgroundTask started: task=%s session=%s pid=%s", task_id, session_id, process.pid)
        return task

    def get(self, session_id: str, task_id: str) -> Optional[BackgroundTask]:
        """按 task_id 查任务（活跃 + 已完成均可）。"""
        session_id = validate_session_id(session_id)
        with self._lock:
            t = self._active_tasks.get(task_id)
            if t is not None and t.session_id == session_id:
                return t
            for t in self._completed:
                if t.task_id == task_id and t.session_id == session_id:
                    return t
        return None

    def list(self, session_id: str) -> List[BackgroundTask]:
        """列出会话全部任务（已完成在前，活跃在后）。"""
        session_id = validate_session_id(session_id)
        with self._lock:
            completed = [t for t in self._completed if t.session_id == session_id]
            running = [t for t in self._active_tasks.values() if t.session_id == session_id]
            return completed + running

    def kill(self, session_id: str, task_id: str) -> bool:
        """杀掉一个任务（进程树），并立即进入完成队列/通知订阅者（Agent 感知状态变化）。"""
        session_id = validate_session_id(session_id)
        with self._lock:
            task = self._active_tasks.get(task_id)
            process = self._processes.get(task_id)
        if task is None or task.session_id != session_id:
            return False
        if task.status == "running":
            # 先标记 killed，抢在 _watch 线程把 process.wait() 的退出码写成 failed 之前
            task.status = "killed"
        self._kill_process(process)
        task.finished_at = time.time()
        task.tail = self._read_tail(task.stdout_path)
        self._write_meta(task)
        self._finalize(task)  # 用户主动关闭：立即推送，不等 _watch 线程
        return True

    def kill_session(self, session_id: str) -> int:
        """杀掉会话内全部存活任务并立即收尾（会话结束/Agent 析构时调用）。"""
        session_id = validate_session_id(session_id)
        with self._lock:
            targets = [t for t in self._active_tasks.values() if t.session_id == session_id and t.running]
            work = []
            for task in targets:
                # 锁内只抢占状态和快照句柄；进程等待、文件 IO、订阅回调全部在锁外。
                task.status = "killed"
                work.append((task, self._processes.get(task.task_id)))

        for task, process in work:
            self._kill_process(process)
            task.finished_at = time.time()
            task.tail = self._read_tail(task.stdout_path)
            self._write_meta(task)
            self._finalize(task)
        return len(targets)

    def drain_completions(self, session_id: str) -> List[BackgroundTask]:
        """取出本会话已完成的全部任务（注入完成通知用，取后清空）。"""
        session_id = validate_session_id(session_id)
        with self._lock:
            drained = [t for t in self._completed if t.session_id == session_id]
            self._completed = [t for t in self._completed if t.session_id != session_id]
            return drained

    def subscribe(
        self,
        callback: Callable[[BackgroundTask], None],
        *,
        session_id: Optional[str] = None,
    ) -> Callable[[], None]:
        """订阅任务完成回调。

        ``session_id`` 指定时仅接收该会话；显式省略则保留 legacy 全局订阅行为。
        返回的 unsubscribe 幂等。
        """
        if session_id is not None:
            session_id = validate_session_id(session_id)
        subscription = (session_id, callback)
        with self._lock:
            if subscription not in self._subscribers:
                self._subscribers.append(subscription)

        def unsubscribe() -> None:
            with self._lock:
                if subscription in self._subscribers:
                    self._subscribers.remove(subscription)

        return unsubscribe

    # ── 内部 ─────────────────────────────────────────────────────────

    def _watch(self, task: BackgroundTask, process: subprocess.Popen, out_f, err_f, lifetime: int) -> None:
        """守护线程：等进程退出 → 更新状态 → 收尾（完成队列 + 通知订阅者）。

        若任务已被外部 kill（status 已置 "killed"），不覆盖该状态。
        """
        try:
            try:
                exit_code = process.wait(timeout=lifetime)
                if task.status == "running":  # 未被外部 kill 抢占
                    task.status = "completed" if exit_code == 0 else "failed"
                    task.exit_code = int(exit_code)
            except subprocess.TimeoutExpired:
                logger.warning("BackgroundTask %s 超过 %ds 存活上限，强制 kill", task.task_id, lifetime)
                self._kill_process(process)
                if task.status == "running":
                    task.status = "killed"
                    task.exit_code = None
                    task.error = f"超过最大存活时间 {lifetime}s，已被强制终止"
        except Exception as e:
            logger.warning("BackgroundTask %s wait 异常: %s", task.task_id, e)
            if task.status == "running":
                task.status = "failed"
                task.exit_code = None
                task.error = str(e)
        finally:
            for f in (out_f, err_f):
                try:
                    f.close()
                except Exception:
                    pass
            task.finished_at = task.finished_at or time.time()
            task.tail = self._read_tail(task.stdout_path)
            self._write_meta(task)
            self._finalize(task)

    def _finalize(self, task: BackgroundTask) -> None:
        """幂等收尾：移出活跃 → 进完成队列 → 通知订阅者。kill()/kill_session()/wait 线程共用。"""
        with self._lock:
            if task.task_id in self._finalized:
                return
            self._finalized.add(task.task_id)
            self._finalized_order.append(task.task_id)
            while len(self._finalized_order) > self._finalized_retention:
                self._finalized.discard(self._finalized_order.popleft())
            self._active_tasks.pop(task.task_id, None)
            self._processes.pop(task.task_id, None)
            self._completed.append(task)
            if len(self._completed) > self._completed_retention:
                del self._completed[: len(self._completed) - self._completed_retention]
            subscribers = [
                callback
                for subscribed_session_id, callback in self._subscribers
                if subscribed_session_id is None or subscribed_session_id == task.session_id
            ]
        logger.info(
            "BackgroundTask finished: task=%s status=%s exit=%s",
            task.task_id, task.status, task.exit_code,
        )
        for cb in subscribers:
            try:
                cb(task)
            except Exception as e:
                logger.warning("BackgroundTask subscriber error: %s", e)

    def _kill_process(self, process: Optional[subprocess.Popen]) -> None:
        """杀整棵进程树。Windows: taskkill /T /F；POSIX: killpg。"""
        if process is None:
            return
        try:
            if process.poll() is not None:
                return
            if self._platform == "win":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            else:
                os.killpg(os.getpgid(process.pid), 9)
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()
        except Exception as e:
            logger.warning("BackgroundTask kill failed pid=%s: %s", process.pid, e)

    def _read_tail(self, path: str, limit: int = _NOTIFICATION_TAIL_MAX) -> str:
        """有界读取 stdout 尾部（供完成通知注入），不把大日志整体载入内存。"""
        if limit <= 0:
            return ""
        try:
            p = Path(path)
            with p.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                # UTF-8 最多 4 bytes/字符；多读以确保解码后能返回 limit 字符。
                stream.seek(max(0, size - limit * 4), os.SEEK_SET)
                data = stream.read(limit * 4)
            text = data.decode("utf-8", errors="replace")
            return text[-limit:]
        except Exception:
            return ""

    def _write_meta(self, task: BackgroundTask) -> None:
        try:
            Path(task.meta_path).write_text(
                json.dumps(task.to_meta_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("BackgroundTask meta write failed: %s", e)

    def _background_dir(self, session_id: str) -> Path:
        """定位会话后台目录，并强制限制在所选 session root 内。"""
        session_id = validate_session_id(session_id)
        session_root: Optional[Path] = None
        if self._base_dir is not None:
            session_root = self._base_dir
        else:
            state_dir = ""
            try:
                from floodmind.tools.session_context import SESSION_CONTEXT

                state_dir = SESSION_CONTEXT.get("state_dir", "") or ""
            except Exception:
                pass
            if state_dir:
                session_root = Path(state_dir) / "sessions"
            else:
                try:
                    from floodmind.agent.runtime.services.workspace_service import get_workspace

                    ws = get_workspace()
                    if ws is not None:
                        session_root = Path(ws.session_root)
                except Exception:
                    pass
            if session_root is None:
                try:
                    from floodmind.tools.session_context import SESSION_CONTEXT

                    out_dir = SESSION_CONTEXT.get("output_dir", "") or ""
                    if out_dir:
                        session_root = Path(out_dir) / ".floodmind" / "sessions"
                except Exception:
                    pass
        if session_root is None:
            raise ValueError("缺少 workspace/state_dir，无法定位后台任务目录")
        return session_path(session_root, session_id, "background")
