"""Tests for host permission fixes — permission_handler adjudication, ASK fast-fail, writable-roots config."""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior,
    PermissionDecision,
)


class TestPermissionHandlerHostAdjudication:
    """permission_handler 语义：True=宿主预授权（可满足策略级 ASK，但不可翻越 SDK 硬门），
    False=DENY，None=交 SDK。硬门（tier/planning/路径/危险命令/deny 规则）只能收紧。"""

    def _make_svc(self, handler, sdk_behavior=PermissionBehavior.DENY):
        from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService

        sdk = MagicMock()
        sdk.check.return_value = PermissionDecision(behavior=sdk_behavior, reason="SDK decides")
        return ToolExecutionService(permission_service=sdk, permission_handler=handler), sdk

    def _tool(self, name="MyTool"):
        t = MagicMock()
        t.name = name
        t.permission_policy = None
        return t

    def test_handler_true_delegates_to_sdk_with_preapproval(self):
        """True = 宿主预授权 → 仍走 permission_service（SDK 的 DENY 不可被翻越）。"""
        svc, sdk = self._make_svc(lambda name, inp: True, sdk_behavior=PermissionBehavior.DENY)
        decision = svc._check_permissions(self._tool(), {}, "s1", journal_authority=object())
        assert decision.behavior == PermissionBehavior.DENY
        sdk.check.assert_called_once()
        assert sdk.check.call_args.kwargs.get("host_preapproved") is True

    def test_handler_false_denies(self):
        """False = 宿主拒绝 → DENY，跳过 permission_service。"""
        svc, sdk = self._make_svc(lambda name, inp: False)
        decision = svc._check_permissions(self._tool(), {}, "s1", journal_authority=object())
        assert decision.behavior == PermissionBehavior.DENY
        sdk.check.assert_not_called()

    def test_handler_none_delegates_to_sdk(self):
        """None = 宿主无意见 → 交给 SDK 判断（permission_service 照常）。"""
        svc, sdk = self._make_svc(lambda name, inp: None, sdk_behavior=PermissionBehavior.ASK)
        decision = svc._check_permissions(self._tool(), {}, "s1", journal_authority=object())
        assert decision.behavior == PermissionBehavior.ASK
        sdk.check.assert_called_once()

    def test_handler_exception_delegates_to_sdk(self):
        """钩子抛异常按无意见处理（不放大放行），交给 SDK 判断。"""
        def boom(name, inp):
            raise RuntimeError("handler down")

        svc, sdk = self._make_svc(boom, sdk_behavior=PermissionBehavior.DENY)
        decision = svc._check_permissions(self._tool(), {}, "s1", journal_authority=object())
        assert decision.behavior == PermissionBehavior.DENY
        sdk.check.assert_called_once()


class TestPermissionHandlerPreapprovalGates:
    """宿主 True 预授权与 SDK 硬门的组合语义（真实 PermissionService，非 mock）。"""

    def _make(self, handler, tmp_path, deny_rules=None):
        from floodmind.agent.runtime.contracts.workspace import Workspace
        from floodmind.agent.runtime.services.path_service import PathService
        from floodmind.agent.runtime.services.permission_service import PermissionService
        from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService

        ws = Workspace.from_folder(tmp_path / "ws", session_id="s1").ensure()
        ps = PathService(workspace=ws)
        sdk = PermissionService.create_default(path_service=ps)
        for rule in (deny_rules or []):
            sdk.add_deny_rule(rule)
        svc = ToolExecutionService(permission_service=sdk, permission_handler=handler,
                                   path_service=ps)
        return svc, sdk

    def _write_tool(self):
        from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
        t = MagicMock()
        t.name = "Write"
        t.permission_policy = ToolPermissionPolicy(policy_type="write", path_field="file_path")
        t.is_readonly = False
        return t

    def _ask_tool(self):
        from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
        t = MagicMock()
        t.name = "RiskyThing"
        t.permission_policy = ToolPermissionPolicy(policy_type="ask", reason="需要确认")
        t.is_readonly = False
        return t

    def test_preapproval_satisfies_policy_ask(self, tmp_path):
        """True 预授权可满足策略级 ASK（桌面 always-trust 场景），无需 AskService。"""
        svc, _ = self._make(lambda name, inp: True, tmp_path)
        decision = svc._check_permissions(self._ask_tool(), {}, "s1", journal_authority=object())
        assert decision.behavior == PermissionBehavior.ALLOW
        assert "预授权" in decision.reason

    def test_preapproval_cannot_bypass_path_deny(self, tmp_path):
        """True 预授权不可翻越路径硬门：工作区外写入仍 DENY。"""
        svc, _ = self._make(lambda name, inp: True, tmp_path)
        outside = str(tmp_path / "outside" / "evil.txt")
        decision = svc._check_permissions(
            self._write_tool(), {"file_path": outside}, "s1", journal_authority=object()
        )
        assert decision.behavior == PermissionBehavior.DENY

    def test_preapproval_cannot_bypass_deny_rule(self, tmp_path):
        """True 预授权不可翻越全局 deny 规则（F-05：deny 先于 ASK）。"""
        from floodmind.agent.runtime.contracts.permissions import PermissionBehavior, PermissionRule

        rule = PermissionRule(
            name="deny_risky",
            tool_name="RiskyThing",
            behavior=PermissionBehavior.DENY,
            reason="宿主显式拒绝 RiskyThing",
        )
        svc, _ = self._make(lambda name, inp: True, tmp_path, deny_rules=[rule])
        decision = svc._check_permissions(self._ask_tool(), {}, "s1", journal_authority=object())
        assert decision.behavior == PermissionBehavior.DENY
        assert "deny_risky" in decision.reason or "RiskyThing" in decision.reason

    def test_no_handler_ask_denies_without_ask_service(self, tmp_path):
        """无宿主预授权时策略级 ASK 在 AskService 缺席下自动 DENY（fail-closed）。"""
        svc, _ = self._make(None, tmp_path)
        decision = svc._check_permissions(self._ask_tool(), {}, "s1", journal_authority=object())
        assert decision.behavior == PermissionBehavior.DENY


