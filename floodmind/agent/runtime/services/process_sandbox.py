"""
ProcessSandbox — 子代理进程树隔离。

把子代理启动的 Bash / 子进程绑定到一个独立的进程组 / Job Object，
子代理退出时强制清理整棵进程树，防止残留。

Windows: Job Object + TerminateJobObject
Linux:   os.setsid + os.killpg
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ProcessSandbox:
    """管理子代理的进程树。"""

    def __init__(self, max_processes: int = 32, workspace_dir: Optional[Path] = None):
        self._max_processes = max(max_processes, 1)
        self._workspace_dir = workspace_dir
        self._processes: List[subprocess.Popen] = []
        self._job_handle: Optional[Any] = None
        self._platform = "win" if os.name == "nt" else "posix"
        if self._platform == "win":
            self._init_windows_job()

    @property
    def workspace_dir(self) -> Optional[Path]:
        return self._workspace_dir

    # ── public API ──────────────────────────────────────────────────────────

    def wrap_popen_kwargs(self, popen_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """返回增加了沙盒参数的 subprocess.Popen kwargs。"""
        kwargs = dict(popen_kwargs)
        if self._platform == "win":
            flags = kwargs.get("creationflags", 0)
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
            kwargs["creationflags"] = flags
        else:
            # Linux: 新 session，便于 killpg
            if "preexec_fn" not in kwargs:
                kwargs["preexec_fn"] = os.setsid
        return kwargs

    def register_process(self, process: subprocess.Popen) -> None:
        """把一个已启动的进程加入沙盒。"""
        self._processes.append(process)
        if self._platform == "win" and self._job_handle is not None:
            self._assign_to_job(process)

    def terminate_all(self) -> None:
        """结束沙盒内所有进程。"""
        if self._platform == "win" and self._job_handle is not None:
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.TerminateJobObject(self._job_handle, 1)
            except Exception as e:
                logger.warning("ProcessSandbox: TerminateJobObject failed: %s", e)
        else:
            for proc in self._processes:
                try:
                    if proc.poll() is None:
                        if self._platform == "win":
                            proc.kill()
                        else:
                            try:
                                os.killpg(os.getpgid(proc.pid), 9)
                            except ProcessLookupError:
                                proc.kill()
                except Exception as e:
                    logger.warning("ProcessSandbox: kill process failed: %s", e)

        # 清理句柄
        if self._platform == "win" and self._job_handle is not None:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self._job_handle)
            except Exception as e:
                logger.warning("ProcessSandbox: CloseHandle failed: %s", e)
            self._job_handle = None

    def restrict_env(self, env: Dict[str, str], workspace_dir: Path) -> Dict[str, str]:
        """返回限制后的环境变量。"""
        restricted = dict(env)
        workspace_str = str(workspace_dir)
        restricted["TEMP"] = workspace_str
        restricted["TMP"] = workspace_str
        restricted["TMPDIR"] = workspace_str
        restricted["MPLCONFIGDIR"] = str(workspace_dir / ".matplotlib")
        # 移除可能泄露父环境的路径/凭证变量
        for key in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
            restricted.pop(key, None)
        return restricted

    # ── Windows helpers ─────────────────────────────────────────────────────

    def _init_windows_job(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            self._kernel32 = kernel32

            self._job_handle = kernel32.CreateJobObjectW(None, None)
            if not self._job_handle:
                logger.warning("ProcessSandbox: CreateJobObjectW failed")
                return

            # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("LimitFlags", wintypes.DWORD),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_void_p),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", wintypes.ULARGE_INTEGER),
                    ("WriteOperationCount", wintypes.ULARGE_INTEGER),
                    ("OtherOperationCount", wintypes.ULARGE_INTEGER),
                    ("ReadTransferCount", wintypes.ULARGE_INTEGER),
                    ("WriteTransferCount", wintypes.ULARGE_INTEGER),
                    ("OtherTransferCount", wintypes.ULARGE_INTEGER),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0008

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            )
            info.BasicLimitInformation.ActiveProcessLimit = self._max_processes

            JobObjectExtendedLimitInformation = 9
            if not kernel32.SetInformationJobObject(
                self._job_handle,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                logger.warning("ProcessSandbox: SetInformationJobObject failed")
        except Exception as e:
            logger.warning("ProcessSandbox: init windows job failed: %s", e)
            self._job_handle = None

    def _assign_to_job(self, process: subprocess.Popen) -> None:
        try:
            import ctypes
            handle = ctypes.c_void_p(process._handle)
            if not self._kernel32.AssignProcessToJobObject(self._job_handle, handle):
                logger.warning("ProcessSandbox: AssignProcessToJobObject failed")
        except Exception as e:
            logger.warning("ProcessSandbox: assign process failed: %s", e)


# 全局 registry：session_id -> ProcessSandbox
_PROCESS_SANDBOXES: Dict[str, "ProcessSandbox"] = {}


def register_process_sandbox(session_id: str, sandbox: ProcessSandbox) -> None:
    _PROCESS_SANDBOXES[session_id] = sandbox


def get_process_sandbox(session_id: str) -> Optional[ProcessSandbox]:
    return _PROCESS_SANDBOXES.get(session_id)


def unregister_process_sandbox(session_id: str) -> None:
    _PROCESS_SANDBOXES.pop(session_id, None)
