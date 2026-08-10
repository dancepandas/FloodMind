"""Approval Record 契约（目标 §6.3）。纯数据层，无 I/O。"""

from pydantic import BaseModel


class ApprovalRecord(BaseModel):
    fingerprint: str
    approver: str
    decision: str  # approved | denied
    timestamp: str = ""
    expiry: str = ""
    scope: str = "once"  # once | run | rule
    resolved_operation: str = ""
