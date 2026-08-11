"""SDK 公共事件契约（§4.4/§10.1 双层流，Journal 派生）。

Preview 可丢失、可被 Committed 对账；每个 Preview 携带 attempt_id/part_id。
Committed 事件携带 canonical sequence，客户端可用 after_sequence 重放/对账。
"""
from typing import Any, Dict, List, Optional, TypedDict


class SdkEvent(TypedDict, total=False):
    type: str
    sequence: int
    event_type: str
    payload: Dict[str, Any]
    attempt_id: str
    part_id: str


_CANONICAL_TO_SDK = {
    "model.attempt.completed": "text_committed",
    "model.attempt.failed": "model_failed",
    "tool.result.committed": "tool_result",
    "tool.call.proposed": "tool_call",
    "artifact.committed": "artifact",
    "background.completed": "background_completed",
    "run.completed": "run_completed",
    "run.failed": "run_failed",
}
_SKIP_EVENT_TYPES = frozenset({
    "model.attempt.started", "model.request.committed", "model.usage.recorded",
    "model.stream.preview", "checkpoint.created", "context.projection.started",
    "context.projection.committed", "thread.message.sent",
})


def project_canonical(envelope) -> Optional[SdkEvent]:
    """把一条 canonical EventEnvelope 投影为公共 SdkEvent；内部事件返回 None。"""
    event_type = envelope.event_type
    if event_type in _SKIP_EVENT_TYPES:
        return None
    sdk_type = _CANONICAL_TO_SDK.get(event_type)
    if sdk_type is None:
        return None
    payload = dict(envelope.payload or {})
    if event_type == "model.attempt.completed" and not payload.get("is_final"):
        return None
    event: SdkEvent = {
        "type": sdk_type,
        "sequence": envelope.sequence,
        "event_type": event_type,
        "payload": payload,
    }
    if getattr(envelope, "attempt_id", ""):
        event["attempt_id"] = envelope.attempt_id
    return event


def project_canonical_many(events) -> List[SdkEvent]:
    """按 canonical 顺序投影多条事件，并滤除非公共事件。"""
    projected: List[SdkEvent] = []
    for envelope in events:
        event = project_canonical(envelope)
        if event is not None:
            projected.append(event)
    return projected
