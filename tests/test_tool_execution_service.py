"""Tests for ToolExecutionService dangerous command enforcement."""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.types import RunContext
from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior,
    PermissionDecision,
    ToolPermissionPolicy,
)
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


def _make_write_tool(base_decision=None):
    """构造一个 write 策略工具；base_decision 控制 SDK 基础权限判断结果。"""
    tool = MagicMock()
    tool.name = "TestWrite"
    tool.permission_policy = ToolPermissionPolicy(policy_type="write", path_field="file_path")
    tool.validate_input.return_value = MagicMock(valid=True)
    tool.args_schema = None
    tool.func = lambda file_path, content="": f"wrote {file_path}"
    if base_decision is not None:
        tool.check_permissions.return_value = base_decision
    else:
        # bool 返回（无 behavior 字段）→ SDK 基础判断为 ALLOW
        tool.check_permissions.return_value = True
    return tool


def _ctx():
    return RunContext(session_id="s1", user_text="test", output_dir="/tmp/out", upload_dir="/tmp/up")


class TestPermissionDecisionHook:
    def test_hook_receives_sdk_decision_and_policy(self):
        seen = {}

        def hook(tool_name, tool_input, sdk_decision, policy):
            seen["tool_name"] = tool_name
            seen["tool_input"] = tool_input
            seen["sdk_decision"] = sdk_decision
            seen["policy_type"] = getattr(policy, "policy_type", None)
            return sdk_decision

        svc = ToolExecutionService(permission_decision_hook=hook)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool()
        call = ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"})

        result = svc.execute(call, context=_ctx(), registry=reg)

        assert result.status == "completed"
        assert seen["tool_name"] == "TestWrite"
        assert seen["tool_input"] == {"file_path": "a.txt"}  # 不含 __call_id
        assert seen["sdk_decision"].behavior == PermissionBehavior.ALLOW
        assert seen["policy_type"] == "write"

    def test_hook_upgrades_allow_to_ask(self):
        ask_service = MagicMock()
        ask_service.start_ask.return_value = "ask-123"

        def hook(tool_name, tool_input, sdk_decision, policy):
            return PermissionDecision(behavior=PermissionBehavior.ASK, reason="需要确认")

        svc = ToolExecutionService(permission_decision_hook=hook, ask_service=ask_service)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool()
        call = ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"})

        result = svc.execute(call, context=_ctx(), registry=reg)

        assert result.status == "awaiting_permission"
        assert result.metadata["ask_id"] == "ask-123"
        ask_service.start_ask.assert_called_once()

    def test_hook_upgrades_allow_to_deny(self):
        def hook(tool_name, tool_input, sdk_decision, policy):
            return PermissionDecision(behavior=PermissionBehavior.DENY, reason="宿主拒绝")

        svc = ToolExecutionService(permission_decision_hook=hook)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool()
        call = ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"})

        result = svc.execute(call, context=_ctx(), registry=reg)

        assert result.status == "error"
        assert "宿主拒绝" in result.content

    def test_hook_cannot_override_sdk_deny(self):
        # SDK 安全拒绝（如危险命令/越界路径）不可被宿主翻成 ALLOW
        def hook(tool_name, tool_input, sdk_decision, policy):
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, reason="宿主放行")

        sdk_deny = PermissionDecision(behavior=PermissionBehavior.DENY, reason="危险命令")
        svc = ToolExecutionService(permission_decision_hook=hook)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool(base_decision=sdk_deny)
        call = ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"})

        result = svc.execute(call, context=_ctx(), registry=reg)

        assert result.status == "error"
        assert "危险命令" in result.content

    def test_hook_cannot_downgrade_sdk_ask_to_allow(self):
        ask_service = MagicMock()
        ask_service.start_ask.return_value = "ask-1"

        def hook(tool_name, tool_input, sdk_decision, policy):
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, reason="宿主放行")

        sdk_ask = PermissionDecision(behavior=PermissionBehavior.ASK, reason="SDK 要求确认")
        svc = ToolExecutionService(permission_decision_hook=hook, ask_service=ask_service)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool(base_decision=sdk_ask)
        call = ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"})

        result = svc.execute(call, context=_ctx(), registry=reg)

        # ASK 未被降级：仍走授权流程
        assert result.status == "awaiting_permission"

    def test_hook_exception_falls_back_to_sdk_decision(self):
        def hook(tool_name, tool_input, sdk_decision, policy):
            raise RuntimeError("boom")

        svc = ToolExecutionService(permission_decision_hook=hook)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool()
        call = ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"})

        result = svc.execute(call, context=_ctx(), registry=reg)

        assert result.status == "completed"
        assert "wrote a.txt" in result.content

    @pytest.mark.parametrize("bad_return", [None, "allow", 42])
    def test_hook_invalid_return_falls_back(self, bad_return):
        svc = ToolExecutionService(permission_decision_hook=lambda *a: bad_return)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool()
        call = ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"})

        result = svc.execute(call, context=_ctx(), registry=reg)

        assert result.status == "completed"

    def test_preauthorized_ask_skips_reask_after_hook(self):
        # hook 升级为 ASK，但本次调用携带已授权 ask_id → 直接放行执行
        ask_service = MagicMock()
        ask_service.wait_response.return_value = True

        def hook(tool_name, tool_input, sdk_decision, policy):
            return PermissionDecision(behavior=PermissionBehavior.ASK, reason="需要确认")

        svc = ToolExecutionService(permission_decision_hook=hook, ask_service=ask_service)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool()
        call = ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"})

        result = svc.execute(call, context=_ctx(), registry=reg, authorized_ask_id="ask-123")

        assert result.status == "completed"
        assert "wrote a.txt" in result.content
        ask_service.wait_response.assert_called_once_with("ask-123", timeout=0)