class TestAskRequestLifecycle:
    @pytest.mark.parametrize("approved", [True, False])
    def test_pending_survives_until_permission_resolved_event(self, approved):
        import threading

        from floodmind.agent.runtime.contracts.permissions import PermissionAskRequest, PermissionAskResponse
        from floodmind.agent.runtime.services.ask_service import AskService

        svc = AskService(timeout=1.0)
        events = []

        def emit(event):
            events.append(event)
            if event["type"] == "permission_resolved":
                assert svc.is_pending(event["ask_id"])

        svc.set_emit_fn(emit, session_id="s1")
        result = []
        worker = threading.Thread(
            target=lambda: result.append(svc.request(PermissionAskRequest(
                session_id="s1", call_id="c1", tool_name="Bash",
                reason="confirm", tool_input={"command": "pwd"},
            ), journal_authority=MagicMock()))
        )
        worker.start()

        for _ in range(100):
            pending = svc.pending("s1")
            if pending:
                break
            worker.join(0.01)
        assert pending
        ask_id = pending[0].ask_id
        assert svc.respond(PermissionAskResponse(
            session_id="s1", ask_id=ask_id, approved=approved,
        ))
        worker.join(timeout=1.0)

        assert result == [approved]
        assert [event["type"] for event in events] == [
            "action_start", "permission_ask", "permission_resolved",
        ]
        assert events[-1] == {
            "type": "permission_resolved",
            "session_id": "s1",
            "call_id": "c1",
            "ask_id": ask_id,
            "approved": approved,
        }
        assert not svc.is_pending(ask_id)
        assert not svc.respond(PermissionAskResponse(
            session_id="s1", ask_id=ask_id, approved=not approved,
        ))

    def test_timeout_emits_denial_then_rejects_late_response(self):
        from floodmind.agent.runtime.contracts.permissions import PermissionAskRequest, PermissionAskResponse
        from floodmind.agent.runtime.services.ask_service import AskService

        svc = AskService(timeout=0.01)
        events = []
        svc.set_emit_fn(events.append, session_id="s1")

        assert svc.request(PermissionAskRequest(
            session_id="s1", call_id="c1", tool_name="Bash", reason="confirm",
        ), journal_authority=MagicMock()) is False

        resolved = events[-1]
        assert resolved["type"] == "permission_resolved"
        assert resolved["approved"] is False
        assert not svc.is_pending(resolved["ask_id"])
        assert not svc.respond(PermissionAskResponse(
            session_id="s1", ask_id=resolved["ask_id"], approved=True,
        ))


