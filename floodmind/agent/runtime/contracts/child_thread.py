"""Child Thread contract and typed subagent result (target §13)."""

from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class ChildThread(BaseModel):
    thread_id: str
    parent_thread_id: str
    parent_call_id: str
    workspace_snapshot_id: str = ""
    sandbox_id: str = ""
    tool_allowlist: List[str] = Field(default_factory=list)
    max_turns: int = 50
    max_tokens: int = 32768
    wall_clock_budget_seconds: float = 300.0


class SubagentEventType(str, Enum):
    accepted = "accepted"
    running = "running"
    result = "result"
    failed = "failed"
    cancelled = "cancelled"


class SubagentResult(BaseModel):
    thread_id: str
    parent_call_id: str
    session_id: str = ""                      # child background/sandbox namespace id
    event_type: SubagentEventType
    summary: str = ""
    artifact_ids: List[str] = Field(default_factory=list)
    tool_result_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    needs_human: bool = False
    completed: bool = False                   # semantic success for handoff
    reason: str = ""                          # failed/cancelled/quota reason
