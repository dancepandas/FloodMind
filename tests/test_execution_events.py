import threading
import time
from unittest.mock import MagicMock

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.types import AgentLoopState, ModelEvent, RunContext
from floodmind.agent.runtime.contracts.permissions import (
    PermissionAskRequest,
    PermissionAskResponse,
    PermissionRequest,
    ToolPermissionPolicy,
)
from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult, ToolSpec
from floodmind.agent.runtime.services.ask_service import AskService
from floodmind.agent.runtime.services.idempotency import (
    derive_idempotency_key,
    side_effect_class_for_spec,
)
from floodmind.agent.runtime.contracts.tool_transaction import canonical_arguments
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
from floodmind.agent.runtime.services.history_projection import project_current
from floodmind.agent.runtime.reducer import reduce, initial_run_state


def test_tool_and_terminal_event_sequence(tmp_path):
    auth = open_journal_authority(
        tmp_path,
        conversation_id="c",
        task_id="t",
        run_id="r",
        thread_id="th",
        turn_id="tu",
    )
    auth.emit(
        "model.attempt.completed",
        {
            "attempt_id": "a1",
            "terminal_reason": "tool_calls",
            "content": "",
            "reasoning": "",
            "tool_calls": [],
            "is_final": False,
            "usage": {},
        },
    )
    auth.emit(
        "tool.execution.started",
        {
            "transaction_id": "ttx_1",
            "call_id": "c1",
            "tool_id": "builtin:Read",
            "arguments": "{}",
        },
    )
    auth.emit(
        "tool.execution.completed",
        {
            "transaction_id": "ttx_1",
            "call_id": "c1",
            "tool_id": "builtin:Read",
            "status": "succeeded",
            "result_summary": "ok",
            "full_ref": "",
            "artifacts": ["art_1"],
        },
    )
    auth.emit(
        "run.completed",
        {"final_output": "done", "terminal_reason": "completed"},
    )
    state = auth.replay(0)
    assert state.status.value == "completed"
    assert state.artifacts == ["art_1"]
    assert state.last_committed_sequence == 4


def test_terminal_tool_failure_closes_started_transaction(tmp_path):
    auth = open_journal_authority(
        tmp_path,
        conversation_id="c",
        task_id="t",
        run_id="r-failed",
        thread_id="th",
        turn_id="tu",
    )
    model_client = MagicMock()
    calls = 0

    def stream_chat(**_kwargs):
        nonlocal calls
        calls += 1
        return [
            ModelEvent(
                type="tool_call_done",
                tool_call=ToolCall(
                    id=f"call-{calls}",
                    name="FailTool",
                    arguments={"attempt": calls},
                ),
            ),
            ModelEvent(type="done"),
        ]

    model_client.stream_chat.side_effect = stream_chat
    model_client.model_name = "test-model"
    tool_executor = MagicMock()
    tool_executor.execute.return_value = ToolResult(
        tool_call_id="ignored",
        name="FailTool",
        content="错误: terminal failure",
        status="error",
        artifacts=["failed-artifact"],
        metadata={"full_ref": "failure-ref"},
    )
    executor = NativeAgentExecutor(
        model_client=model_client,
        tool_executor=tool_executor,
        event_bus=EventBus(),
        tools_schema=[],
        journal_authority=auth,
        max_iterations=10,
    )
    context = RunContext(session_id="session", user_text="fail")

    executor.run(context, "fail")

    events = auth.read_after(0)
    starts = [event for event in events if event.event_type == "tool.execution.started"]
    failures = [event for event in events if event.event_type == "tool.execution.failed"]
    assert len(starts) == 5
    assert len(failures) == 5
    assert failures[-1].payload == {
        "transaction_id": starts[-1].payload["transaction_id"],
        "call_id": starts[-1].payload["call_id"],
        "tool_id": "FailTool",
        "status": "error",
        "result_summary": "错误: terminal failure",
        "full_ref": "failure-ref",
        "artifacts": ["failed-artifact"],
        "idempotency_key": "",
    }
    assert [event.event_type for event in events].count("run.failed") == 1