class TestAskTimeoutAutoReject:
    """ASK 无宿主响应时按 AskService 超时自动拒绝，不再无限轮询。"""

    def _build_executor(self, ask_service):
        from floodmind.agent.native.executor import NativeAgentExecutor
        from floodmind.agent.native.event_bus import EventBus
        from floodmind.agent.native.message_builder import MessageBuilder
        from floodmind.agent.native.model_client import ModelClient

        mc = MagicMock(spec=ModelClient)
        return NativeAgentExecutor(
            model_client=mc,
            tool_executor=MagicMock(),
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=3,
            system_prompt="test",
            tools_schema=[],
            tool_registry=MagicMock(),
        ), ask_service

    def test_ask_older_than_timeout_auto_rejected(self):
        from floodmind.agent.native.types import AgentLoopState
        from floodmind.agent.runtime.contracts.permissions import PermissionAskRequest
        from floodmind.agent.runtime.services.ask_service import AskService, set_ask_service

        svc = AskService(timeout=300.0)
        svc.set_emit_fn(lambda ev: None)
        ask_id = svc.start_ask(PermissionAskRequest(session_id="s1", tool_name="Bash", reason="确认"), journal_authority=MagicMock())
        # 把 created_at 人为改老（模拟等了很久无人响应）
        svc._pending[ask_id].created_at -= 400.0

        executor, _ = self._build_executor(svc)
        # 注入全局 AskService（executor 经 get_ask_service() 读取）
        from floodmind.agent.runtime.services import ask_service as ask_module
        from unittest.mock import patch
        with patch.object(ask_module, "get_ask_service", return_value=svc):
            state = AgentLoopState(session_id="s1", status="awaiting_permission", pending_ask_id=ask_id)
            state.pending_tool_calls = []
            executor._on_awaiting_permission(state, MagicMock())
        # 自动拒绝 → 回 awaiting_llm，pending_ask_id 清空，而非无限 sleep
        assert state.status == "awaiting_llm"
        assert state.pending_ask_id is None

    def test_ask_within_timeout_still_pending(self):
        from floodmind.agent.native.types import AgentLoopState
        from floodmind.agent.runtime.contracts.permissions import PermissionAskRequest
        from floodmind.agent.runtime.services.ask_service import AskService

        svc = AskService(timeout=300.0)
        svc.set_emit_fn(lambda ev: None)
        ask_id = svc.start_ask(PermissionAskRequest(session_id="s1", tool_name="Bash", reason="确认"), journal_authority=MagicMock())

        executor, _ = self._build_executor(svc)
        from floodmind.agent.runtime.services import ask_service as ask_module
        from unittest.mock import patch
        with patch.object(ask_module, "get_ask_service", return_value=svc):
            state = AgentLoopState(session_id="s1", status="awaiting_permission", pending_ask_id=ask_id)
            state.pending_tool_calls = []
            executor._on_awaiting_permission(state, MagicMock())
        # 未超时 → 仍 awaiting_permission（等待宿主响应）
        assert state.status == "awaiting_permission"
        assert state.pending_ask_id == ask_id


class TestBashWriteScope:
    """Bash 写范围可配：writable_roots 运行时扩展 + web_session 自动含会话目录。"""

    def test_workspace_add_writable_root(self, tmp_path):
        from floodmind.agent.runtime.contracts.workspace import Workspace

        ws = Workspace.from_folder(tmp_path / "ws", session_id="s1")
        ext = tmp_path / "external"
        ws.add_writable_root(ext)
        assert ext.resolve() in ws.writable_roots
        # 幂等
        ws.add_writable_root(ext)
        assert ws.writable_roots.count(ext.resolve()) == 1

    def test_build_workspace_includes_session_dir(self, tmp_path):
        from floodmind.agent.runtime.services.workspace_service import build_workspace

        session_root = tmp_path / "sessions"
        ws = build_workspace("sid1", session_root=session_root)
        assert (session_root / "sid1").resolve() in ws.writable_roots

    def test_write_to_uploads_allowed_after_add_writable_root(self, tmp_path):
        from floodmind.agent.runtime.contracts.workspace import Workspace
        from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
        from floodmind.agent.runtime.services.path_service import PathService
        from floodmind.tools.agent_tool import resolve_tool_path
        from floodmind.tools.session_context import set_runtime_context

        ws = Workspace.from_folder(tmp_path / "ws", session_id="s1")
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        ws.add_writable_root(uploads)
        set_runtime_context(RuntimeContext("s1", "s1", "run", "thread", "turn", path_service=PathService(workspace=ws)))
        try:
            result = resolve_tool_path(str(uploads / "shot.png"), access="write")
            assert result.allowed, f"uploads/ 应可写: {result.reason}"
        finally:
            set_runtime_context(None)
