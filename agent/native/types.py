"""
Native Agent Runtime 核心数据结构

LangChain-free 的 Agent 执行层数据类型定义。
ToolSpec / ToolCall / ToolResult 统一由 agent.runtime.contracts.tools 定义，
此模块 re-export 保持向后兼容。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional

from agent.runtime.contracts.tools import ToolCall, ToolResult, ToolSpec


@dataclass
class Attachment:
    file_id: str
    name: str
    path: str
    kind: Literal["image", "document"]
    mime_type: str
    size: int


@dataclass
class RunContext:
    session_id: str
    user_text: str
    attachments: List[Attachment] = field(default_factory=list)
    output_dir: str = ""
    upload_dir: str = ""
    model_key: str = ""
    enable_reasoning: bool = False
    enable_search: bool = False
    enable_rag: bool = False
    abort_check: Optional[Callable[[], bool]] = None


@dataclass
class ModelEvent:
    type: Literal[
        "reasoning",
        "token",
        "tool_call_delta",
        "tool_call_done",
        "done",
        "usage",
        "error",
        "timeout",
    ]
    content: str = ""
    tool_call: Optional[ToolCall] = None
    raw: Optional[dict] = None


@dataclass
class AgentResult:
    final_output: str
    reasoning: str
    tool_results: List[ToolResult] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    is_timeout: bool = False


@dataclass
class PlanStep:
    id: str
    title: str
    detail: str = ""
    status: Literal["pending", "running", "completed", "error", "skipped"] = "pending"
    tool_name: str = ""
    artifact_ids: List[str] = field(default_factory=list)
    error: str = ""


@dataclass
class AgentLoopState:
    run_id: str
    iteration: int = 0
    plan: Optional['ExecutionPlan'] = None
    current_step_id: str = ""
    completed_steps: List[str] = field(default_factory=list)
    failed_steps: List[str] = field(default_factory=list)
    tool_results: List[ToolResult] = field(default_factory=list)
    artifact_registry: Dict[str, dict] = field(default_factory=dict)
    execution_journal: List[dict] = field(default_factory=list)
    needs_replan: bool = False
    validation_errors: List[str] = field(default_factory=list)
    original_input: str = ""
    user_message: str = ""
    final_output: str = ""
    latest_payload: Optional[Dict[str, Any]] = None
    artifacts: List[str] = field(default_factory=list)
    round_count: int = 0
    replan_count: int = 0
    terminal_status: str = "running"


@dataclass
class ArtifactRecord:
    file_name: str
    file_path: str
    kind: Literal["file", "image"]
    source_tool: str
    verified: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    plan_id: str = ""
    user_message: str = ""
    goal_deliverables: List[Dict[str, str]] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    terminal_status: str = "running"

    def find_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        for s in self.steps:
            if s.get("step_id") == step_id:
                return s
        return None

    def next_pending_step(self) -> Optional[Dict[str, Any]]:
        for s in self.steps:
            if s.get("status") == "pending":
                return s
        return None

    def all_steps_completed(self) -> bool:
        return all(s.get("status") == "completed" for s in self.steps) if self.steps else False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "user_message": self.user_message,
            "goal_deliverables": self.goal_deliverables,
            "steps": self.steps,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "terminal_status": self.terminal_status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionPlan":
        return cls(
            plan_id=data.get("plan_id", ""),
            user_message=data.get("user_message", ""),
            goal_deliverables=data.get("goal_deliverables", []),
            steps=data.get("steps", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            terminal_status=data.get("terminal_status", "running"),
        )
