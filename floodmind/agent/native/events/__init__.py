"""
Event-driven state management (Phase 3)

OpenCode-style event sourcing for Agent state:
- SessionEvent: immutable event schema
- apply_event(state, event): pure function state update
- EventProjector: persist events to SQLite
"""

from .schema import SessionEvent, EventStore
from .updater import apply_event, evolve

__all__ = ["SessionEvent", "EventStore", "apply_event", "evolve"]