def test_approval_requested_and_resolved_emit_once(tmp_path):
    auth = open_journal_authority(
        tmp_path,
        conversation_id="c",
        task_id="t",
        run_id="r-approval",
        thread_id="th",
        turn_id="tu",
    )
    service = AskService()
    service.set_emit_fn(lambda _event: None, session_id="session")
    ask_id = service.start_ask(
        PermissionAskRequest(
            session_id="session",
            call_id="call-approval",
            tool_name="Write",
            reason="write",
            tool_input={"path": "x.txt"},
        ),
        journal_authority=auth,
    )
    tool_executor = MagicMock()
    tool_executor.execute.return_value = ToolResult(
        tool_call_id="call-approval",
        name="Write",
        content="waiting",
        status="awaiting_permission",
        metadata={"ask_id": ask_id, "reason": "write"},
    )
    executor = NativeAgentExecutor(
        model_client=MagicMock(),
        tool_executor=tool_executor,
        event_bus=EventBus(),
        tools_schema=[],
        journal_authority=auth,
    )
    state = AgentLoopState(
        session_id="session",
        run_id="r-approval",
        status="awaiting_tool",
        pending_tool_calls=[
            ToolCall(id="call-approval", name="Write", arguments={"path": "x.txt"})
        ],
    )

    state = executor._on_awaiting_tool(
        state, RunContext(session_id="session", user_text="approve")
    )
    assert state.status == "awaiting_permission"
    assert service.respond(
        PermissionAskResponse(session_id="session", ask_id=ask_id, approved=True)
    )

    event_types = [event.event_type for event in auth.read_after(0)]
    assert event_types.count("tool.approval.requested") == 1
    assert event_types.count("tool.approval.resolved") == 1


def test_approval_authority_is_bound_to_pending_ask(tmp_path):
    auth = open_journal_authority(
        tmp_path,
        conversation_id="c",
        task_id="t",
        run_id="r-approval",
        thread_id="th",
        turn_id="tu",
    )
    service = AskService()
    service.set_emit_fn(lambda _event: None, session_id="session")
    ask_id = service.start_ask(
        PermissionAskRequest(
            session_id="session",
            call_id="call-approval",
            tool_name="Write",
            reason="write",
            tool_input={"path": "x.txt"},
        ),
        journal_authority=auth,
    )

    assert service.respond(
        PermissionAskResponse(session_id="session", ask_id=ask_id, approved=True)
    )
    resolved = [
        event for event in auth.read_after(0)
        if event.event_type == "tool.approval.resolved"
    ]
    assert len(resolved) == 1
    assert resolved[0].payload == {
        "ask_id": ask_id,
        "call_id": "call-approval",
        "approved": True,
    }


def test_synchronous_permission_ask_response_is_accepted_and_ordered(tmp_path):
    auth = open_journal_authority(
        tmp_path,
        conversation_id="c",
        task_id="t",
        run_id="r-sync-approval",
        thread_id="th",
        turn_id="tu",
    )
    service = AskService()
    respond_results = []

    def emit_fn(event):
        if event["type"] == "permission_ask":
            respond_results.append(
                service.respond(
                    PermissionAskResponse(
                        session_id="session",
                        ask_id=event["ask_id"],
                        approved=True,
                    )
                )
            )

    service.set_emit_fn(emit_fn, session_id="session")
    ask_id = service.start_ask(
        PermissionAskRequest(
            session_id="session",
            call_id="call-sync-approval",
            tool_name="Write",
            reason="write",
            tool_input={"path": "同步.txt"},
        ),
        journal_authority=auth,
    )

    assert ask_id is not None
    assert respond_results == [True]
    approval_events = [
        event for event in auth.read_after(0)
        if event.event_type.startswith("tool.approval.")
    ]
    assert [event.event_type for event in approval_events] == [
        "tool.approval.requested",
        "tool.approval.resolved",
    ]
    requested, resolved = approval_events
    assert requested.payload == {
        "ask_id": ask_id,
        "call_id": "call-sync-approval",
        "tool_name": "Write",
        "reason": "write",
        "arguments": '{"path": "同步.txt"}',
    }
    assert resolved.payload == {
        "ask_id": ask_id,
        "call_id": "call-sync-approval",
        "approved": True,
    }


