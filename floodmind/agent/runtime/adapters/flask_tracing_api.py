"""Legacy compatibility alias for :mod:`tracing_api`.

Kept for source-tree legacy routes; does not import Flask.
"""

from floodmind.agent.runtime.adapters.tracing_api import (
    handle_list_trace_events,
    handle_get_trace_file_path,
)

__all__ = [
    "handle_list_trace_events",
    "handle_get_trace_file_path",
]
