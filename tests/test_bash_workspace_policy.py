from floodmind.agent.runtime.contracts.permissions import PermissionBehavior, ToolPermissionPolicy
from floodmind.agent.runtime.services.path_service import PathService, set_path_service
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.workspace_service import build_folder_workspace
from floodmind.tools.base_tools import _impl_exec_bash
from floodmind.tools.session_context import set_session_context


def test_exec_policy_asks_for_mutating_redirection():
    decision = PermissionService().check_tool_policy(
        ToolPermissionPolicy(policy_type="exec", command_field="command"),
        {"command": "echo hi > out.txt"},
        tool_name="Bash",
        session_id="s1",
    )
    assert decision.behavior == PermissionBehavior.ASK
    assert "文件副作用" in decision.reason


def test_exec_policy_denies_external_workdir(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    outside = tmp_path / "outside"
    outside.mkdir()
    perm = PermissionService(path_service=PathService(project_root=tmp_path, workspace=ws))
    decision = perm.check_tool_policy(
        ToolPermissionPolicy(policy_type="exec", command_field="command", path_fields=["workdir"]),
        {"command": "python --version", "workdir": str(outside)},
        tool_name="Bash",
        session_id="s1",
    )
    assert decision.behavior == PermissionBehavior.DENY


def test_impl_exec_bash_rejects_external_workdir(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    outside = tmp_path / "outside"
    outside.mkdir()
    set_path_service(PathService(project_root=tmp_path, workspace=ws))
    set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
    try:
        result = _impl_exec_bash(command="python --version", workdir=str(outside), timeout=5)
    finally:
        set_session_context("", output_dir="")
        set_path_service(PathService(project_root=tmp_path))

    assert "错误" in result
    assert "不在允许目录" in result or "不允许" in result


def test_impl_exec_bash_requires_cwd_without_context(tmp_path):
    set_session_context("", output_dir="")
    set_path_service(PathService(project_root=tmp_path, workspace=None))
    result = _impl_exec_bash(command="python --version", timeout=5)
    assert "缺少 workspace cwd" in result
