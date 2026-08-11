"""P7 Task 3 — parent cancellation tree propagation and verified cleanup."""
import sys
import threading
import time
from unittest.mock import MagicMock

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent, RunContext
from floodmind.agent.runtime.contracts.child_thread import ChildThread, SubagentEventType
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.child_thread_runtime import ChildThreadRuntime
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.sandbox_service import SandboxService


def test_parent_cancel_propagates_to_child_and_verifies_cleanup(tmp_path):
    cancel = threading.Event()
    mc = MagicMock(spec=ModelClient)

    def stream(*args, **kwargs):
        while not kwargs["abort_check"]():
            time.sleep(0.01)
        return [ModelEvent(type="done")]

    mc.stream_chat.side_effect = stream
    runtime, bg, parent_auth = _runtime(tmp_path, mc)
    timer = threading.Timer(0.05, cancel.set)
    timer.start()
    try:
        result = runtime.run(_child(), _ctx("sess_main", abort_check=cancel.is_set))
    finally:
        timer.cancel()

    assert result.event_type == SubagentEventType.cancelled
    assert result.reason == "parent_cancelled"
    assert not bg.has_active(result.session_id)
    assert "child_thread.cancelled" in [e.event_type for e in parent_auth.read_after(0)]


def test_child_bg_tasks_killed_and_verified_on_cancel(tmp_path):
    mc = MagicMock(spec=ModelClient)
    runtime, bg, parent_auth = _runtime(tmp_path, mc)
    started = {}

    def stream(*args, **kwargs):
        accepted = next(
            e for e in parent_auth.read_after(0)
            if e.event_type == "child_thread.accepted"
        )
        child_session_id = accepted.payload["session_id"]
        started["task"] = bg.start(
            child_session_id,
            "sleep",
            _sleep_cmd(30),
            cwd=str(tmp_path),
        )
        return [ModelEvent(type="token", content="ok"), ModelEvent(type="done")]

    mc.stream_chat.side_effect = stream
    result = runtime.run(_child(), _ctx("sess_main"))

    assert not bg.has_active(result.session_id)
    assert started["task"].status in ("killed", "completed")


def _runtime(tmp_path, model_client):
    parent_auth = open_journal_authority(
        tmp_path / "runtime",
        conversation_id="c",
        task_id="t",
        run_id="run_1",
        thread_id="th_main",
        turn_id="tu_main",
    )
    bg = BackgroundTaskService(base_dir=str(tmp_path / "sessions"))
    runtime = ChildThreadRuntime(
        model_client=model_client,
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
        tool_runtime_factory=lambda: (_reg(), _loader()),
    )
    return runtime, bg, parent_auth


def _sleep_cmd(seconds):
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _ctx(session_id, abort_check=None):
    return RunContext(
        session_id=session_id,
        user_text="child task",
        agent_tier="main",
        abort_check=abort_check,
        runtime_context=RuntimeContext(
            conversation_id="c",
            task_id="t",
            run_id="run_1",
            thread_id="th_main",
            turn_id="tu_main",
            actor_type="agent",
            actor_id="main",
            agent_tier="main",
            runtime_mode="execution",
        ),
    )


def _child():
    return ChildThread(
        thread_id="th_child",
        parent_thread_id="th_main",
        parent_call_id="s",
    )


def _reg():
    registry = MagicMock()
    registry.tools_schema.return_value = []
    return registry


def _loader():
    return MagicMock()
