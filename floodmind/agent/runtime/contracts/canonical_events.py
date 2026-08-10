"""Canonical Event Envelope and taxonomy (target §4)."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Literal
from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def canonical_payload_sha256(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class Actor(BaseModel):
    type: Literal["user", "model", "tool", "host", "system", "subagent"] = "system"
    id: str = ""


class Provenance(BaseModel):
    source: Literal["runtime", "provider", "host", "migration"] = "runtime"
    provider: str = ""
    request_id: str = ""
    code_version: str = ""
    codec_version: str = ""


class EventIntegrity(BaseModel):
    payload_sha256: str = ""
    previous_event_sha256: str = ""
    event_sha256: str = ""


class EventEnvelope(BaseModel):
    schema_version: str = "1.0"
    event_id: str
    event_type: str
    sequence: int
    recorded_at: datetime = Field(default_factory=utcnow)

    conversation_id: str = ""
    task_id: str = ""
    run_id: str = ""
    thread_id: str = ""
    turn_id: str = ""
    attempt_id: str = ""
    call_id: str = ""

    causation_id: str = ""
    correlation_id: str = ""

    actor: Actor = Field(default_factory=Actor)
    payload: Dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
    integrity: EventIntegrity = Field(default_factory=EventIntegrity)


_EVENT_TYPES: Dict[str, frozenset[str]] = {
    "run": frozenset({
        "run.created", "run.started", "run.pause.requested", "run.paused",
        "run.cancel.requested", "run.cancel.completed", "run.completed", "run.failed",
    }),
    "context": frozenset({
        "context.projection.started", "context.source.selected",
        "context.source.omitted", "context.compaction.started",
        "context.compaction.completed", "context.projection.committed",
    }),
    "model": frozenset({
        "model.attempt.started", "model.request.committed", "model.stream.preview",
        "model.block.completed", "model.usage.recorded", "model.attempt.completed",
        "model.attempt.failed", "model.retry.scheduled", "model.continuation.requested",
    }),
    "tool": frozenset({
        "tool.call.proposed", "tool.call.validated", "tool.permission.evaluated",
        "tool.approval.requested", "tool.approval.resolved", "tool.execution.started",
        "tool.execution.completed", "tool.execution.failed", "tool.execution.cancelled",
        "tool.execution.indeterminate", "tool.result.committed",
    }),
    "thread": frozenset({
        "thread.spawn.requested", "thread.created", "thread.message.sent",
        "thread.completed", "thread.failed", "thread.cancelled",
    }),
    "artifact": frozenset({
        "artifact.declared", "artifact.committed", "artifact.verified",
        "artifact.superseded",
    }),
    "checkpoint": frozenset({"checkpoint.created", "resume.started",
                             "resume.reconciliation.required", "resume.completed"}),
    "security": frozenset({"security.violation", "privacy.redaction.requested",
                           "privacy.redaction.completed"}),
}

EVENT_TYPES: frozenset[str] = frozenset(
    t for types in _EVENT_TYPES.values() for t in types
)
