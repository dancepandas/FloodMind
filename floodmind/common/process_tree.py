"""子进程树终止的跨平台实现。

POSIX 下 ``killpg`` 只能用于「已与调用者隔离进程组」的子进程。若子进程未
``setsid``，它与调用者同属一个进程组，``killpg`` 会把调用者自身一起杀掉
（Agent 进程、pytest、CI runner worker）。因此这里约定：

- 启动子进程时用 :func:`isolate_process_group` 让它进入独立 session；
- 终止时 :func:`terminate_process_tree` 会检查进程组归属，未隔离的子进程
  退回 ``process.kill()``，只杀直接子进程，绝不连带调用者。

Windows 用 ``taskkill /T /F`` 杀整棵树。
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def isolate_process_group(popen_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """让 POSIX 子进程进入独立 session，使 ``killpg`` 只作用于该子进程树。

    调用方若已显式指定 ``start_new_session`` 或 ``preexec_fn``，保持原样。
    """
    if os.name == "nt":
        return popen_kwargs
    if "start_new_session" in popen_kwargs or "preexec_fn" in popen_kwargs:
        return popen_kwargs
    popen_kwargs["start_new_session"] = True
    return popen_kwargs


def _kill_posix_tree(process: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    if pgid == os.getpgrp():
        # 未隔离：与调用者同组，killpg 会杀死调用者自身
        process.kill()
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def terminate_process_tree(
    process: Optional[subprocess.Popen], *, wait_timeout: float = 5.0
) -> None:
    """终止子进程及其派生的整棵树。

    Windows: ``taskkill /PID <pid> /T /F``；POSIX: 对独立进程组 ``killpg``，
    未隔离时退回 ``process.kill()``。等待超时或异常时兜底杀直接子进程。
    """
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        else:
            _kill_posix_tree(process)
        try:
            process.wait(timeout=wait_timeout)
        except subprocess.TimeoutExpired:
            process.kill()
    except Exception as e:
        logger.warning("终止进程树失败 pid=%s: %s", process.pid, e)
        try:
            process.kill()
        except Exception:
            pass
