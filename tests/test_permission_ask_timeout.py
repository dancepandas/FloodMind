"""Permission ASK 交互时限：按会话覆盖、无人值守 fail-closed、执行器超时判定。"""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.types import AgentLoopState
from floodmind.agent.runtime.contracts.permissions import (
    PermissionAskRequest,
    PermissionBehavior,
    PermissionRequest,
    ToolPermissionPolicy,
)
from floodmind.agent.runtime.services.ask_service import AskService
from floodmind.agent.runtime.services.permission_service import PermissionService


def _ask_service(timeout=300.0):
    svc = AskService(timeout=timeout)
    svc.set_emit_fn(lambda event: None)
    return svc


def _ask_request(session_id="s1"):
    return PermissionRequest(
        session_id=session_id,
        call_id="call-1",
        tool_name="Bash",
        tool_input={"command": "echo hi"},
        permission_policy=ToolPermissionPolicy(policy_type="ask", reason="需要确认"),
    )


def test_session_timeout_overrides_global_default():
    svc = _ask_service(timeout=300.0)

    assert svc.get_timeout("s1") == 300.0
    svc.set_session_timeout("s1", 5.0)
    assert svc.get_timeout("s1") == 5.0
    assert svc.get_timeout("s2") == 300.0

    svc.clear_session_timeout("s1")
    assert svc.get_timeout("s1") == 300.0


def test_zero_timeout_disables_interactive_asks():
    svc = _ask_service()
    assert svc.accepts_asks("s1") is True

    svc.set_session_timeout("s1", 0)
    assert svc.accepts_asks("s1") is False

    svc.set_session_timeout("s2", None)
    assert svc.accepts_asks("s2") is True


def test_negative_timeout_rejected():
    svc = _ask_service()
    with pytest.raises(ValueError):
        svc.set_session_timeout("s1", -1)


def test_permission_service_fails_closed_without_interactive_channel():
    ask_service = _ask_service()
    ask_service.set_session_timeout("s1", 0)
    service = PermissionService(ask_service=ask_service, path_service=MagicMock())

    decision = service.check(_ask_request(), journal_authority=MagicMock())

    assert decision.behavior == PermissionBehavior.DENY
    assert "未启用交互审批" in decision.reason
    # 没有发起 ASK，也没有留下悬挂的待应答记录
    assert ask_service.pending("s1") == []


def test_permission_service_waits_for_host_response():
    import threading

    from floodmind.agent.runtime.contracts.permissions import PermissionAskResponse

    ask_service = _ask_service()
    ask_service.set_session_timeout("s1", 5.0)
    service = PermissionService(ask_service=ask_service, path_service=MagicMock())

    asked = threading.Event()
    ask_holder = {}

    def emit(event):
        if event.get("type") == "permission_ask":
            ask_holder["ask_id"] = event["ask_id"]
            asked.set()

    ask_service.set_emit_fn(emit)

    def responder():
        if asked.wait(timeout=5):
            ask_service.respond(PermissionAskResponse(
                session_id="s1", ask_id=ask_holder["ask_id"], approved=True,
            ))

    thread = threading.Thread(target=responder)
    thread.start()
    try:
        decision = service.check(_ask_request(), journal_authority=MagicMock())
    finally:
        thread.join(timeout=5)

    assert decision.behavior == PermissionBehavior.ALLOW
    assert "用户确认允许" in decision.reason


def test_executor_skips_auto_reject_when_timeout_is_unlimited():
    from floodmind.agent.native.executor import NativeAgentExecutor

    ask_service = _ask_service()
    ask_service.set_session_timeout("s1", None)
    ask_id = ask_service.start_ask(
        PermissionAskRequest(
            session_id="s1", call_id="call-1", tool_name="Bash", reason="需要确认",
        ),
        journal_authority=MagicMock(),
    )
    # 人为变老：无限时模式下不应自动拒绝
    ask_service._pending[ask_id].created_at -= 10_000.0

    executor = NativeAgentExecutor.__new__(NativeAgentExecutor)
    executor._get_ask_service = MagicMock(return_value=ask_service)
    state = AgentLoopState(
        session_id="s1", status="awaiting_permission", pending_ask_id=ask_id,
    )

    returned = NativeAgentExecutor._on_awaiting_permission(executor, state, MagicMock())

    assert returned.status == "awaiting_permission"
    assert returned.pending_ask_id == ask_id


def test_agent_respond_permission_answers_current_session(monkeypatch):
    from types import SimpleNamespace

    from floodmind.agent.api import Agent
    from floodmind.agent.runtime.services import ask_service as ask_module

    ask_service = _ask_service()
    monkeypatch.setattr(ask_module, "_global_ask_service", ask_service)

    agent = Agent.__new__(Agent)
    agent._agent = SimpleNamespace(session_id="s1")

    ask_id = ask_service.start_ask(
        PermissionAskRequest(
            session_id="s1", call_id="call-1", tool_name="Bash", reason="需要确认",
        ),
        journal_authority=MagicMock(),
    )
    assert [p["ask_id"] for p in agent.pending_permissions()] == [ask_id]

    assert agent.respond_permission(ask_id, True) is True
    assert agent.pending_permissions() == []

    # 会话不匹配的应答必须被拒绝
    other_agent = Agent.__new__(Agent)
    other_agent._agent = SimpleNamespace(session_id="other")
    other_ask = ask_service.start_ask(
        PermissionAskRequest(
            session_id="other", call_id="call-2", tool_name="Bash", reason="需要确认",
        ),
        journal_authority=MagicMock(),
    )
    assert agent.respond_permission(other_ask, True) is False
    assert other_agent.respond_permission(other_ask, True) is True

