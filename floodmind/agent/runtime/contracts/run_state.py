"""Reducer 派生状态契约（目标 §5.1）。纯数据层，无 I/O。"""

from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from floodmind.agent.runtime.contracts.tool_transaction import ToolTransaction


class RunStatus(str, Enum):
    created = "created"
    projecting_context = "projecting_context"
    awaiting_model = "awaiting_model"
    streaming_model = "streaming_model"
    awaiting_tool = "awaiting_tool"
    awaiting_approval = "awaiting_approval"
    executing_tool = "executing_tool"
    compacting = "compacting"
    paused = "paused"
    cancelling = "cancelling"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"


class PendingApproval(BaseModel):
    ask_id: str
    call_id: str
    tool_name: str
    reason: str = ""


class ChildThreadState(BaseModel):
    thread_id: str
    parent_call_id: str = ""
    status: str = "running"


class RunState(BaseModel):
    run_id: str
    conversation_id: str = ""
    task_id: str = ""
    status: RunStatus = RunStatus.created
    current_thread_id: str = ""
    current_turn_id: str = ""
    active_attempt_id: str = ""
    last_committed_sequence: int = 0
    pending_tool_transactions: List[ToolTransaction] = Field(default_factory=list)
    pending_approvals: List[PendingApproval] = Field(default_factory=list)
    active_background_tasks: List[str] = Field(default_factory=list)
    child_threads: List[ChildThreadState] = Field(default_factory=list)
    artifacts: List[str] = Field(default_factory=list)
    token_usage: Dict[str, int] = Field(default_factory=dict)
    processed_event_ids: List[str] = Field(default_factory=list)
    cancellation_state: str = ""
    resumability: str = ""
    # 派生对话历史：扁平 user/assistant 条目，与现 DualMemory._turns 形状 wire 兼容
    turns: List[Dict[str, Any]] = Field(default_factory=list)
