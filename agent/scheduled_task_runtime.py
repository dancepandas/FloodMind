"""Scheduled task runtime for background Agent jobs."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        with self._lock:
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
        with self._lock:
            data = self._load_unlocked()
            remaining = [task for task in data if task.get("id") != task_id]
            if len(remaining) == len(data):
                raise ValueError(f"定时任务不存在: {task_id}")
            deleted = next(task for task in data if task.get("id") == task_id)
            self._save_unlocked(remaining)
            return deleted

    def update_task(self, task_id: str, **updates: Any) -> Dict[str, Any]:
        allowed = {"command", "enabled", "run_time", "scheduled_at", "repeat", "status"}
        with self._lock:
            data = self._load_unlocked()
            for task in data:
                if task.get("id") != task_id:
                    continue
                for key, value in updates.items():
                    if key in allowed:
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
        with self._lock:
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
        with self._lock:
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
        tmp_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.storage_path)


_DEFAULT_RUNTIME: Optional[ScheduledTaskRuntime] = None
_DEFAULT_RUNTIME_LOCK = threading.Lock()


def get_scheduled_task_runtime() -> ScheduledTaskRuntime:
    global _DEFAULT_RUNTIME
    with _DEFAULT_RUNTIME_LOCK:
        if _DEFAULT_RUNTIME is None:
            _DEFAULT_RUNTIME = ScheduledTaskRuntime()
        return _DEFAULT_RUNTIME
