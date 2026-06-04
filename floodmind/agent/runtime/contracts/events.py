"""
Runtime Contracts — SSE 流事件协议模型

所有事件类型集中定义，后端只发这些事件，前端只消费这些事件。
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class StreamEvent(BaseModel):
    type: str
    session_id: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None and v != ""}


class StreamStartEvent(StreamEvent):
    type: Literal["stream_start"] = "stream_start"


class ThoughtDeltaEvent(StreamEvent):
    type: Literal["thought_delta"] = "thought_delta"
    content: str = ""


class AnswerDeltaEvent(StreamEvent):
    type: Literal["answer_delta"] = "answer_delta"
    content: str = ""


class ActionStartEvent(StreamEvent):
    type: Literal["action_start"] = "action_start"
    call_id: str = ""
    tool_name: str = ""
    status: str = "running"
    tool_input: str = ""


class PermissionAskEvent(StreamEvent):
    type: Literal["permission_ask"] = "permission_ask"
    call_id: str = ""
    ask_id: str = ""
    tool_name: str = ""
    reason: str = ""
    tool_input: Dict[str, Any] = {}


class PermissionResolvedEvent(StreamEvent):
    type: Literal["permission_resolved"] = "permission_resolved"
    call_id: str = ""
    ask_id: str = ""
    approved: bool = False


class ActionEndEvent(StreamEvent):
    type: Literal["action_end"] = "action_end"
    call_id: str = ""
    tool_name: str = ""
    status: str = "completed"
    content: str = ""


class ArtifactAddedEvent(StreamEvent):
    type: Literal["artifact_added"] = "artifact_added"
    filename: str = ""
    download_url: str = ""
    image_url: str = ""
    size: int = 0


class FinalEvent(StreamEvent):
    type: Literal["final"] = "final"
    content: str = ""
    artifacts: List[Dict[str, Any]] = []


class ErrorEvent(StreamEvent):
    type: Literal["error"] = "error"
    content: str = ""


class StreamEndEvent(StreamEvent):
    type: Literal["stream_end"] = "stream_end"


class HeartbeatEvent(StreamEvent):
    type: Literal["heartbeat"] = "heartbeat"


VALID_EVENT_TYPES = {
    "stream_start", "thought_delta", "answer_delta",
    "action_start", "permission_ask", "permission_resolved",
    "action_end", "artifact_added", "final",
    "error", "stream_end", "heartbeat",
    "workflow_plan", "workflow_step",
    "memory_status", "artifact_warning",
}
