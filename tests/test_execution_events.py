import threading
import time
from unittest.mock import MagicMock

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.types import AgentLoopState, ModelEvent, RunContext
from floodmind.agent.runtime.contracts.permissions import (
    PermissionAskRequest,
    PermissionAskResponse,
    PermissionBehavior,
    PermissionDecision,
    PermissionRequest,
    ToolPermissionPolicy,
)
from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult, ToolSpec
from floodmind.agent.runtime.services.ask_service import AskService
from floodmind.agent.runtime.services.idempotency import (
    derive_idempotency_key,
    find_committed_result,
    side_effect_class_for_spec,
)
from floodmind.agent.runtime.contracts.tool_transaction import (
    arguments_sha256,
    canonical_arguments,
)
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
    """Real service: each failing tool closes its started transaction, and 5
    consecutive failures force the executor to terminate with run.failed."""
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

    def _fail(attempt):
        raise RuntimeError(f"boom {attempt}")

    spec = ToolSpec(
        name="FailTool",
        description="always fails",
        parameters={"type": "object", "properties": {"attempt": {"type": "integer"}}},
        func=_fail,
        is_readonly=False,
        is_destructive=False,
    )
    registry = MagicMock()
    registry.get.return_value = spec
    tool_executor = ToolExecutionService()
    executor = NativeAgentExecutor(
        model_client=model_client,
        tool_executor=tool_executor,
        event_bus=EventBus(),
        tools_schema=[],
        tool_registry=registry,
        journal_authority=auth,
        max_iterations=10,
    )
    context = RunContext(session_id="session", user_text="fail")

    executor.run(context, "fail")

    events = auth.read_after(0)
    starts = [event for event in events if event.event_type == "tool.execution.started"]
    failures = [event for event in events if event.event_type == "tool.execution.failed"]
    proposed = [event for event in events if event.event_type == "tool.call.proposed"]
    assert len(starts) == 5
    assert len(failures) == 5
    assert len(proposed) == 5
    for p, s, f in zip(proposed, starts, failures):
        assert s.payload["transaction_id"] == p.payload["transaction_id"]
        assert f.payload["transaction_id"] == p.payload["transaction_id"]
        assert f.payload["idempotency_key"] == p.payload["idempotency_key"]
    assert failures[-1].payload["call_id"] == starts[-1].payload["call_id"]
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


def test_executor_emits_full_lifecycle_events_in_order(tmp_path):
    """Executor + real service emit the §6.6 chain order:
    proposed -> validated -> permission.evaluated(fingerprint) -> started -> completed.
    The executor no longer emits a premature started before execute()."""
    auth = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="r", thread_id="th", turn_id="tu",
    )
    registry = MagicMock()
    registry.get.return_value = _make_write_spec()
    tool_executor = ToolExecutionService()
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
    assert [e.event_type for e in events][:5] == [
        "tool.call.proposed",
        "tool.call.validated",
        "tool.permission.evaluated",
        "tool.execution.started",
        "tool.execution.completed",
    ]
    proposed = events[0].payload
    assert proposed["transaction_id"]
    assert proposed["call_id"] == "c1"
    assert proposed["tool_id"] == "Write"
    assert proposed["tool_version"] == "1"
    assert proposed["canonical_arguments"] == '{"path":"/a"}'
    assert proposed["arguments_sha256"] == arguments_sha256("Write", "1", '{"path":"/a"}')
    assert proposed["side_effect_class"] == "reversible_write"
    assert proposed["idempotency_key"] == derive_idempotency_key(
        tool_id="Write",
        canonical_arguments='{"path":"/a"}',
        side_effect_class="reversible_write",
    )
    assert proposed["preconditions"] == []
    for e in events[1:5]:
        assert e.payload["transaction_id"] == proposed["transaction_id"]
    perm = events[2].payload
    assert perm["decision"] == "allow"
    assert perm["approval_fingerprint"]  # non-empty fingerprint bound to the transaction
    started = events[3].payload
    assert started["tool_id"] == "Write"
    assert started["arguments"] == '{"path":"/a"}'
    completed = events[4].payload
    assert completed["status"] == "succeeded"
    assert completed["idempotency_key"] == proposed["idempotency_key"]
    # started must NOT appear before permission.evaluated (the old premature emission).
    assert events[1].event_type == "tool.call.validated"


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


