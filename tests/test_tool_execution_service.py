"""Tests for ToolExecutionService dangerous command enforcement."""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.types import RunContext
from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
from floodmind.agent.runtime.contracts.tools import ToolCall
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
from floodmind.tools.session_context import SESSION_CONTEXT


def _make_exec_tool():
    tool = MagicMock()
    tool.name = "TestExec"
    tool.permission_policy = ToolPermissionPolicy(policy_type="exec", command_field="command")
    tool.validate_input.return_value = MagicMock(valid=True)
    tool.args_schema = None
    tool.func = lambda command: f"ran {command}"
    return tool


class TestToolExecutionServiceDangerousCommand:
    def test_exec_policy_denies_dangerous_command(self):
        perm_svc = PermissionService()
        svc = ToolExecutionService(permission_service=perm_svc)

        reg = MagicMock()
        reg.get.return_value = _make_exec_tool()

        ctx = RunContext(session_id="s1", user_text="test", output_dir="/tmp/out", upload_dir="/tmp/up")
        call = ToolCall(id="c1", name="TestExec", arguments={"command": "rm -rf /tmp/important"})

        result = svc.execute(call, context=ctx, registry=reg)

        assert result.status == "error"
        assert "危险" in result.content or "PERMISSION_DENIED" in result.content

    def test_exec_policy_allows_safe_command(self):
        perm_svc = PermissionService()
        svc = ToolExecutionService(permission_service=perm_svc)

        reg = MagicMock()
        reg.get.return_value = _make_exec_tool()

        ctx = RunContext(session_id="s1", user_text="test", output_dir="/tmp/out", upload_dir="/tmp/up")
        call = ToolCall(id="c1", name="TestExec", arguments={"command": "python script.py"})

        result = svc.execute(call, context=ctx, registry=reg)

        assert result.status == "completed"
        assert "ran python script.py" in result.content


class TestToolExecutionContextInjection:
    def test_injects_cwd_and_workspace_fields(self, tmp_path):
        def _set_context(*args, **kwargs):
            from floodmind.tools.session_context import set_session_context
            set_session_context(*args, **kwargs)

        svc = ToolExecutionService(set_session_context_fn=_set_context)
        reg = MagicMock()
        tool = MagicMock()
        tool.name = "CtxTool"
        tool.permission_policy = None
        tool.check_permissions.return_value = True
        tool.validate_input.return_value = MagicMock(valid=True)
        tool.args_schema = None

        def _func():
            return f"cwd={SESSION_CONTEXT.get('cwd')} workspace={SESSION_CONTEXT.get('workspace_dir')}"

        tool.func = _func
        reg.get.return_value = tool

        ctx = RunContext(
            session_id="s1",
            user_text="test",
            output_dir=str(tmp_path / "out"),
            upload_dir=str(tmp_path / "up"),
            cwd=str(tmp_path / "project"),
            workspace_dir=str(tmp_path / "project"),
            artifact_dir=str(tmp_path / "project" / ".floodmind" / "artifacts" / "s1"),
        )
        call = ToolCall(id="c1", name="CtxTool", arguments={})

        result = svc.execute(call, context=ctx, registry=reg)

        assert result.status == "completed"
        assert f"cwd={tmp_path / 'project'}" in result.content
        assert f"workspace={tmp_path / 'project'}" in result.content
