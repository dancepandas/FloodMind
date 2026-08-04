"""
Runtime Adapters — transport-neutral exports.
"""

from floodmind.agent.runtime.adapters.permission_api import (
    handle_permission_respond,
    handle_permission_pending,
    handle_permission_cancel_session,
)
from floodmind.agent.runtime.adapters.event_stream_adapter import (
    setup_ask_service_emit,
    teardown_ask_service_emit,
    validate_sse_event,
    sanitize_event_for_client,
)

__all__ = [
    "handle_permission_respond",
    "handle_permission_pending",
    "handle_permission_cancel_session",
    "setup_ask_service_emit",
    "teardown_ask_service_emit",
    "validate_sse_event",
    "sanitize_event_for_client",
]
