"""Approval Fingerprint（目标 §6.3）。纯函数，无 I/O/时间/随机。"""

import hashlib
from typing import List

from floodmind.agent.runtime.contracts.approval import ApprovalRecord


def compute_approval_fingerprint(
    *,
    tool_id: str,
    tool_version: str,
    canonical_arguments: str,
    resolved_targets: List[str],
    cwd: str,
    environment_identity: str,
    workspace_id: str,
    workspace_generation: str,
    sandbox_permissions: List[str],
    agent_tier: str,
    runtime_mode: str,
    side_effect_class: str,
    policy_version: str,
) -> str:
    """§6.3：指纹元组按序定界连接后 SHA256。参数变化即失效。"""
    parts = [
        tool_id, tool_version, canonical_arguments, repr(list(resolved_targets)),
        cwd, environment_identity, workspace_id, workspace_generation,
        repr(list(sandbox_permissions)), agent_tier, runtime_mode,
        side_effect_class, policy_version,
    ]
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fingerprint_matches(record: ApprovalRecord, current: str) -> bool:
    return bool(record.fingerprint) and record.fingerprint == current