def test_blocking_permission_ask_timeout_emits_matching_denial_once(tmp_path):
    auth = open_journal_authority(
        tmp_path,
        conversation_id="c",
        task_id="t",
        run_id="r-blocking-timeout",
        thread_id="th",
        turn_id="tu",
    )
    ask_service = AskService(timeout=0.01)
    ask_service.set_emit_fn(lambda _event: None, session_id="session")
    permission_service = PermissionService(ask_service=ask_service)
    decision = permission_service.check(
        PermissionRequest(
            session_id="session",
            call_id="call-timeout",
            tool_name="DangerousTool",
            tool_input={"path": "x.txt"},
            permission_policy=ToolPermissionPolicy(policy_type="ask", reason="confirm"),
        ),
        journal_authority=auth,
    )

    assert decision.behavior.value == "deny"
    approval_events = [
        event for event in auth.read_after(0)
        if event.event_type.startswith("tool.approval.")
    ]
    assert [event.event_type for event in approval_events] == [
        "tool.approval.requested",
        "tool.approval.resolved",
    ]
    requested, resolved = approval_events
    assert resolved.payload == {
        "ask_id": requested.payload["ask_id"],
        "call_id": "call-timeout",
        "approved": False,
    }


def test_blocking_permission_ask_emits_matching_events_once(tmp_path):
    auth = open_journal_authority(
        tmp_path,
        conversation_id="c",
        task_id="t",
        run_id="r-blocking-approval",
        thread_id="th",
        turn_id="tu",
    )
    ask_service = AskService(timeout=3.0)
    ask_service.set_emit_fn(lambda _event: None, session_id="session")
    permission_service = PermissionService(ask_service=ask_service)
    request = PermissionRequest(
        session_id="session",
        call_id="call-blocking",
        tool_name="DangerousTool",
        tool_input={"path": "x.txt"},
        permission_policy=ToolPermissionPolicy(policy_type="ask", reason="confirm"),
    )
    decision = {}

    worker = threading.Thread(
        target=lambda: decision.setdefault(
            "value", permission_service.check(request, journal_authority=auth)
        )
    )
    worker.start()
    for _ in range(100):
        pending = ask_service.pending("session")
        if pending:
            break
        time.sleep(0.01)
    assert pending
    ask_id = pending[0].ask_id
    assert ask_service.respond(
        PermissionAskResponse(session_id="session", ask_id=ask_id, approved=True)
    )
    worker.join(timeout=3.0)
    assert not worker.is_alive()

    approval_events = [
        event for event in auth.read_after(0)
        if event.event_type.startswith("tool.approval.")
    ]
    assert [event.event_type for event in approval_events] == [
        "tool.approval.requested",
        "tool.approval.resolved",
    ]
    assert {event.payload["ask_id"] for event in approval_events} == {ask_id}
    assert {event.payload["call_id"] for event in approval_events} == {"call-blocking"}


def _make_write_spec():
    """A real ToolSpec for a non-read write tool (non-empty idempotency key)."""
    return ToolSpec(
        name="Write",
        description="write a file",
        parameters={"type": "object", "properties": {}},
        func=lambda path: "wrote",
        is_readonly=False,
        is_destructive=False,
    )


def test_executor_emits_proposed_before_started_with_full_fields(tmp_path):
    auth = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="r", thread_id="th", turn_id="tu",
    )
    registry = MagicMock()
    registry.get.return_value = _make_write_spec()
    tool_executor = MagicMock()
    tool_executor.execute.return_value = ToolResult(
        tool_call_id="c1", name="Write", content="wrote", status="completed",
    )
    executor = NativeAgentExecutor(
        model_client=MagicMock(),
        tool_executor=tool_executor,
        event_bus=EventBus(),
        tools_schema=[],
        tool_registry=registry,
        journal_authority=auth,
    )
    state = AgentLoopState(
        session_id="session",
        run_id="r",
        status="awaiting_tool",
        pending_tool_calls=[ToolCall(id="c1", name="Write", arguments={"path": "/a"})],
    )

    executor._on_awaiting_tool(state, RunContext(session_id="session", user_text="t"))

    events = auth.read_after(0)
    assert [e.event_type for e in events][:2] == [
        "tool.call.proposed", "tool.execution.started",
    ]
    proposed = events[0].payload
    assert proposed["transaction_id"]
    assert proposed["call_id"] == "c1"
    assert proposed["tool_id"] == "Write"
    assert proposed["tool_version"] == "1"
    assert proposed["canonical_arguments"] == '{"path":"/a"}'
    assert proposed["arguments_sha256"]
    assert proposed["side_effect_class"] == "reversible_write"
    assert proposed["idempotency_key"] == derive_idempotency_key(
        tool_id="Write",
        canonical_arguments='{"path":"/a"}',
        side_effect_class="reversible_write",
    )
    assert proposed["preconditions"] == []
    assert events[1].payload["transaction_id"] == proposed["transaction_id"]
    completed = [e for e in events if e.event_type == "tool.execution.completed"]
    assert completed[0].payload["idempotency_key"] == proposed["idempotency_key"]


