"""Tool Transaction contract (target §6)."""

import hashlib
import json
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from floodmind.agent.runtime.contracts.canonical_events import canonical_json


def canonical_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(canonical_json(arguments))


def arguments_sha256(tool_id: str, tool_version: str, arguments: Dict[str, Any]) -> str:
    digest = canonical_json({
        "tool_id": tool_id,
        "tool_version": tool_version,
        "arguments": canonical_arguments(arguments),
    })
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()


def compute_fingerprint(parts: Dict[str, str]) -> str:
    digest = canonical_json(parts)
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()


class SideEffectClass(str, Enum):
    read = "read"
    reversible_write = "reversible_write"
    irreversible = "irreversible"
    external = "external"


class ToolStatus(str, Enum):
    proposed = "proposed"
    validated = "validated"
    approval_required = "approval_required"
    approved = "approved"
    denied = "denied"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    indeterminate = "indeterminate"


class ToolTransaction(BaseModel):
    transaction_id: str
    call_id: str
    tool_id: str
    tool_version: str = "1"
    canonical_arguments: Dict[str, Any] = Field(default_factory=dict)
    arguments_sha256: str = ""
    workspace_fingerprint: str = ""
    runtime_fingerprint: str = ""
    permission_fingerprint: str = ""
    idempotency_key: str = ""
    side_effect_class: SideEffectClass = SideEffectClass.read
    status: ToolStatus = ToolStatus.proposed
    preconditions: List[str] = Field(default_factory=list)
    result_ref: str = ""
