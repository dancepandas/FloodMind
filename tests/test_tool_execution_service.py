"""Tests for ToolExecutionService dangerous command enforcement."""

import threading
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field

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

        result = svc.execute(call, context=ctx, registry=reg, journal_authority=object())

        assert result.status == "error"
        assert "危险" in result.content or "PERMISSION_DENIED" in result.content

    def test_exec_policy_allows_safe_command(self):
        perm_svc = PermissionService()
        svc = ToolExecutionService(permission_service=perm_svc)

        reg = MagicMock()
        reg.get.return_value = _make_exec_tool()

        ctx = RunContext(session_id="s1", user_text="test", output_dir="/tmp/out", upload_dir="/tmp/up")
        call = ToolCall(id="c1", name="TestExec", arguments={"command": "python script.py"})

        result = svc.execute(call, context=ctx, registry=reg, journal_authority=object())

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

        result = svc.execute(call, context=ctx, registry=reg, journal_authority=object())

        assert result.status == "completed"
        assert f"cwd={tmp_path / 'project'}" in result.content
        assert f"workspace={tmp_path / 'project'}" in result.content

    def test_modern_callback_internal_type_error_runs_side_effect_once(self, tmp_path):
        calls = []

        def _callback(session_id, output_dir, **kwargs):
            calls.append((session_id, output_dir, kwargs))
            raise TypeError("callback body failed")

        svc = ToolExecutionService(set_session_context_fn=_callback)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool()
        ctx = RunContext(
            session_id="s1",
            user_text="test",
            output_dir=str(tmp_path / "out"),
            upload_dir=str(tmp_path / "up"),
            cwd=str(tmp_path),
        )

        with pytest.raises(TypeError, match="callback body failed"):
            svc.execute(
                ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"}),
                context=ctx,
                registry=reg,
                journal_authority=object(),
            )

        assert len(calls) == 1

    def test_legacy_callback_receives_only_supported_fields(self):
        calls = []

        def _legacy(session_id, output_dir, delegate_cwd=None):
            calls.append((session_id, output_dir, delegate_cwd))

        svc = ToolExecutionService(set_session_context_fn=_legacy)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool()

        result = svc.execute(
            ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"}),
            context=_ctx(),
            registry=reg,
                journal_authority=object(),
        )

        assert result.status == "completed"
        assert calls == [("s1", "/tmp/out", None)]


class TestToolExecutionTimeout:
    def test_running_timeout_is_indeterminate_and_not_retryable(self):
        started = threading.Event()
        release = threading.Event()
        tool = _make_write_tool()

        def _side_effect(file_path, content=""):
            started.set()
            release.wait(timeout=2)
            return "eventually completed"

        tool.func = _side_effect
        reg = MagicMock()
        reg.get.return_value = tool
        svc = ToolExecutionService()
        svc.TOOL_TIMEOUT_SECONDS = 0.05

        try:
            result = svc.execute(
                ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"}),
                context=_ctx(),
                registry=reg,
                journal_authority=object(),
            )
        finally:
            release.set()

        assert started.is_set()
        assert result.status == "error"
        assert result.metadata["error_code"] == "TOOL_EXECUTION_TIMEOUT_INDETERMINATE"
        assert result.metadata["execution_state"] == "indeterminate_running"
        assert result.metadata["indeterminate"] is True
        assert result.metadata["cancelled"] is False
        assert result.metadata["retryable"] is False
        assert result.metadata["do_not_retry_same_call"] is True
        assert "不要自动重试" in result.content


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

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

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

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

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

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

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

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

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

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

        # ASK 未被降级：仍走授权流程
        assert result.status == "awaiting_permission"

    def test_hook_exception_falls_back_to_sdk_decision(self):
        def hook(tool_name, tool_input, sdk_decision, policy):
            raise RuntimeError("boom")

        svc = ToolExecutionService(permission_decision_hook=hook)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool()
        call = ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"})

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

        assert result.status == "completed"
        assert "wrote a.txt" in result.content

    @pytest.mark.parametrize("bad_return", [None, "allow", 42])
    def test_hook_invalid_return_falls_back(self, bad_return):
        svc = ToolExecutionService(permission_decision_hook=lambda *a: bad_return)
        reg = MagicMock()
        reg.get.return_value = _make_write_tool()
        call = ToolCall(id="c1", name="TestWrite", arguments={"file_path": "a.txt"})

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

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

        result = svc.execute(call, context=_ctx(), registry=reg, authorized_ask_id="ask-123", journal_authority=object())

        assert result.status == "completed"
        assert "wrote a.txt" in result.content
        ask_service.wait_response.assert_called_once_with("ask-123", timeout=0)


class _PydToolInput(BaseModel):
    """模拟 GetSkill 类带 pydantic args_schema 的工具输入。"""
    skill_name: str = Field(description="[必填] 技能名")


