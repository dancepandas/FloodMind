"""Scheduled task runtime for background Agent jobs."""

from __future__ import annotations

import json
import logging
import os
import platform
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from floodmind.common.filelock import FileLock

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS_PATH = PROJECT_ROOT / "data" / "scheduled_tasks.json"


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse_dt(value: str) -> datetime:
    text = str(value or "").strip().replace(" ", "T", 1)
    if not text:
        raise ValueError("时间不能为空")
    return datetime.fromisoformat(text)


def _parse_run_time(run_time: str) -> tuple[int, int]:
    text = str(run_time or "").strip()
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("run_time 必须使用 HH:MM 或 HH:MM:SS 格式")
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("run_time 超出有效范围")
    return hour, minute


def _next_daily_run(run_time: str, base: Optional[datetime] = None) -> datetime:
    base = base or _now()
    hour, minute = _parse_run_time(run_time)
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    return candidate


def _download_url(session_id: str, filename: str) -> str:
    return f"/api/sessions/{session_id}/outputs/{filename}"


class ScheduledTaskRuntime:
    """JSON-backed scheduled task store and state machine."""

    def __init__(self, storage_path: Optional[Path | str] = None):
        self.storage_path = Path(storage_path or os.getenv("SCHEDULED_TASKS_FILE") or DEFAULT_TASKS_PATH)
        self._lock = threading.RLock()
        # 跨进程锁：多进程宿主（桌面端 + 后台服务）并发 read-modify-write 时防丢失更新
        self._storage_lock = FileLock(self.storage_path.with_suffix(self.storage_path.suffix + ".lock"), timeout=10.0)
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_stop: Optional[threading.Event] = None
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def create_task(
        self,
        *,
        session_id: str,
        command: str,
        repeat: str = "none",
        run_time: str = "",
        scheduled_at: str = "",
        timezone: str = "Asia/Shanghai",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        command = str(command or "").strip()
        session_id = str(session_id or "default").strip() or "default"
        repeat = str(repeat or "none").strip().lower()
        if repeat not in {"none", "daily"}:
            raise ValueError("repeat 仅支持 none 或 daily")
        if not command:
            raise ValueError("command 不能为空")

        now = _now()
        if repeat == "daily":
            if not run_time:
                raise ValueError("每日重复任务必须提供 run_time")
            next_run_at = _next_daily_run(run_time, now)
            normalized_run_time = f"{_parse_run_time(run_time)[0]:02d}:{_parse_run_time(run_time)[1]:02d}"
        else:
            target = _parse_dt(scheduled_at) if scheduled_at else now
            next_run_at = target.replace(microsecond=0)
            normalized_run_time = ""

        task = {
            "id": f"sched_{uuid.uuid4().hex[:12]}",
            "session_id": session_id,
            "command": command,
            "repeat": repeat,
            "enabled": bool(enabled),
            "run_time": normalized_run_time,
            "scheduled_at": _iso(next_run_at) if repeat == "none" else "",
            "timezone": timezone or "Asia/Shanghai",
            "next_run_at": _iso(next_run_at),
            "status": "pending",
            "last_status": "",
            "last_run_at": "",
            "last_finished_at": "",
            "last_result": "",
            "last_error": "",
            "artifacts": [],
            "attempt_count": 0,
            "created_by": "agent",
            "created_at": _iso(now),
            "updated_at": _iso(now),
        }
        with self._lock, self._storage_lock:
            data = self._load_unlocked()
            data.append(task)
            self._save_unlocked(data)
        return task

    def list_tasks(self, session_id: str = "") -> List[Dict[str, Any]]:
        with self._lock:
            tasks = self._load_unlocked()
        if session_id:
            tasks = [t for t in tasks if t.get("session_id") == session_id]
        return sorted(tasks, key=lambda item: item.get("next_run_at") or item.get("created_at") or "")

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for task in self._load_unlocked():
                if task.get("id") == task_id:
                    return task
        return None

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        return self.update_task(task_id, enabled=False, status="disabled")

    def delete_task(self, task_id: str) -> Dict[str, Any]:
        with self._lock, self._storage_lock:
            data = self._load_unlocked()
            remaining = [task for task in data if task.get("id") != task_id]
            if len(remaining) == len(data):
                raise ValueError(f"定时任务不存在: {task_id}")
            deleted = next(task for task in data if task.get("id") == task_id)
            self._save_unlocked(remaining)
            return deleted

    def update_task(self, task_id: str, **updates: Any) -> Dict[str, Any]:
        allowed = {"command", "enabled", "run_time", "scheduled_at", "repeat", "status"}
        _VALID_STATUS = {"pending", "running", "completed", "failed", "disabled"}
        with self._lock, self._storage_lock:
            data = self._load_unlocked()
            for task in data:
                if task.get("id") != task_id:
                    continue
                for key, value in updates.items():
                    if key in allowed:
                        if key == "status" and str(value) not in _VALID_STATUS:
                            raise ValueError(f"无效的状态: {value}，允许: {_VALID_STATUS}")
                        task[key] = value
                if "run_time" in updates and task.get("repeat") == "daily":
                    task["next_run_at"] = _iso(_next_daily_run(str(task.get("run_time") or "")))
                if "scheduled_at" in updates and task.get("repeat") == "none":
                    task["next_run_at"] = _iso(_parse_dt(str(task.get("scheduled_at") or "")))
                task["updated_at"] = _iso(_now())
                self._save_unlocked(data)
                return task
        raise ValueError(f"定时任务不存在: {task_id}")

    def claim_due_tasks(self, *, lookback_minutes: int = 60, lookahead_minutes: int = 0, limit: int = 1) -> List[Dict[str, Any]]:
        now = _now()
        earliest = now - timedelta(minutes=max(0, int(lookback_minutes)))
        latest = now + timedelta(minutes=max(0, int(lookahead_minutes)))
        claimed: List[Dict[str, Any]] = []
        with self._lock, self._storage_lock:
            data = self._load_unlocked()
            changed = False
            for task in data:
                if len(claimed) >= limit:
                    break
                if not task.get("enabled", True) or task.get("status") == "running":
                    continue
                next_run_at = str(task.get("next_run_at") or "").strip()
                if not next_run_at:
                    continue
                try:
                    due_at = _parse_dt(next_run_at)
                except ValueError:
                    logger.warning("定时任务 next_run_at 无效: %s", task.get("id"))
                    continue
                if due_at > latest:
                    continue
                if due_at < earliest:
                    self._mark_missed(task, now)
                    changed = True
                    continue
                task["status"] = "running"
                task["last_run_at"] = _iso(now)
                # 记录认领者（P1-4）：跨进程部署时 recover 据此区分"崩溃残留"与
                # "另一进程正在合法执行长任务"，避免重复执行
                task["claimed_by"] = f"{os.getpid()}@{platform.node() or 'unknown'}"
                task["attempt_count"] = int(task.get("attempt_count") or 0) + 1
                task["updated_at"] = _iso(now)
                claimed.append(dict(task))
                changed = True
            if changed:
                self._save_unlocked(data)
        return claimed

    def complete_task(
        self,
        task_id: str,
        *,
        success: bool,
        result: str = "",
        error: str = "",
        artifacts: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        now = _now()
        with self._lock, self._storage_lock:
            data = self._load_unlocked()
            for task in data:
                if task.get("id") != task_id:
                    continue
                task["last_status"] = "completed" if success else "failed"
                task["last_finished_at"] = _iso(now)
                task["last_result"] = str(result or "")[:4000]
                task["last_error"] = str(error or "")[:4000]
                task["artifacts"] = artifacts or []
                task["updated_at"] = _iso(now)
                if task.get("status") == "disabled":
                    # 任务在运行中被取消，不覆盖状态
                    return task
                if task.get("repeat") == "daily":
                    task["status"] = "pending"
                    task["next_run_at"] = _iso(_next_daily_run(str(task.get("run_time") or ""), now))
                else:
                    task["status"] = "completed" if success else "failed"
                    task["enabled"] = False
                self._save_unlocked(data)
                return task
        raise ValueError(f"定时任务不存在: {task_id}")

    def build_artifact_records(self, session_id: str, files: List[Path], base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            if not path.is_file():
                continue
            stat = path.stat()
            filename = path.name
            if base_dir is not None:
                try:
                    filename = path.relative_to(base_dir).as_posix()
                except ValueError:
                    filename = path.name
            records.append({
                "filename": filename,
                "download_url": _download_url(session_id, filename),
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
            })
        return records

    def recover_stale_running(self, *, max_age_minutes: float = 30.0) -> List[Dict[str, Any]]:
        """把卡在 running 状态超过阈值的任务重置为 pending（D11：崩溃后任务不可恢复）。

        running 状态本应是瞬态；进程在 claim 之后 complete 之前崩溃会留下僵尸 running，
        旧实现 claim_due_tasks 永远跳过 running，daily 任务从此不再执行。

        P1-4：跨进程安全——claim 时记录了 claimed_by（pid@host）。同机且该 pid 仍
        存活时视为"另一进程正在合法执行长任务"，超过硬上限（2×阈值）才强制回收；
        pid 已死或跨机记录超阈值才立即恢复。一次性任务恢复后顺延 5 分钟，避免
        立即被再 claim 形成 fire 循环。
        """
        now = _now()
        recovered: List[Dict[str, Any]] = []
        with self._lock, self._storage_lock:
            data = self._load_unlocked()
            changed = False
            for task in data:
                if task.get("status") != "running":
                    continue
                claimed_at = str(task.get("last_run_at") or "").strip()
                age_minutes: Optional[float] = None
                if claimed_at:
                    try:
                        age_minutes = (now - _parse_dt(claimed_at)).total_seconds() / 60.0
                    except ValueError:
                        age_minutes = None
                if age_minutes is not None and age_minutes <= max_age_minutes:
                    continue  # 尚未超阈值：正常执行中
                # 超阈值（或时间戳损坏）：claimed_by 进程仍存活则再观察
                if self._claimer_alive(task) and (
                    age_minutes is not None and age_minutes <= max_age_minutes * 2
                ):
                    logger.info(
                        "定时任务 %s running 超过 %.0f 分钟但认领进程仍存活，暂不回收",
                        task.get("id"), age_minutes or 0,
                    )
                    continue
                task["status"] = "pending"
                task["last_error"] = (
                    f"检测到僵尸 running 状态（超过 {max_age_minutes:g} 分钟），已重置为 pending"
                )
                task["updated_at"] = _iso(now)
                task["claimed_by"] = ""
                if task.get("repeat") == "none":
                    # 一次性任务顺延，避免恢复后立即再 claim 形成 fire 循环
                    try:
                        base = _parse_dt(str(task.get("next_run_at") or "")) if task.get("next_run_at") else now
                    except ValueError:
                        base = now
                    next_run = max(base, now) + timedelta(minutes=5)
                    task["next_run_at"] = _iso(next_run)
                    task["scheduled_at"] = _iso(next_run)
                recovered.append(dict(task))
                changed = True
            if changed:
                self._save_unlocked(data)
        if recovered:
            logger.warning("定时任务恢复：重置 %d 个僵尸 running 任务为 pending", len(recovered))
        return recovered

    @staticmethod
    def _claimer_alive(task: Dict[str, Any]) -> bool:
        """判断 claimed_by 记录的进程是否仍存活（仅同机可判定；跨机记录视为已死）。"""
        claimed_by = str(task.get("claimed_by") or "")
        if not claimed_by or "@" not in claimed_by:
            return False
        pid_text, host = claimed_by.split("@", 1)
        if not pid_text.isdigit():
            return False
        if host and host != (platform.node() or "unknown"):
            return False  # 异机记录：无法探测，视为不存活（由超时阈值兜底）
        try:
            import psutil
            return psutil.pid_exists(int(pid_text))
        except Exception:
            return False

    def start_scheduler(
        self,
        execute_fn: "Callable[[Dict[str, Any]], Any]",
        *,
        poll_seconds: float = 30.0,
        lookback_minutes: int = 60,
        recover_stale_minutes: float = 30.0,
        on_error: "Optional[Callable[[str, BaseException], None]]" = None,
    ) -> threading.Event:
        """启动后台调度循环（D03：claim_due_tasks 此前无任何调用方，任务永不执行）。

        Args:
            execute_fn: ``task -> (success, result, error, artifacts)`` 或返回任意可忽略值。
                返回 tuple 时按结构回填 complete_task；返回 None/其他值视为成功且无产物。
            poll_seconds: 轮询间隔。
            lookback_minutes: 补跑窗口（透传 claim_due_tasks）。
            recover_stale_minutes: 僵尸 running 判定阈值。
            on_error: 单任务执行异常回调（task_id, exc），默认仅记日志。

        Returns:
            stop_event —— 调用 ``stop_event.set()`` 停止调度循环。
        """
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            raise RuntimeError("调度循环已在运行")
        stop_event = threading.Event()

        def _run() -> None:
            logger.info(
                "定时任务调度循环启动（poll=%.1fs）: %s", poll_seconds, self.storage_path
            )
            while not stop_event.is_set():
                try:
                    self.recover_stale_running(max_age_minutes=recover_stale_minutes)
                    claimed = self.claim_due_tasks(lookback_minutes=lookback_minutes, limit=1)
                    for task in claimed:
                        self._execute_claimed(task, execute_fn, on_error)
                except Exception as exc:  # 调度循环自身不退出
                    logger.exception("定时任务调度循环异常: %s", exc)
                stop_event.wait(max(1.0, float(poll_seconds)))
            logger.info("定时任务调度循环已停止")

        self._scheduler_stop = stop_event
        self._scheduler_thread = threading.Thread(
            target=_run, name="floodmind-scheduler", daemon=True
        )
        self._scheduler_thread.start()
        return stop_event

    def stop_scheduler(self, timeout: float = 5.0) -> None:
        stop_event = self._scheduler_stop
        thread = self._scheduler_thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._scheduler_stop = None
        self._scheduler_thread = None

    def _execute_claimed(self, task: Dict[str, Any], execute_fn, on_error) -> None:
        task_id = str(task.get("id") or "")
        try:
            outcome = execute_fn(dict(task))
        except Exception as exc:
            logger.exception("定时任务执行回调异常: %s", task_id)
            if on_error is not None:
                try:
                    on_error(task_id, exc)
                except Exception:
                    pass
            try:
                self.complete_task(task_id, success=False, error=f"执行回调异常: {exc}")
            except ValueError:
                pass
            return
        if isinstance(outcome, tuple):
            success = bool(outcome[0]) if len(outcome) >= 1 else True
            result = str(outcome[1]) if len(outcome) >= 2 and outcome[1] is not None else ""
            error = str(outcome[2]) if len(outcome) >= 3 and outcome[2] is not None else ""
            artifacts = outcome[3] if len(outcome) >= 4 and outcome[3] is not None else []
        else:
            success, result, error, artifacts = True, "", "", []
        try:
            self.complete_task(
                task_id,
                success=success,
                result=result,
                error=error,
                artifacts=list(artifacts or []),
            )
        except ValueError:
            # 任务可能已被并发删除
            logger.warning("定时任务回填结果失败（任务不存在或已删除）: %s", task_id)

    def _mark_missed(self, task: Dict[str, Any], now: datetime) -> None:
        task["last_status"] = "missed"
        task["last_error"] = "任务执行时间已超过补跑窗口，已跳过本次执行"
        task["updated_at"] = _iso(now)
        if task.get("repeat") == "daily":
            run_time = str(task.get("run_time") or "")
            next_run = _next_daily_run(run_time, now)
            task["next_run_at"] = _iso(next_run)
            task["status"] = "pending"
        else:
            task["status"] = "failed"
            task["enabled"] = False

    def _load_unlocked(self) -> List[Dict[str, Any]]:
        if not self.storage_path.exists():
            return []
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except Exception as exc:
            logger.warning("读取定时任务文件失败: %s", exc)
        return []

    def _save_unlocked(self, tasks: List[Dict[str, Any]]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(tasks, ensure_ascii=False, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.storage_path)


_DEFAULT_RUNTIME: Optional[ScheduledTaskRuntime] = None
_DEFAULT_RUNTIME_LOCK = threading.Lock()


def get_scheduled_task_runtime() -> ScheduledTaskRuntime:
    global _DEFAULT_RUNTIME
    with _DEFAULT_RUNTIME_LOCK:
        if _DEFAULT_RUNTIME is None:
            _DEFAULT_RUNTIME = ScheduledTaskRuntime()
        return _DEFAULT_RUNTIME
