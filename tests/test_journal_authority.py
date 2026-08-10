from floodmind.agent.runtime.services.journal_authority import (
    open_journal_authority,
    JournalAuthority,
)
from floodmind.agent.runtime.contracts.identity import new_id, is_valid_id
from floodmind.agent.runtime.contracts.run_state import RunStatus


def test_emit_scopes_identity(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_1", task_id="task_1",
                                   run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    ev = auth.emit("thread.message.sent", {"content": "hi", "turn_index": 0})
    assert ev.conversation_id == "conv_1"
    assert ev.run_id == "run_1"
    assert ev.thread_id == "thread_1"
    assert ev.turn_id == "turn_1"
    assert ev.sequence == 1


def test_child_thread_override_scope(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_1", task_id="task_1",
                                   run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    ev = auth.emit("thread.created", {"thread_id": "thread_child"}, thread_id="thread_child")
    assert ev.thread_id == "thread_child"


def test_replay_dedup_by_event_id(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_1", task_id="task_1",
                                   run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    auth.emit("thread.message.sent", {"content": "hi", "turn_index": 0})
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "ok", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    state = auth.replay(after_sequence=0)
    assert state.status == RunStatus.completed
    assert state.token_usage["total_tokens"] == 2
    assert len(state.turns) == 2
    # 幂等重放：同 after_sequence 必须与之前一致
    state2 = auth.replay(after_sequence=0)
    assert state2.model_dump() == state.model_dump()


def test_cursor_read_after(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="r", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "a", "turn_index": 0})
    cur = auth.cursor()
    assert cur == 1
    auth.emit("model.attempt.completed", {"attempt_id": "a", "terminal_reason": "completed",
        "content": "b", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    assert len(auth.read_after(cur)) == 1