class TestExecPolicyWriteTarget:
    """SDK 收敛项 ①：exec 命令体内写目标越权 → DENY（堵住只读授权被 Bash 绕过）。"""

    def _make_svc(self, tmp_path):
        from floodmind.agent.runtime.contracts.workspace import Workspace
        from floodmind.agent.runtime.services.path_service import PathService

        ws = Workspace.from_folder(tmp_path / "ws").ensure()
        svc = PermissionService()
        svc._path_service = PathService(project_root=tmp_path, workspace=ws)
        return svc

    def test_denies_out_of_roots_write_target(self, tmp_path):
        svc = self._make_svc(tmp_path)
        outside = tmp_path / "external" / "x.txt"
        normalized = {"command": f'Set-Content -Path {outside} hi'}

        decision = svc._check_exec_policy(normalized, "command", [], session_id="main-sess")

        assert decision.behavior == PermissionBehavior.DENY
        assert "写目标" in decision.reason

    def test_denies_out_of_roots_redirect(self, tmp_path):
        svc = self._make_svc(tmp_path)
        outside = tmp_path / "external" / "x.txt"
        normalized = {"command": f"echo hi > {outside}"}

        decision = svc._check_exec_policy(normalized, "command", [], session_id="main-sess")

        assert decision.behavior == PermissionBehavior.DENY
        assert "写目标" in decision.reason

    def test_allows_in_roots_write_target(self, tmp_path):
        svc = self._make_svc(tmp_path)
        inside = tmp_path / "ws" / "out.txt"
        normalized = {"command": f"echo hi > {inside}"}

        decision = svc._check_exec_policy(normalized, "command", [], session_id="main-sess")

        # 写目标在允许目录内 → 不因写目标拒绝（可能因 mutating 模式返回 ASK，但非 DENY）
        assert decision.behavior != PermissionBehavior.DENY

    def test_exec_service_denies_write_target(self, tmp_path):
        """经 ToolExecutionService 全链路：exec 工具命令体内越权写 → DENY 反馈。"""
        from floodmind.agent.runtime.contracts.workspace import Workspace
        from floodmind.agent.runtime.services.path_service import PathService

        ws = Workspace.from_folder(tmp_path / "ws").ensure()
        perm_svc = PermissionService()
        perm_svc._path_service = PathService(project_root=tmp_path, workspace=ws)
        svc = ToolExecutionService(permission_service=perm_svc)

        reg = MagicMock()
        tool = _make_exec_tool()
        tool.permission_policy = ToolPermissionPolicy(policy_type="exec", command_field="command")
        reg.get.return_value = tool

        outside = tmp_path / "external" / "x.txt"
        call = ToolCall(id="c1", name="TestExec", arguments={"command": f'Set-Content -Path {outside} hi'})

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

        assert result.status == "error"
        assert "写目标" in result.content


