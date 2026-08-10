"""Immutable RuntimeContext (target §11.1)."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class RuntimeContext:
    """Frozen per-run context injected into every tool/model operation.

    Service fields are typed as Any to keep this contract import-light; P2 rewires
    concrete services into these slots and removes ContextVar global getters.
    """

    conversation_id: str
    task_id: str
    run_id: str
    thread_id: str
    turn_id: str
    actor_type: str = "system"
    actor_id: str = ""
    agent_tier: str = "main"
    runtime_mode: str = "execution"
    workspace_id: str = ""
    workspace_generation: str = ""
    sandbox_id: str = ""
    permission_service: Any = None
    path_service: Any = None
    background_service: Any = None
    artifact_service: Any = None
    cancellation: Any = None
    deadline: Any = None
    environment_id: str = ""
    policy_version: str = ""
