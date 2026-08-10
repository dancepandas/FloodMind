"""Tool Transaction 契约（目标 §6.1/§6.2/§6.4）。纯数据层，无 I/O。"""

import hashlib
from enum import Enum
from typing import List

from pydantic import BaseModel

from floodmind.agent.runtime.contracts.canonical_events import canonical_json


class SideEffectClass(str, Enum):
    read = "read"
    reversible_write = "reversible_write"
    irreversible = "irreversible"
    external = "external"


class ToolStatus(str, Enum):
    proposed = "proposed"
    validated = "validated"
    permission_evaluated = "permission_evaluated"
    approval_required = "approval_required"
    approved = "approved"
    denied = "denied"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    indeterminate = "indeterminate"
    result_committed = "result_committed"


_LEGAL_TRANSITIONS = {
    ToolStatus.proposed: {ToolStatus.validated, ToolStatus.denied},
    ToolStatus.validated: {ToolStatus.permission_evaluated},
    ToolStatus.permission_evaluated: {ToolStatus.approval_required, ToolStatus.approved, ToolStatus.denied},
    ToolStatus.approval_required: {ToolStatus.approved, ToolStatus.denied},
    ToolStatus.approved: {ToolStatus.running, ToolStatus.cancelled, ToolStatus.failed},
    ToolStatus.running: {ToolStatus.succeeded, ToolStatus.failed, ToolStatus.cancelled, ToolStatus.indeterminate},
    ToolStatus.succeeded: {ToolStatus.result_committed},
    ToolStatus.indeterminate: {ToolStatus.result_committed, ToolStatus.succeeded, ToolStatus.failed},
}
# 终态：无出边
_TERMINAL = {ToolStatus.denied, ToolStatus.failed, ToolStatus.cancelled, ToolStatus.result_committed}


class ToolTransaction(BaseModel):
    transaction_id: str
    call_id: str
    tool_id: str
    tool_version: str = "1"
    canonical_arguments: str = ""
    arguments_sha256: str = ""
    workspace_fingerprint: str = ""
    runtime_fingerprint: str = ""
    permission_fingerprint: str = ""
    idempotency_key: str = ""
    side_effect_class: SideEffectClass = SideEffectClass.read
    status: ToolStatus = ToolStatus.proposed
    preconditions: List[str] = []
    result_ref: str = ""

    def transition(self, to: ToolStatus) -> "ToolTransaction":
        allowed = _LEGAL_TRANSITIONS.get(self.status, frozenset())
        if self.status in _TERMINAL or to not in allowed:
            raise ValueError(f"非法工具状态转移: {self.status.value} -> {to.value}")
        return self.model_copy(update={"status": to})


def canonical_arguments(arguments: dict) -> str:
    return canonical_json(arguments or {})


def arguments_sha256(tool_id: str, tool_version: str, canonical_arguments: str) -> str:
    raw = (tool_id + tool_version + canonical_arguments).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