class TestMalformedArgumentKeys:
    """MiniMax-M3 等模型偶发畸形参数键（键名带尾引号/空白/控制符）健壮性回归。

    复现背景：GetTool、系统工具、MCP 工具都是无 pydantic args_schema
    的裸 JSON Schema 工具。旧逻辑 _validate_schema 见 args_schema is None 原样放行，
    模型发 {"tool_name"": "..."}（键带尾引号）→ **kwargs 崩成
    TypeError: unexpected keyword argument 'tool_name"'，模型看不懂不会自纠。
    """

    def _make_no_schema_tool(self):
        """模拟 GetTool：parameters 为裸 JSON Schema，无 pydantic args_schema。"""
        tool = MagicMock()
        tool.name = "GetTool"
        tool.permission_policy = None
        tool.check_permissions.return_value = True
        tool.validate_input.return_value = MagicMock(valid=True)
        tool.args_schema = None
        tool.parameters = {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "include_schema": {"type": "boolean"},
            },
            "required": ["tool_name"],
        }
        tool.func = lambda tool_name="", include_schema=True: f"detail: {tool_name}"
        return tool

    def _make_pyd_schema_tool(self):
        """模拟 GetSkill：带 pydantic args_schema 的工具。"""
        tool = MagicMock()
        tool.name = "GetSkill"
        tool.permission_policy = None
        tool.check_permissions.return_value = True
        tool.validate_input.return_value = MagicMock(valid=True)
        tool.args_schema = _PydToolInput
        tool.parameters = {"type": "object", "properties": {"skill_name": {"type": "string"}}}
        tool.func = lambda skill_name="": f"skill: {skill_name}"
        return tool

    def test_no_schema_tool_malformed_key_trailing_quote_executes(self):
        # {"tool_name"": "..."} —— 键名带尾引号，应清洗成 tool_name 后正常执行
        svc = ToolExecutionService()
        reg = MagicMock()
        reg.get.return_value = self._make_no_schema_tool()
        call = ToolCall(
            id="c1",
            name="GetTool",
            arguments={'tool_name"': "analyze_example_document"},
        )

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

        assert result.status == "completed"
        assert "detail: analyze_example_document" in result.content

    def test_no_schema_tool_malformed_key_leading_quote_and_control_executes(self):
        # {'"tool_name': 'x'} + 键内控制符：均应清洗成 tool_name
        svc = ToolExecutionService()
        reg = MagicMock()
        reg.get.return_value = self._make_no_schema_tool()
        call = ToolCall(
            id="c2",
            name="GetTool",
            arguments={"\n\"tool_name": "demo"},
        )

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

        assert result.status == "completed"
        assert "detail: demo" in result.content

    def test_pyd_schema_tool_malformed_key_recovers(self):
        # GetSkill 类：pydantic 默认 extra=ignore，键清洗后 tool_name" → tool_name，
        # 但 GetSkill 的合法键是 skill_name —— 键名互斥时仍应给出清晰的校验失败，
        # 而不是 TypeError 崩溃。
        svc = ToolExecutionService()
        reg = MagicMock()
        reg.get.return_value = self._make_pyd_schema_tool()
        call = ToolCall(
            id="c3",
            name="GetSkill",
            arguments={'skill_name"': "chronos"},
        )

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

        # skill_name" → skill_name（正好是合法键）→ 正常执行
        assert result.status == "completed"
        assert "skill: chronos" in result.content

    def test_pyd_schema_tool_missing_required_gives_clean_feedback(self):
        svc = ToolExecutionService()
        reg = MagicMock()
        reg.get.return_value = self._make_pyd_schema_tool()
        call = ToolCall(id="c4", name="GetSkill", arguments={"other": "x"})

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

        assert result.status == "error"
        assert "INPUT_VALIDATION_FAILED" in result.content
        assert "unexpected keyword argument" not in result.content

    def test_unknown_argument_gives_schema_feedback(self):
        svc = ToolExecutionService()
        reg = MagicMock()
        reg.get.return_value = self._make_no_schema_tool()
        call = ToolCall(id="c5", name="GetTool", arguments={"tool_nam": "x"})

        result = svc.execute(call, context=_ctx(), registry=reg, journal_authority=object())

        assert result.status == "error"
        assert "INPUT_VALIDATION_FAILED" in result.content
        assert "unexpected keyword argument" not in result.content


class TestRawParametersJsonSchemaValidation:
    def _execute(self, schema, arguments):
        permission_handler = MagicMock(return_value=True)
        handler = MagicMock(return_value="ok")
        tool = MagicMock()
        tool.name = "SchemaTool"
        tool.parameters = schema
        tool.args_schema = None
        tool.permission_policy = None
        tool.validate_input.return_value = MagicMock(valid=True)
        tool.func = handler
        registry = MagicMock()
        registry.get.return_value = tool

        result = ToolExecutionService(permission_handler=permission_handler).execute(
            ToolCall(id="schema-call", name="SchemaTool", arguments=arguments),
            context=_ctx(),
            registry=registry,
            journal_authority=object(),
        )
        return result, permission_handler, handler

    @pytest.mark.parametrize(
        ("schema", "arguments"),
        [
            ({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, {}),
            ({"type": "object", "properties": {"name": {"type": "string"}}, "additionalProperties": False}, {"name": "ok", "extra": 1}),
            ({"type": "object", "properties": {"count": {"type": "integer"}}}, {"count": "1"}),
            ({"type": "object", "properties": {"config": {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]}}}, {"config": {"enabled": "yes"}}),
            ({"type": "object", "properties": {"mode": {"enum": ["fast", "safe"]}}}, {"mode": "other"}),
            ({"type": "object", "properties": {"value": {"oneOf": [{"type": "string"}, {"type": "integer"}]}}}, {"value": False}),
            ({"$defs": {"item": {"type": "string", "enum": ["a", "b"]}}, "type": "object", "properties": {"item": {"$ref": "#/$defs/item"}}}, {"item": "c"}),
        ],
    )
    def test_invalid_raw_schema_input_fails_before_permission_and_handler(self, schema, arguments):
        result, permission_handler, handler = self._execute(schema, arguments)

        assert result.status == "error"
        assert "INPUT_VALIDATION_FAILED" in result.content
        permission_handler.assert_not_called()
        handler.assert_not_called()

    def test_valid_composed_and_referenced_input_reaches_handler(self):
        schema = {
            "$defs": {"mode": {"enum": ["fast", "safe"]}},
            "type": "object",
            "properties": {
                "mode": {"$ref": "#/$defs/mode"},
                "payload": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            },
            "required": ["mode", "payload"],
            "additionalProperties": False,
        }

        result, permission_handler, handler = self._execute(
            schema, {"mode": "safe", "payload": 3}
        )

        assert result.status == "completed"
        permission_handler.assert_called_once()
        handler.assert_called_once_with(mode="safe", payload=3)
