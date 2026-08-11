"""P7 Task 1 — ChildThreadRuntime typed lifecycle + reducer lineage."""
from unittest.mock import MagicMock

from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.event_bus import EventBus, StepEventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent, RunContext
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.contracts.child_thread import ChildThread, SubagentEventType, SubagentResult
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.reducer import initial_run_state, reduce
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.child_thread_runtime import ChildThreadRuntime
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.sandbox_service import SandboxService


def _event(seq, et, thread_id, payload):
    return EventEnvelope(
        event_id=f"e{seq}", event_type=et, sequence=seq,
        recorded_at="2026-08-11T00:00:00Z", thread_id=thread_id,
        causation_id="", correlation_id="", actor={"type": "subagent"}, payload=payload,
    )


def test_reducer_child_thread_lineage():
    state = initial_run_state("run_1", conversation_id="c", task_id="t", thread_id="th_main")
    state = reduce(state, _event(1, "child_thread.accepted", "th_child", {
        "thread_id": "th_child", "parent_thread_id": "th_main",
        "parent_call_id": "step_1", "session_id": "sub-x",
    }))
    state = reduce(state, _event(2, "child_thread.running", "th_child", {"thread_id": "th_child"}))
    assert state.child_threads == [
        type(state.child_threads[0])(thread_id="th_child", parent_thread_id="th_main",
                                     parent_call_id="step_1", status="running"),
    ]
    state = reduce(state, _event(3, "child_thread.result", "th_child", {
        "thread_id": "th_child", "summary": "done", "artifact_ids": [],
    }))
    assert state.child_threads[0].status == "completed"


def test_reducer_child_thread_terminal_reasons():
    state = initial_run_state("run_1", thread_id="th_main")
    state = reduce(state, _event(1, "child_thread.accepted", "th_child", {
        "thread_id": "th_child", "parent_thread_id": "th_main", "parent_call_id": "s",
    }))
    state = reduce(state, _event(2, "child_thread.failed", "th_child", {
        "thread_id": "th_child", "reason": "quota:max_turns(50/50)",
    }))
    assert state.child_threads[0].status == "failed"
    assert state.child_threads[0].reason == "quota:max_turns(50/50)"
    state = reduce(state, _event(3, "child_thread.cancelled", "th_child", {
        "thread_id": "th_child", "reason": "parent_cancelled",
    }))
    assert state.child_threads[-1].status == "cancelled"


def test_runtime_typed_lifecycle_result(tmp_path):
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [
        ModelEvent(type="token", content="child result here"),
        ModelEvent(type="done"),
    ]
    parent_auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th_main", turn_id="tu_main",
    )
    bg = BackgroundTaskService(base_dir=str(tmp_path / "sessions"))
    rt = ChildThreadRuntime(
        model_client=mc,
        tool_executor=MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=5,
        system_prompts=["test prompt"],
        checkpoint_service=None,
        tracing_service=None,
        background_task_service=bg,
        journal_authority=parent_auth,
        sandbox_service=SandboxService(base_dir=str(tmp_path / "sbx")),
        permission_service=PermissionService(),
        path_service=PathService(),
        artifact_store_root=tmp_path / "artifacts",
        runtime_dir=tmp_path / "runtime",
        tool_runtime_factory=lambda: _stub_registry_and_loader(),
    )
    child = ChildThread(
        thread_id="th_child", parent_thread_id="th_main", parent_call_id="step_1",
    )
    parent_rc = RuntimeContext(
        conversation_id="c", task_id="t", run_id="run_1",
        thread_id="th_main", turn_id="tu_main", actor_type="agent",
        actor_id="main", agent_tier="main", runtime_mode="execution",
    )
    parent_context = RunContext(
        session_id="sess_main", user_text="child task", agent_tier="main",
        runtime_context=parent_rc,
    )
    result = rt.run(child, parent_context)
    assert isinstance(result, SubagentResult)
    assert result.event_type == SubagentEventType.result
    assert result.completed is True
    assert "child result here" in result.summary
    assert result.session_id.startswith("sub-")
    # 生命周期事件顺序：accepted -> running -> result
    types = [e.event_type for e in parent_auth.read_after(0)]
    assert types.index("child_thread.accepted") < types.index("child_thread.running") \
        < types.index("child_thread.result")


def test_runtime_child_executor_uses_trace_scoped_event_bus(tmp_path, monkeypatch):
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [
        ModelEvent(type="token", content="child result here"),
        ModelEvent(type="done"),
    ]
    parent_auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th_main", turn_id="tu_main",
    )
    rt = ChildThreadRuntime(
        model_client=mc,
        tool_executor=MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=5,
        system_prompts=["test prompt"],
        checkpoint_service=None,
        tracing_service=None,
        background_task_service=BackgroundTaskService(base_dir=str(tmp_path / "sessions")),
        journal_authority=parent_auth,
        sandbox_service=SandboxService(base_dir=str(tmp_path / "sbx")),
        permission_service=PermissionService(),
        path_service=PathService(),
        artifact_store_root=tmp_path / "artifacts",
        runtime_dir=tmp_path / "runtime",
        tool_runtime_factory=lambda: _stub_registry_and_loader(),
    )
    captured = {}
    original = rt._build_child_executor

    def capture(child_auth, registry, tool_loader, event_bus):
        captured["event_bus"] = event_bus
        return original(child_auth, registry, tool_loader, event_bus)

    monkeypatch.setattr(rt, "_build_child_executor", capture)
    result = rt.run(
        ChildThread(
            thread_id="th_child", parent_thread_id="th_main", parent_call_id="step_1",
        ),
        RunContext(
            session_id="sess_main", user_text="child task", agent_tier="main",
            runtime_context=RuntimeContext(
                conversation_id="c", task_id="t", run_id="run_1",
                thread_id="th_main", turn_id="tu_main", actor_type="agent",
                actor_id="main", agent_tier="main", runtime_mode="execution",
            ),
        ),
    )

    assert isinstance(captured["event_bus"], StepEventBus)
    assert captured["event_bus"]._trace_session_id == result.session_id


def _stub_registry_and_loader():
    reg = MagicMock()
    reg.tools_schema.return_value = []
    loader = MagicMock()
    return reg, loader
