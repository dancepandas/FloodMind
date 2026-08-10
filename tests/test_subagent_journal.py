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
