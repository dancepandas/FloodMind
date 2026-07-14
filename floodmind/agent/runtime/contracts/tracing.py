"""
Tracing contracts for the Agent Harness.

Provides Pydantic models for TraceSpan (duration-bound operations) and
TraceEvent (instantaneous observations). Both are serializable to JSONL.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

TraceType = Literal[
    "llm",
    "tool",
    "permission",
    "state_transition",
    "checkpoint",
    "workflow",
    "other",
]

TraceStatus = Literal["ok", "error", "in_progress"]


def _new_span_id() -> str:
    return f"span-{uuid.uuid4().hex[:16]}"


def _new_event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:16]}"


class TraceSpan(BaseModel):
    """A duration-bound operation: LLM call, tool execution, permission wait, etc."""

    model_config = ConfigDict(extra="allow")

    span_id: str = Field(default_factory=_new_span_id)
    parent_id: Optional[str] = None
    trace_id: str = ""
    type: TraceType = "other"
    name: str = ""
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    duration_ms: Optional[float] = None
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Dict[str, Any] = Field(default_factory=dict)
    status: TraceStatus = "in_progress"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def finalize(
        self,
        output: Optional[Dict[str, Any]] = None,
        status: TraceStatus = "ok",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Finalize the span with an end timestamp and status."""
        self.end_time = datetime.now(timezone.utc)
        delta = self.end_time - self.start_time
        self.duration_ms = round(delta.total_seconds() * 1000, 3)
        if output is not None:
            self.output = output
        self.status = status
        if metadata:
            self.metadata.update(metadata)


class TraceEvent(BaseModel):
    """An instantaneous observation: token usage, state transition, checkpoint save, etc."""

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(default_factory=_new_event_id)
    trace_id: str = ""
    type: TraceType = "other"
    name: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Dict[str, Any] = Field(default_factory=dict)
    status: TraceStatus = "ok"
    metadata: Dict[str, Any] = Field(default_factory=dict)
