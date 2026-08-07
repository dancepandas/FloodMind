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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 护栏默认值
_DEFAULT_MAX_CONCURRENT_PER_SESSION = 8
_DEFAULT_MAX_LIFETIME_SECONDS = 30 * 60  # 30 分钟兜底 kill

# 完成通知注入时的输出尾部最大长度
_NOTIFICATION_TAIL_MAX = 4000


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
    ):
        self._base_dir = Path(base_dir) if base_dir else None
        self._max_concurrent_per_session = max(max_concurrent_per_session, 1)
        self._max_lifetime_seconds = max(max_lifetime_seconds, 60)

        self._lock = threading.RLock()
        self._active_tasks: Dict[str, BackgroundTask] = {}
        self._completed: List[BackgroundTask] = []
        self._subscribers: List[Callable[[BackgroundTask], None]] = []
        self._processes: Dict[str, subprocess.Popen] = {}
        # 已收尾的任务（进过完成队列/通知过订阅者）——_finalize 幂等去重
        self._finalized: set = set()
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

        with self._lock:
            running = [t for t in self._active_tasks.values() if t.session_id == session_id and t.running]
            if len(running) >= self._max_concurrent_per_session:
                raise RuntimeError(
                    f"会话 {session_id} 后台任务已达上限 {self._max_concurrent_per_session} 个，"
                    f"请先 TaskKill 或等待完成后再启动"
                )
            task_dir = self._background_dir(session_id) / (task_id := uuid.uuid4().hex[:12])
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

        try:
            process = subprocess.Popen(shell_cmd, **kwargs)
        except Exception as e:
            out_f.close()
            err_f.close()
            raise

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
            self._active_tasks[task_id] = task
            self._processes[task_id] = process
        # 进程句柄由守护线程负责释放
        threading.Thread(
            target=self._watch,
            args=(task, process, out_f, err_f, lifetime),
            name=f"bg-task-{task_id[:8]}",
            daemon=True,
        ).start()
        logger.info("BackgroundTask started: task=%s session=%s pid=%s", task_id, session_id, process.pid)
        return task

    def get(self, session_id: str, task_id: str) -> Optional[BackgroundTask]:
        """按 task_id 查任务（活跃 + 已完成均可）。"""
        with self._lock:
            t = self._active_tasks.get(task_id)
            if t is not None:
                return t
            for t in self._completed:
                if t.task_id == task_id and t.session_id == session_id:
                    return t
        return None

    def list(self, session_id: str) -> List[BackgroundTask]:
        """列出会话全部任务（已完成在前，活跃在后）。"""
        with self._lock:
            completed = [t for t in self._completed if t.session_id == session_id]
            running = [t for t in self._active_tasks.values() if t.session_id == session_id]
            return completed + running

    def kill(self, session_id: str, task_id: str) -> bool:
        """杀掉一个任务（进程树），并立即进入完成队列/通知订阅者（Agent 感知状态变化）。"""
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
        with self._lock:
            targets = [t for t in self._active_tasks.values() if t.session_id == session_id and t.running]
            for t in targets:
                # 先标记 killed，抢在 _watch 线程覆盖前
                t.status = "killed"
                self._kill_process(self._processes.get(t.task_id))
                t.finished_at = time.time()
                self._write_meta(t)
                self._finalize(t)
        return len(targets)

    def drain_completions(self, session_id: str) -> List[BackgroundTask]:
        """取出本会话已完成的全部任务（注入完成通知用，取后清空）。"""
        with self._lock:
            drained = [t for t in self._completed if t.session_id == session_id]
            self._completed = [t for t in self._completed if t.session_id != session_id]
            return drained

    def subscribe(self, callback: Callable[[BackgroundTask], None]) -> None:
        """订阅任务完成回调（宿主 EventBus 唤醒通道）。幂等去重。"""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

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
            self._active_tasks.pop(task.task_id, None)
            self._processes.pop(task.task_id, None)
            self._completed.append(task)
            subscribers = list(self._subscribers)
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
        """读 stdout 尾部（供完成通知注入）。"""
        try:
            p = Path(path)
            if not p.exists():
                return ""
            data = p.read_text(encoding="utf-8", errors="replace")
            return data[-limit:] if len(data) > limit else data
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
        """定位 .floodmind/sessions/<sid>/background（folder-first）或兼容目录。"""
        if self._base_dir is not None:
            return self._base_dir / session_id / "background"
        state_dir = ""
        try:
            from floodmind.tools.session_context import SESSION_CONTEXT

            state_dir = SESSION_CONTEXT.get("state_dir", "") or ""
        except Exception:
            pass
        if state_dir:
            return Path(state_dir) / "sessions" / session_id / "background"
        try:
            from floodmind.agent.runtime.services.workspace_service import get_workspace

            ws = get_workspace()
            if ws is not None:
                return Path(ws.session_root) / session_id / "background"
        except Exception:
            pass
        try:
            from floodmind.tools.session_context import SESSION_CONTEXT

            out_dir = SESSION_CONTEXT.get("output_dir", "") or ""
            if out_dir:
                return Path(out_dir) / ".floodmind" / "background"
        except Exception:
            pass
        raise ValueError("缺少 workspace/state_dir，无法定位后台任务目录")


# 全局单例
_service: Optional[BackgroundTaskService] = None
_service_lock = threading.Lock()


def get_background_task_service() -> BackgroundTaskService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = BackgroundTaskService()
    return _service


def set_background_task_service(svc: Optional[BackgroundTaskService]) -> None:
    global _service
    _service = svc