def test_timeout_result_emits_indeterminate_event(tmp_path):
    auth = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="r", thread_id="th", turn_id="tu",
    )
    tool_executor = MagicMock()
    tool_executor.execute.return_value = ToolResult(
        tool_call_id="c1",
        name="Bash",
        content="工具执行超过300秒；操作结果不确定",
        status="error",
        metadata={"indeterminate": True},
    )
    executor = NativeAgentExecutor(
        model_client=MagicMock(),
        tool_executor=tool_executor,
        event_bus=EventBus(),
        tools_schema=[],
        journal_authority=auth,
    )
    state = AgentLoopState(
        session_id="session",
        run_id="r",
        status="awaiting_tool",
        pending_tool_calls=[ToolCall(id="c1", name="Bash", arguments={"command": "sleep 100"})],
    )

    executor._on_awaiting_tool(state, RunContext(session_id="session", user_text="t"))

    events = auth.read_after(0)
    types = [e.event_type for e in events]
    assert "tool.execution.indeterminate" in types
    assert "tool.execution.failed" not in types
    assert "tool.execution.completed" not in types
    indet = [e for e in events if e.event_type == "tool.execution.indeterminate"][0]
    assert indet.payload["transaction_id"]
    assert indet.payload["call_id"] == "c1"
    assert indet.payload["tool_id"] == "Bash"
    assert indet.payload["reason"]
    assert "idempotency_key" in indet.payload


def test_replayed_committed_result_skips_reexecution(tmp_path):
    """Executor: a committed result for the same idempotency key is replayed — the
    tool function runs once, then the second identical call is served from the journal."""
    auth = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="r", thread_id="th", turn_id="tu",
    )
    calls = []
    spec = _make_write_spec()

    def _func(path):
        calls.append(path)
        return f"wrote {path}"

    spec.func = _func
    registry = MagicMock()
    registry.get.return_value = spec
    svc = ToolExecutionService()
    call = ToolCall(id="c1", name="Write", arguments={"path": "/a"})
    canon = canonical_arguments(call.arguments)
    ik = derive_idempotency_key(
        tool_id=call.name,
        canonical_arguments=canon,
        side_effect_class=side_effect_class_for_spec(spec),
    )
    # First call executes normally.
    r1 = svc.execute(call, context=RunContext(session_id="session", user_text="t"),
                     registry=registry, journal_authority=auth)
    assert r1.status == "completed" and calls == ["/a"]
    # The executor would then commit the result to the journal (same idempotency key).
    auth.emit("tool.execution.completed", {
        "transaction_id": "ttx_old", "call_id": "c_old", "tool_id": "Write",
        "status": "succeeded", "result_summary": "wrote /a", "full_ref": "ref://a",
        "artifacts": ["art_1"], "idempotency_key": ik,
    })
    # Second identical call: replayed, never re-executed.
    r2 = svc.execute(ToolCall(id="c2", name="Write", arguments={"path": "/a"}),
                     context=RunContext(session_id="session", user_text="t"),
                     registry=registry, journal_authority=auth)
    assert r2.status == "completed"
    assert r2.metadata.get("idempotent_replay") is True
    assert r2.content == "wrote /a"
    assert r2.artifacts == ["art_1"]
    assert calls == ["/a"]  # still only executed once
