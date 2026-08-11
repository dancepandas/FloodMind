import hashlib

from floodmind.agent.runtime.services.approval_fingerprint import (
    compute_approval_fingerprint, fingerprint_matches,
)
from floodmind.agent.runtime.contracts.approval import ApprovalRecord
from floodmind.agent.runtime.contracts.tool_transaction import canonical_arguments


def _fp(**kw):
    base = dict(
        tool_id="builtin:Write", tool_version="1", canonical_arguments=canonical_arguments({"path": "/a"}),
        resolved_targets=["/a"], cwd="/work", environment_identity="env-1", workspace_id="ws_1",
        workspace_generation="gen-1", sandbox_permissions=[], agent_tier="main", runtime_mode="execution",
        side_effect_class="reversible_write", policy_version="v1",
    )
    base.update(kw)
    return compute_approval_fingerprint(**base)


def test_fingerprint_deterministic_and_sensitive():
    f1 = _fp()
    assert f1 == _fp()
    assert f1 == hashlib.sha256(
        "|".join([
            "builtin:Write", "1", canonical_arguments({"path": "/a"}), "['/a']", "/work",
            "env-1", "ws_1", "gen-1", "[]", "main", "execution", "reversible_write", "v1",
        ]).encode("utf-8")
    ).hexdigest()


def test_param_path_workspace_policy_change_invalidates():
    f_base = _fp()
    assert _fp(canonical_arguments=canonical_arguments({"path": "/b"})) != f_base  # 参数变
    assert _fp(resolved_targets=["/b"]) != f_base                               # 目标路径变
    assert _fp(workspace_generation="gen-2") != f_base                          # workspace 代变
    assert _fp(policy_version="v2") != f_base                                   # policy 变
    assert _fp(agent_tier="sub") != f_base                                      # tier 变


def test_fingerprint_matches_approval_record():
    rec = ApprovalRecord(fingerprint=_fp(), approver="host", decision="approved")
    assert fingerprint_matches(rec, _fp())
    assert not fingerprint_matches(rec, _fp(policy_version="v2"))
