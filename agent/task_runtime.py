"""
Task Runtime - 任务运行时

借鉴 Claude Code 的 Task.ts 设计，为 FloodAgent 提供正式的任务状态机：
- TaskStatus 枚举：pending / running / completed / failed / killed
- 合法状态流转规则
- 唯一 Task ID 生成
- 任务产物追踪
- 任务事件回调
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


VALID_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.KILLED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.KILLED: set(),
}

TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED}


class TaskType(str, Enum):
    PLAN_STEP = "plan_step"
    SCRIPT_EXECUTION = "script_execution"
    BASH_COMMAND = "bash_command"
    PYTHON_FILE = "python_file"
    FILE_WRITE = "file_write"
    KNOWLEDGE_SEARCH = "knowledge_search"
    AGENT_DELEGATION = "agent_delegation"


TASK_ID_PREFIXES = {
    TaskType.PLAN_STEP: "s",
    TaskType.SCRIPT_EXECUTION: "r",
    TaskType.BASH_COMMAND: "b",
    TaskType.PYTHON_FILE: "p",
    TaskType.KNOWLEDGE_SEARCH: "k",
    TaskType.FILE_WRITE: "w",
    TaskType.AGENT_DELEGATION: "a",
}

def generate_task_id(task_type: TaskType = TaskType.PLAN_STEP) -> str:
    prefix = TASK_ID_PREFIXES.get(task_type, "t")
    short_uuid = uuid.uuid4().hex[:12]
    return f"{prefix}{short_uuid}"


class InvalidTransitionError(Exception):
    def __init__(self, from_status: TaskStatus, to_status: TaskStatus, task_id: str = ""):
        self.from_status = from_status
        self.to_status = to_status
        self.task_id = task_id
        super().__init__(
            f"Invalid transition: {from_status.value} -> {to_status.value}"
            + (f" (task: {task_id})" if task_id else "")
        )


@dataclass
class TaskResult:
    output: str = ""
    error: str = ""
    artifacts: List[str] = field(default_factory=list)
    tool_name: str = ""
    tool_input: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    task_id: str = ""
    task_type: TaskType = TaskType.PLAN_STEP
    status: TaskStatus = TaskStatus.PENDING
    title: str = ""
    input_data: Dict[str, Any] = field(default_factory=dict)
    result: Optional[TaskResult] = None
    parent_task_id: Optional[str] = None
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    attempt_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.task_id:
            self.task_id = generate_task_id(self.task_type)
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def transition_to(self, new_status: TaskStatus) -> None:
        if new_status not in VALID_TRANSITIONS.get(self.status, set()):
            raise InvalidTransitionError(self.status, new_status, self.task_id)
        self.status = new_status
        if new_status == TaskStatus.RUNNING:
            self.started_at = datetime.now().isoformat()
            self.attempt_count += 1
        elif new_status in TERMINAL_STATUSES:
            self.finished_at = datetime.now().isoformat()

    def duration_seconds(self) -> Optional[float]:
        if not self.started_at:
            return None
        end = self.finished_at or datetime.now().isoformat()
        try:
            start_dt = datetime.fromisoformat(self.started_at)
            end_dt = datetime.fromisoformat(end)
            return (end_dt - start_dt).total_seconds()
        except (ValueError, TypeError):
            return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "status": self.status.value,
            "title": self.title,
            "input_data": self.input_data,
            "result": {
                "output": self.result.output,
                "error": self.result.error,
                "artifacts": self.result.artifacts,
                "tool_name": self.result.tool_name,
            } if self.result else None,
            "parent_task_id": self.parent_task_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "attempt_count": self.attempt_count,
            "duration_seconds": self.duration_seconds(),
        }


class TaskTracker:
    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._event_listeners: List[Callable] = []

    def create_task(
        self,
        task_type: TaskType = TaskType.PLAN_STEP,
        title: str = "",
        input_data: Optional[Dict[str, Any]] = None,
        parent_task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        task = Task(
            task_type=task_type,
            title=title,
            input_data=input_data or {},
            parent_task_id=parent_task_id,
            metadata=metadata or {},
        )
        self._tasks[task.task_id] = task
        self._emit("created", task)
        return task

    def start_task(self, task_id: str) -> Task:
        task = self._get_task(task_id)
        task.transition_to(TaskStatus.RUNNING)
        self._emit("started", task)
        return task

    def complete_task(self, task_id: str, result: Optional[TaskResult] = None) -> Task:
        task = self._get_task(task_id)
        task.result = result
        task.transition_to(TaskStatus.COMPLETED)
        self._emit("completed", task)
        return task

    def fail_task(self, task_id: str, error: str = "") -> Task:
        task = self._get_task(task_id)
        if not task.result:
            task.result = TaskResult(error=error)
        else:
            task.result.error = error
        task.transition_to(TaskStatus.FAILED)
        self._emit("failed", task)
        return task

    def kill_task(self, task_id: str, reason: str = "") -> Task:
        task = self._get_task(task_id)
        task.transition_to(TaskStatus.KILLED)
        self._emit("killed", task)
        return task

    def running_tasks(self) -> List[Task]:
        return [t for t in self._tasks.values() if t.status == TaskStatus.RUNNING]

    def add_listener(self, listener: Callable) -> None:
        self._event_listeners.append(listener)

    def summary(self) -> Dict[str, Any]:
        status_counts = {}
        for status in TaskStatus:
            status_counts[status.value] = len([t for t in self._tasks.values() if t.status == status])
        return {
            "total": len(self._tasks),
            "by_status": status_counts,
        }

    def _get_task(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")
        return task

    def _emit(self, event: str, task: Task) -> None:
        for listener in self._event_listeners:
            try:
                listener(event, task)
            except Exception as e:
                logger.warning(f"[TaskTracker] 事件监听器异常: {e}")
