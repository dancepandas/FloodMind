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


def test_impl_exec_bash_closes_stdin_no_hang(tmp_path):
    """P0-1：stdin 已关闭——读标准输入的命令应立即报错返回，而非挂起到超时。

    用 PowerShell $Host.UI.Prompt 模拟交互读入：stdin=DEVNULL 时立即抛错退出
    （returncode 非 0），进程数秒内返回；未关 stdin 时会挂起直到 timeout。"""
    import time

    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    set_path_service(PathService(project_root=tmp_path, workspace=ws))
    set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
    try:
        start = time.monotonic()
        result = _impl_exec_bash(command="$Host.UI.Prompt('x','y',@())", timeout=15)
        elapsed = time.monotonic() - start
    finally:
        set_session_context("", output_dir="")
        set_path_service(PathService(project_root=tmp_path))
    # 应立即返回（远小于 15s 超时），且不含"超时"字样
    assert elapsed < 12, f"读 stdin 的命令疑似挂起 {elapsed:.1f}s"
    assert "超时" not in result


def test_bash_description_declares_shell_and_stdin():
    """P0-2：Bash 描述声明 shell 类型 + stdin 已关闭。"""
    from floodmind.tools.base_tools import _bash_shell_hint, exec_bash

    hint = _bash_shell_hint()
    assert "当前 shell" in hint
    assert "stdin 已关闭" in hint
    assert "stdin 已关闭" in exec_bash.description

