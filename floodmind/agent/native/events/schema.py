"""
SessionEvent schema — immutable event types for Agent state transitions.

Every state change MUST be represented as a SessionEvent.
Events are the single source of truth; state is a projection of events.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from floodmind.agent.native.types import AgentLoopState, ExecutionPlan


EventType = Literal[
    "step.started",       # LLM inference begins
    "step.ended",         # LLM inference ends (with usage)
    "step.failed",        # LLM inference error
    "tool.called",        # Tool call requested by LLM
    "tool.result",        # Tool executed successfully
    "tool.error",         # Tool execution failed
    "plan.created",       # Execution plan created
    "plan.step.updated",  # Step status changed
    "compaction.done",    # Context compression completed
    "agent.role_changed", # Agent role switched (plan -> build)
    "todo.updated",       # Todo list changed
]


@dataclass(frozen=True)
class SessionEvent:
    """Immutable event representing a single state transition."""

    type: EventType
    session_id: str
    timestamp: float
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = ""  # Optional UUID for idempotency

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
            "event_id": self.event_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionEvent":
        return cls(
            type=data["type"],
            session_id=data["session_id"],
            timestamp=data["timestamp"],
            payload=data.get("payload", {}),
            event_id=data.get("event_id", ""),
        )


class EventStore:
    """Append-only event log backed by SQLite (via session_store)."""

    def __init__(self, session_store=None):
        self._session_store = session_store
        self._buffer: List[SessionEvent] = []
        self._max_buffer = 50

    def append(self, event: SessionEvent) -> None:
        """Append event to buffer; flush when buffer is full."""
        self._buffer.append(event)
        if len(self._buffer) >= self._max_buffer:
            self.flush()

    def flush(self) -> None:
        """Persist buffered events to SQLite."""
        if not self._buffer or not self._session_store:
            return
        try:
            events_json = [e.to_dict() for e in self._buffer]
            self._session_store.append_events(events_json)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("EventStore flush failed: %s", e)
        finally:
            self._buffer.clear()

    def get_events(self, session_id: str, after_timestamp: float = 0.0) -> List[SessionEvent]:
        """Read events for a session (from buffer + SQLite)."""
        results: List[SessionEvent] = []
        # From buffer
        for e in self._buffer:
            if e.session_id == session_id and e.timestamp > after_timestamp:
                results.append(e)
        # From SQLite
        if self._session_store:
            try:
                raw_events = self._session_store.get_events(session_id, after_timestamp)
                for raw in raw_events:
                    results.append(SessionEvent.from_dict(raw))
            except Exception:
                pass
        return sorted(results, key=lambda e: e.timestamp)
