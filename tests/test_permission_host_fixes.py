"""Tests for host permission fixes — permission_handler adjudication, ASK fast-fail, writable-roots config."""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior,
    PermissionDecision,
)


class TestPermissionHandlerHostAdjudication:
    """permission_handler 改为宿主最高裁决：True=ALLOW 跳过 SDK，False=DENY，None=交 SDK。"""

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

    def test_handler_true_direct_allow_bypasses_sdk(self):
        """True = 宿主显式放行 → 直接 ALLOW，跳过 permission_service（即使 SDK 会 DENY）。"""
        svc, sdk = self._make_svc(lambda name, inp: True, sdk_behavior=PermissionBehavior.DENY)
        decision = svc._check_permissions(self._tool(), {}, "s1")
        assert decision.behavior == PermissionBehavior.ALLOW
        sdk.check.assert_not_called()

    def test_handler_false_denies(self):
        """False = 宿主拒绝 → DENY，跳过 permission_service。"""
        svc, sdk = self._make_svc(lambda name, inp: False)
        decision = svc._check_permissions(self._tool(), {}, "s1")
        assert decision.behavior == PermissionBehavior.DENY
        sdk.check.assert_not_called()

    def test_handler_none_delegates_to_sdk(self):
        """None = 宿主无意见 → 交给 SDK 判断（permission_service 照常）。"""
        svc, sdk = self._make_svc(lambda name, inp: None, sdk_behavior=PermissionBehavior.ASK)
        decision = svc._check_permissions(self._tool(), {}, "s1")
        assert decision.behavior == PermissionBehavior.ASK
        sdk.check.assert_called_once()

    def test_handler_exception_delegates_to_sdk(self):
        """钩子抛异常按无意见处理（不放大放行），交给 SDK 判断。"""
        def boom(name, inp):
            raise RuntimeError("handler down")

        svc, sdk = self._make_svc(boom, sdk_behavior=PermissionBehavior.DENY)
        decision = svc._check_permissions(self._tool(), {}, "s1")
        assert decision.behavior == PermissionBehavior.DENY
        sdk.check.assert_called_once()


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
        ask_id = svc.start_ask(PermissionAskRequest(session_id="s1", tool_name="Bash", reason="确认"))
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
        ask_id = svc.start_ask(PermissionAskRequest(session_id="s1", tool_name="Bash", reason="确认"))

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
        from floodmind.agent.runtime.services.path_service import PathService, set_path_service
        from floodmind.tools.agent_tool import resolve_tool_path

        ws = Workspace.from_folder(tmp_path / "ws", session_id="s1")
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        ws.add_writable_root(uploads)
        set_path_service(PathService(workspace=ws))
        try:
            result = resolve_tool_path(str(uploads / "shot.png"), access="write")
            assert result.allowed, f"uploads/ 应可写: {result.reason}"
        finally:
            set_path_service(PathService())
