from floodmind.agent.runtime.services.checkpoint_service import CheckpointService
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.reducer import initial_run_state


class _StubState:
    def __init__(self, session_id, run_id, checkpoint_id="", iteration=0, status="created"):
        self.session_id = session_id; self.run_id = run_id; self.checkpoint_id = checkpoint_id
        self.iteration = iteration; self.status = status; self.updated_at = None
    def model_dump(self):
        return {"session_id": self.session_id, "run_id": self.run_id,
                "checkpoint_id": self.checkpoint_id, "iteration": self.iteration,
                "status": self.status}


def test_checkpoint_binds_cursor(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "hi", "turn_index": 0})
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "ok", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    svc = CheckpointService(base_dir=str(tmp_path))
    st = _StubState(session_id="sess_1", run_id="run_1")
    record = svc.save(st, journal_cursor=auth.cursor(), reducer_version="1")
    assert record.journal_cursor == 2
    assert record.reducer_version == "1"
    # load 后 state 携带 cursor
    state = svc.load("sess_1", record.checkpoint_id)
    assert state["checkpoint_id"] == record.checkpoint_id
