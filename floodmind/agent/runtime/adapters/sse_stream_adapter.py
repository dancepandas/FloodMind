"""Legacy compatibility alias for :mod:`event_stream_adapter`.

Kept for source-tree legacy routes; does not import Flask.
"""

from floodmind.agent.runtime.adapters.event_stream_adapter import (
    setup_ask_service_emit,
    teardown_ask_service_emit,
    validate_sse_event,
    sanitize_event_for_client,
)

__all__ = [
    "setup_ask_service_emit",
    "teardown_ask_service_emit",
    "validate_sse_event",
    "sanitize_event_for_client",
]
