"""P7 Task 5 — child permission/path isolation (strict subset)."""
from pathlib import Path

from floodmind.agent.runtime.services.child_permission_context import (
    build_child_permission_context,
)
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior, PermissionRequest,
)
from floodmind.agent.runtime.contracts.paths import PathResolveRequest


def _parent_services(tmp_path):
    parent_ws = PathService(project_root=tmp_path)
    parent_perm = PermissionService(ask_service=None, path_service=parent_ws)
    parent_perm.add_allow_rule(_rule("allow_data", r".*", PermissionBehavior.ALLOW))
    return parent_ws, parent_perm


def _rule(name, pattern, behavior):
    from floodmind.agent.runtime.contracts.permissions import PermissionRule
    return PermissionRule(name=name, pattern=pattern, behavior=behavior)


def test_child_path_service_confined_to_child_workspace(tmp_path):
    parent_ws, parent_perm = _parent_services(tmp_path)
    child_ws_dir = tmp_path / "child-workspace"
    child_ws_dir.mkdir()
    child_perm, child_path = build_child_permission_context(
        parent_path_service=parent_ws,
        parent_permission_service=parent_perm,
        child_workspace=child_ws_dir,
        child_session_id="sub-1",
    )
    # 相对写落在 child workspace 内
    res = child_path.resolve(PathResolveRequest(raw_path="out.txt", access="write", session_id="sub-1"))
    assert res.allowed
    assert Path(res.resolved_path).is_relative_to(child_ws_dir)
    # child workspace 外写被拒（父的 data/ 写根对子不可见）
    outside = child_path.resolve(PathResolveRequest(raw_path=str(tmp_path / "data" / "x.txt"), access="write", session_id="sub-1"))
    assert not outside.allowed


def test_child_cannot_escalate_no_ask_no_network(tmp_path):
    parent_ws, parent_perm = _parent_services(tmp_path)
    child_ws_dir = tmp_path / "child-workspace"
    child_ws_dir.mkdir()
    child_perm, child_path = build_child_permission_context(
        parent_path_service=parent_ws,
        parent_permission_service=parent_perm,
        child_workspace=child_ws_dir,
        child_session_id="sub-1",
    )
    auth = None  # 不触发 ASK 的路径断言用
    # 子代理 network 类工具（policy_type="network"）被 tier 层硬拒
    from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
    req = PermissionRequest(
        tool_name="mcp:srv:web", session_id="sub-1", agent_tier="sub",
        permission_policy=ToolPermissionPolicy(policy_type="network"),
        tool_input={}, call_id="c1",
    )
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    dec = child_perm.check(req, journal_authority=auth)
    assert dec.behavior == PermissionBehavior.DENY
    # 子代理 ASK 降级 DENY（无法向用户确认）：含写目标的命令触发 ASK，tier 层硬拒。
    # 注意：普通 "echo hi" 会得到 ALLOW（非 ASK），不会命中 tier 降级分支。
    ask_req = PermissionRequest(
        tool_name="Bash", session_id="sub-1", agent_tier="sub",
        permission_policy=ToolPermissionPolicy(policy_type="exec"),
        tool_input={"command": "echo hi > /tmp/out.txt"}, call_id="c2",
    )
    dec2 = child_perm.check(ask_req, journal_authority=auth)
    assert dec2.behavior == PermissionBehavior.DENY
