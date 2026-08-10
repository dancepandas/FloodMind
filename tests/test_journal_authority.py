from floodmind.agent.runtime.services.journal_authority import (
    open_journal_authority,
    JournalAuthority,
)
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope, Actor, utcnow
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
    # 身份契约：各 id 字段符合 new_id 前缀规范
    assert is_valid_id("conversation", ev.conversation_id)
    assert is_valid_id("task", ev.task_id)
    assert is_valid_id("run", ev.run_id)
    assert is_valid_id("thread", ev.thread_id)
    assert is_valid_id("turn", ev.turn_id)
    # 默认身份：attempt/call 为空，actor 为 system
    assert ev.attempt_id == ""
    assert ev.call_id == ""
    assert ev.actor.type == "system"
    assert ev.actor.id == ""


def test_scope_overrides_and_actor(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_1", task_id="task_1",
                                   run_id="run_1", thread_id="thread_1", turn_id="turn_1",
                                   attempt_id="attempt_1")
    call_id = new_id("call")
    ev = auth.emit("model.attempt.completed",
                   {"attempt_id": "attempt_1", "terminal_reason": "completed",
                    "content": "ok", "reasoning": "", "tool_calls": [], "is_final": True,
                    "usage": {}},
                   actor_type="model", actor_id="model_1", call_id=call_id)
    assert ev.task_id == "task_1"
    assert ev.attempt_id == "attempt_1"
    assert ev.call_id == call_id
    assert ev.actor.type == "model"
    assert ev.actor.id == "model_1"


def test_none_scope_overrides_default(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_1", task_id="task_1",
                                   run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    ev = auth.emit("thread.message.sent", {"content": "hi", "turn_index": 0},
                   thread_id=None, turn_id=None, attempt_id=None, call_id=None)
    # 显式 None 视为未提供：回落权威身份默认值，而不是校验失败
    assert ev.thread_id == "thread_1"
    assert ev.turn_id == "turn_1"
    assert ev.attempt_id == ""
    assert ev.call_id == ""


def test_child_thread_override_scope(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_1", task_id="task_1",
                                   run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    ev = auth.emit("thread.created", {"thread_id": "thread_child"}, thread_id="thread_child")
    assert ev.thread_id == "thread_child"


class _FakeWriter:
    """受控 writer：仅 read_from 需要真实行为，用于注入 replay 重复事件。"""

    def __init__(self, events):
        self._events = events

    def current_sequence(self) -> int:
        return max((e.sequence for e in self._events), default=0)

    def append(self, event):
        return event

    def append_many(self, events):
        return events

    def read_from(self, after_sequence=0):
        return [e for e in self._events if e.sequence > after_sequence]


def test_replay_dedup_by_event_id():
    shared_id = "evt_shared_dup"
    usage = {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}

    def make_event(seq):
        return EventEnvelope(
            event_id=shared_id,
            event_type="model.attempt.completed",
            sequence=seq,
            conversation_id="conv_1", task_id="task_1", run_id="run_1",
            thread_id="thread_1", turn_id="turn_1",
            actor=Actor(type="model", id="model_1"),
            payload={"attempt_id": "a1", "terminal_reason": "completed",
                     "content": "ok", "reasoning": "", "tool_calls": [],
                     "is_final": True, "usage": usage},
            recorded_at=utcnow(),
        )

    fake = _FakeWriter([make_event(1), make_event(2)])  # 同一 event_id 出现在两个 sequence
    auth = JournalAuthority(writer=fake, conversation_id="conv_1", task_id="task_1",
                            run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    state = auth.replay(after_sequence=0)
    # 重复 event_id 只应用一次副作用
    assert state.token_usage["total_tokens"] == 5
    assert sum(1 for t in state.turns if t["role"] == "assistant") == 1
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


def test_new_envelope_ids_unique(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_c", task_id="task_t",
                                   run_id="run_r", thread_id="thread_th", turn_id="turn_tu")
    e1 = auth.new_envelope("thread.message.sent", {"content": "a", "turn_index": 0})
    e2 = auth.new_envelope("thread.message.sent", {"content": "a", "turn_index": 0})
    assert e1.event_id != e2.event_id
    assert is_valid_id("conversation", e1.conversation_id)
    assert is_valid_id("task", e1.task_id)
    assert is_valid_id("run", e1.run_id)


def test_append_group_persists_consecutive_sequences(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_c", task_id="task_t",
                                   run_id="run_r", thread_id="thread_th", turn_id="turn_tu")
    e1 = auth.new_envelope("thread.message.sent", {"content": "a", "turn_index": 0})
    e2 = auth.new_envelope("thread.message.sent", {"content": "b", "turn_index": 1})
    sealed = auth.append_group([e1, e2])
    # append_many 按输入顺序返回已封存信封，sequence 连续
    assert [e.sequence for e in sealed] == [1, 2]
    assert auth.cursor() == 2
    assert [e.event_id for e in auth.read_after(0)] == [e.event_id for e in sealed]
