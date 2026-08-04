"""Legacy compatibility alias for :mod:`permission_api`.

Kept for source-tree legacy routes; does not import Flask.
"""

from floodmind.agent.runtime.adapters.permission_api import (
    handle_permission_respond,
    handle_permission_pending,
    handle_permission_cancel_session,
)

__all__ = [
    "handle_permission_respond",
    "handle_permission_pending",
    "handle_permission_cancel_session",
]