def test_ask_path_emits_approval_required_with_same_fingerprint(tmp_path):
    """ASK path emits permission.evaluated(ask) then tool.approval.required carrying
    the SAME deterministic approval fingerprint, and returns awaiting_permission."""
    auth = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="r", thread_id="th", turn_id="tu",
    )
    registry = MagicMock()
    registry.get.return_value = _make_write_spec()
    ask_service = MagicMock()
    ask_service.start_ask.return_value = "ask-123"
    svc = ToolExecutionService(
        ask_service=ask_service,
        permission_decision_hook=lambda tool_name, tool_input, sdk_decision, policy: (
            PermissionDecision(behavior=PermissionBehavior.ASK, reason="需要确认")
        ),
    )
    call = ToolCall(id="c1", name="Write", arguments={"path": "/a"})
    canon = canonical_arguments(call.arguments)
    ik = derive_idempotency_key(
        tool_id=call.name, canonical_arguments=canon, side_effect_class="reversible_write",
    )
    ttx = "ttx_ask"
    auth.emit("tool.call.proposed", {
        "transaction_id": ttx, "call_id": call.id, "tool_id": call.name,
        "tool_version": "1", "canonical_arguments": canon,
        "arguments_sha256": arguments_sha256(call.name, "1", canon),
        "side_effect_class": "reversible_write", "idempotency_key": ik, "preconditions": [],
    })

    result = svc.execute(
        call, context=RunContext(session_id="session", user_text="t"),
        registry=registry, journal_authority=auth,
        transaction_id=ttx, idempotency_key=ik, side_effect_class="reversible_write",
        canonical_arguments_str=canon, arguments_sha256=arguments_sha256(call.name, "1", canon),
    )

    assert result.status == "awaiting_permission"
    assert result.metadata["ask_id"] == "ask-123"
    events = auth.read_after(0)
    types = [e.event_type for e in events]
    assert "tool.permission.evaluated" in types
    assert "tool.approval.required" in types
    assert types.index("tool.permission.evaluated") < types.index("tool.approval.required")
    assert "tool.execution.started" not in types  # no side effect started
    perm = [e for e in events if e.event_type == "tool.permission.evaluated"][0].payload
    approval = [e for e in events if e.event_type == "tool.approval.required"][0].payload
    assert perm["decision"] == "ask"
    assert perm["transaction_id"] == ttx
    assert approval["transaction_id"] == ttx
    assert approval["tool_name"] == "Write"
    assert approval["reason"] == "需要确认"
    assert approval["arguments"] == '{"path":"/a"}'
    assert approval["approval_fingerprint"] == perm["approval_fingerprint"]
    assert approval["approval_fingerprint"]  # non-empty, deterministic


def test_find_committed_result_requires_succeeded_status(tmp_path):
    """find_committed_result must not treat a malformed completed event (status !=
    succeeded) as a reusable success."""
    auth = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="r", thread_id="th", turn_id="tu",
    )
    ik = derive_idempotency_key(
        tool_id="Write", canonical_arguments=canonical_arguments({"path": "/f"}),
        side_effect_class="reversible_write",
    )
    # Failed result recorded on tool.execution.completed (malformed) — NOT reusable.
    auth.emit("tool.execution.completed", {
        "transaction_id": "ttx_f", "call_id": "c1", "tool_id": "Write",
        "status": "failed", "result_summary": "boom", "full_ref": "", "artifacts": [],
        "idempotency_key": ik,
    })
    assert find_committed_result(auth, ik) is None
    # Genuine succeeded result — reusable.
    auth.emit("tool.execution.completed", {
        "transaction_id": "ttx_ok", "call_id": "c2", "tool_id": "Write",
        "status": "succeeded", "result_summary": "wrote /f", "full_ref": "ref://f",
        "artifacts": ["art"], "idempotency_key": ik,
    })
    hit = find_committed_result(auth, ik)
    assert hit is not None
    assert hit["result_summary"] == "wrote /f"
    assert hit["artifacts"] == ["art"]
