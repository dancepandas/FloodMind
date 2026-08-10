from types import SimpleNamespace

import pytest

from floodmind.agent.native.executor import project_run_state_to_loop_state
from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.agent.native.types import AgentLoopState, RunContext
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.history_projection import project_conversation, project_current
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.reducer import initial_run_state, reduce


def test_thread_dirs_are_isolated_under_child_thread(tmp_path):
    from floodmind.agent.runtime.services.runtime_layout import thread_dirs

    dirs = thread_dirs(tmp_path, "c", "t", "run_1", "thread_child")
    base = tmp_path / "conversations" / "c" / "tasks" / "t" / "runs" / "run_1" / "threads" / "thread_child"
    assert dirs == {
        "thread_dir": base,
        "state_dir": base / "state",
        "tmp_dir": base / "tmp",
        "scripts_dir": base / "scripts",
    }


def test_child_thread_events_scoped(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="thread_main", turn_id="tu")
    auth.emit("thread.spawn.requested", {"thread_id": "thread_child", "parent_call_id": "call_1"})
    auth.emit("thread.created", {"thread_id": "thread_child", "parent_call_id": "call_1"})
    auth.emit("thread.completed", {"thread_id": "thread_child", "parent_call_id": "call_1",
        "summary": "done", "artifact_ids": ["art_1"]}, thread_id="thread_child")
    events = auth.read_after(0)
    child_evs = [e for e in events if e.thread_id == "thread_child"]
    assert len(child_evs) == 1  # 只有 thread.completed 用 child scope 覆盖
    # reducer 记录 child_threads
    s = initial_run_state("run_1")
    for e in events:
        s = reduce(s, e)
    assert any(ct.thread_id == "thread_child" for ct in s.child_threads)


def _completed(auth, content):
    auth.emit("model.attempt.completed", {
        "attempt_id": "a", "terminal_reason": "completed", "content": content,
        "reasoning": "", "tool_calls": [], "is_final": True, "usage": {},
    })


def test_turn_replay_and_loop_projection_are_thread_scoped(tmp_path):
    parent = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="thread_parent", turn_id="turn_parent",
    )
    parent.emit("thread.message.sent", {"content": "parent question", "turn_index": 0})
    _completed(parent, "parent answer")
    parent.emit("thread.spawn.requested", {"thread_id": "thread_child", "parent_call_id": "call"})
    parent.emit("thread.created", {"thread_id": "thread_child", "parent_call_id": "call"})
    child = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="thread_child", turn_id="turn_child",
    )
    child.emit("thread.message.sent", {"content": "child question", "turn_index": 0})
    _completed(child, "child answer")
    child.emit("tool.execution.completed", {
        "transaction_id": "tx", "call_id": "call-tool", "tool_id": "builtin:Read",
        "status": "succeeded", "result_summary": "child tool", "artifacts": [],
    })
    child.emit("thread.completed", {"thread_id": "thread_child", "parent_call_id": "call"})

    assert {t["thread_id"] for t in parent.replay().turns} == {"thread_parent"}
    assert {t["thread_id"] for t in child.replay().turns} == {"thread_child"}

    seeded = AgentLoopState(messages=[
        {"role": "system", "content": "specialist"},
        {"role": "user", "content": "seeded child prompt"},
    ])
    empty_child = initial_run_state("run_1", thread_id="thread_child")
    assert project_run_state_to_loop_state(seeded, empty_child).messages == seeded.messages
    child_state = child.replay()
    projected = project_run_state_to_loop_state(seeded, child_state)
    assert projected.iteration == 1
    assert [m["content"] for m in projected.messages if m["role"] != "system"] == [
        "child question", "child answer", "child tool",
    ]


def test_history_projections_exclude_child_turns(tmp_path):
    parent = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="thread_parent", turn_id="turn_parent",
    )
    parent.emit("thread.message.sent", {"content": "parent question", "turn_index": 0})
    _completed(parent, "parent answer")
    parent.emit("thread.created", {"thread_id": "thread_child", "parent_call_id": "call"})
    child = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="thread_child", turn_id="turn_child",
    )
    child.emit("thread.message.sent", {"content": "child question", "turn_index": 0})
    _completed(child, "child answer")

    assert [t["content"] for t in project_current(parent)] == ["parent question", "parent answer"]
    assert [t["content"] for t in project_conversation(tmp_path, "c")] == [
        "parent question", "parent answer",
    ]


def test_specialist_preparation_failure_emits_failed_terminal(tmp_path, monkeypatch):
    parent = open_journal_authority(
        tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="thread_parent", turn_id="turn_parent",
    )
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent._journal_authority = parent
    agent._build_specialist_user_input = lambda *_: (_ for _ in ()).throw(RuntimeError("prepare failed"))
    agent._background_task_service = SimpleNamespace(kill_session=lambda _session: 0)
    agent._sandbox_service = SimpleNamespace(destroy=lambda _ctx: None)
    context = RunContext(
        session_id="parent", user_text="task", state_dir="",
        runtime_context=RuntimeContext(
            conversation_id="c", task_id="t", run_id="run_1",
            thread_id="thread_parent", turn_id="turn_parent",
        ),
    )

    with pytest.raises(RuntimeError, match="prepare failed"):
        agent._run_specialist_task("task", "", context, "call")

    events = parent.read_after(0)
    assert [e.event_type for e in events] == [
        "thread.spawn.requested", "thread.created", "thread.failed",
    ]
    assert all(e.run_id == "run_1" for e in events)
