"""P8 Task 2 — SDK 公共事件契约（§4.4/§10.1 双层流，Journal 派生）。"""
from floodmind.agent.sdk_events import SdkEvent, project_canonical, project_canonical_many
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def _mk_auth(tmp_path, run_id="run_1"):
    return open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id=run_id, thread_id="th", turn_id="tu",
    )


def test_project_canonical_maps_committed_events(tmp_path):
    auth = _mk_auth(tmp_path)
    auth.emit("model.attempt.completed", {"content": "final answer", "is_final": True})
    auth.emit("tool.result.committed", {"tool_name": "Bash", "status": "completed"})
    auth.emit("run.completed", {})
    events = project_canonical_many(auth.read_after(0))
    types = [e["type"] for e in events]
    assert "text_committed" in types
    assert "tool_result" in types
    assert "run_completed" in types
    seqs = [e["sequence"] for e in events]
    assert seqs == sorted(seqs) and seqs[-1] == auth.cursor()


def test_project_canonical_skips_internal_events(tmp_path):
    """纯内部事件（usage/checkpoint/attempt.started）不进入公共事件。"""
    auth = _mk_auth(tmp_path)
    auth.emit("model.attempt.started", {})
    auth.emit("model.usage.recorded", {"total_tokens": 10})
    auth.emit("model.attempt.completed", {"content": "ok", "is_final": True})
    events = project_canonical_many(auth.read_after(0))
    assert [e["type"] for e in events] == ["text_committed"]
