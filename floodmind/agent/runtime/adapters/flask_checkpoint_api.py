"""Legacy compatibility alias for :mod:`checkpoint_api`.

Kept for source-tree legacy routes; does not import Flask.
"""

from floodmind.agent.runtime.adapters.checkpoint_api import (
    handle_list_checkpoints,
    handle_get_checkpoint_manifest,
    handle_rollback_checkpoint,
)

__all__ = [
    "handle_list_checkpoints",
    "handle_get_checkpoint_manifest",
    "handle_rollback_checkpoint",
]
