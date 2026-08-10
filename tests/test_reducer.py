import pytest
from floodmind.agent.runtime.reducer import reduce, initial_run_state
from floodmind.agent.runtime.contracts.run_state import RunStatus
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope, canonical_json
from floodmind.agent.runtime.contracts.identity import new_id


def _ev(event_type: str, sequence: int, payload: dict, **kw) -> EventEnvelope:
    return EventEnvelope(event_id=f"evt_{sequence}", event_type=event_type,
                         sequence=sequence, payload=payload, **kw)


def test_message_sent_appends_turn():
    s = initial_run_state(new_id("run"))
    s = reduce(s, _ev("thread.message.sent", 1, {"content": "hi", "turn_index": 0}))
    assert s.turns[-1] == {"role": "user", "content": "hi", "turn_index": 0, "thread_id": ""}
    assert s.status == RunStatus.awaiting_model


def test_attempt_completed_appends_assistant_turn():
    s = initial_run_state(new_id("run"))
    s = reduce(s, _ev("thread.message.sent", 1, {"content": "hi", "turn_index": 0}))
    s = reduce(s, _ev("model.attempt.completed", 2, {
        "attempt_id": "attempt_1", "terminal_reason": "completed",
        "content": "answer", "reasoning": "think", "tool_calls": [],
        "is_final": True, "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }))
    assert s.turns[-1]["role"] == "assistant"
    assert s.turns[-1]["content"] == "answer"
    assert s.token_usage["total_tokens"] == 15
    assert s.status == RunStatus.completed


def test_tool_transaction_lifecycle():
    s = initial_run_state(new_id("run"))
    s = reduce(s, _ev("tool.execution.started", 1, {
        "transaction_id": "ttx_1", "call_id": "call_1", "tool_id": "builtin:Read", "arguments": "{}",
    }))
    assert s.pending_tool_transactions[0].status == "running"
    s = reduce(s, _ev("tool.execution.completed", 2, {
        "transaction_id": "ttx_1", "call_id": "call_1", "tool_id": "builtin:Read",
        "status": "succeeded", "result_summary": "ok", "full_ref": "", "artifacts": [],
    }))
    assert s.pending_tool_transactions == []
    assert s.artifacts == []
    assert s.turns[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "tool_id": "builtin:Read",
        "content": "ok",
        "turn_index": 0,
        "thread_id": "",
    }


def test_tool_failed_appends_payload_derived_tool_turn():
    s = initial_run_state("run_r")
    s = reduce(s, _ev("tool.execution.started", 1, {
        "transaction_id": "ttx_1", "call_id": "call_1", "tool_id": "builtin:Read",
    }))
    s = reduce(s, _ev("tool.execution.failed", 2, {
        "transaction_id": "ttx_1", "call_id": "call_1", "tool_id": "builtin:Read",
        "status": "error", "result_summary": "read failed", "artifacts": [],
    }))
    assert s.turns[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "tool_id": "builtin:Read",
        "content": "read failed",
        "turn_index": 0,
        "thread_id": "",
    }


def test_replay_determinism():
    # 覆盖 model.attempt.started 的带/不带 attempt_id 两条路径（无随机 ID 分支）。
    events = [
        _ev("thread.message.sent", 1, {"content": "hi", "turn_index": 0}),
        _ev("model.attempt.started", 2, {"attempt_id": "a1"}),
        _ev("model.attempt.completed", 3, {"attempt_id": "a1", "terminal_reason": "tool_calls",
            "content": "", "reasoning": "", "tool_calls": [], "is_final": False, "usage": {}}),
        _ev("tool.execution.started", 4, {"transaction_id": "ttx_1", "call_id": "c1",
            "tool_id": "builtin:Read", "arguments": "{}"}),
        _ev("tool.execution.completed", 5, {"transaction_id": "ttx_1", "call_id": "c1",
            "tool_id": "builtin:Read", "status": "succeeded", "result_summary": "r", "full_ref": "", "artifacts": []}),
        _ev("model.attempt.started", 6, {}),  # 无 attempt_id → 确定性空串
        _ev("model.attempt.completed", 7, {"attempt_id": "a2", "terminal_reason": "completed",
            "content": "done", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {"total_tokens": 3}}),
        _ev("run.completed", 8, {"final_output": "done", "terminal_reason": "completed"}),
    ]
    s1 = initial_run_state("run_r")
    s2 = initial_run_state("run_r")
    for e in events:
        s1_before = s1
        s2_before = s2
        pre1 = canonical_json(s1_before.model_dump())
        pre2 = canonical_json(s2_before.model_dump())
        s1 = reduce(s1, e)
        s2 = reduce(s2, e)
        assert canonical_json(s1.model_dump()) == canonical_json(s2.model_dump())
        # 输入不可变：reduce 不得修改入参对象
        assert canonical_json(s1_before.model_dump()) == pre1
        assert canonical_json(s2_before.model_dump()) == pre2


def test_duplicate_event_id_does_not_double_apply():
    # reducer 层按 event_id 去重：同一 EventEnvelope 对象应用两次，结果与一次相同。
    s = initial_run_state("run_r")
    ev = _ev("model.attempt.completed", 1, {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "x", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})
    once = reduce(s, ev)
    twice = reduce(once, ev)
    assert canonical_json(twice.model_dump()) == canonical_json(once.model_dump())
    assert len(twice.turns) == len(once.turns) == 1
    assert twice.token_usage["total_tokens"] == 10
    assert twice.last_committed_sequence == 1


def _fold(setup_events, final_event):
    """应用一串 setup 事件后应用 final 事件，返回最终状态。"""
    s = initial_run_state("run_r")
    seq = 0
    for et, payload in setup_events:
        seq += 1
        s = reduce(s, _ev(et, seq, payload))
    seq += 1
    return reduce(s, _ev(final_event[0], seq, final_event[1]))


@pytest.mark.parametrize("setup,event_type,payload,expected_status", [
    ([], "model.attempt.failed", {"attempt_id": "a1", "reason": "boom"}, RunStatus.failed),
    ([("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1", "tool_id": "builtin:Read"})],
     "tool.execution.failed", {"transaction_id": "ttx_1", "call_id": "c1", "tool_id": "builtin:Read"},
     RunStatus.awaiting_model),
    ([], "tool.approval.requested", {"ask_id": "ask_1", "call_id": "c1", "tool_name": "Bash"},
     RunStatus.awaiting_approval),
    ([("tool.approval.requested", {"ask_id": "ask_1", "call_id": "c1", "tool_name": "Bash"})],
     "tool.approval.resolved", {"ask_id": "ask_1"}, RunStatus.awaiting_model),
    ([], "context.compaction.started", {}, RunStatus.compacting),
    ([], "context.compaction.completed", {}, RunStatus.awaiting_model),
    ([], "run.failed", {"terminal_reason": "boom"}, RunStatus.failed),
])
def test_status_transitions(setup, event_type, payload, expected_status):
    s = _fold(setup, (event_type, payload))
    assert s.status == expected_status


def test_tool_failed_clears_pending_transaction():
    s = _fold(
        [("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1", "tool_id": "builtin:Read"})],
        ("tool.execution.failed", {"transaction_id": "ttx_1", "call_id": "c1", "tool_id": "builtin:Read"}),
    )
    assert s.pending_tool_transactions == []
    assert s.status == RunStatus.awaiting_model


def test_approval_requested_adds_pending():
    s = _fold([], ("tool.approval.requested", {"ask_id": "ask_1", "call_id": "c1", "tool_name": "Bash"}))
    assert len(s.pending_approvals) == 1
    assert s.pending_approvals[0].ask_id == "ask_1"
    assert s.status == RunStatus.awaiting_approval


def test_approval_resolved_removes_pending():
    s = _fold(
        [("tool.approval.requested", {"ask_id": "ask_1", "call_id": "c1", "tool_name": "Bash"})],
        ("tool.approval.resolved", {"ask_id": "ask_1"}),
    )
    assert s.pending_approvals == []
    assert s.status == RunStatus.awaiting_model


def test_thread_spawn_only_advances_cursor():
    s = initial_run_state("run_r")
    out = reduce(s, _ev("thread.spawn.requested", 1, {
        "thread_id": "thread_child", "parent_call_id": "call_1",
    }))
    assert out.child_threads == []
    assert out.last_committed_sequence == 1


def test_thread_created_records_child():
    s = initial_run_state("run_r")
    out = reduce(s, _ev("thread.created", 1, {
        "thread_id": "thread_child", "parent_call_id": "call_1",
    }))
    assert [ct.model_dump() for ct in out.child_threads] == [{
        "thread_id": "thread_child", "parent_call_id": "call_1", "status": "running",
    }]


@pytest.mark.parametrize("event_type,expected_status", [
    ("thread.completed", "completed"),
    ("thread.failed", "failed"),
    ("thread.cancelled", "cancelled"),
])
def test_thread_terminal_updates_matching_child(event_type, expected_status):
    s = initial_run_state("run_r")
    s = reduce(s, _ev("thread.created", 1, {
        "thread_id": "thread_child", "parent_call_id": "call_1",
    }))
    s = reduce(s, _ev("thread.created", 2, {
        "thread_id": "thread_other", "parent_call_id": "call_2",
    }))
    out = reduce(s, _ev(event_type, 3, {"thread_id": "thread_child"}))
    assert [(ct.thread_id, ct.status) for ct in out.child_threads] == [
        ("thread_child", expected_status), ("thread_other", "running"),
    ]


def test_two_thread_reduction_is_deterministic_and_scoped():
    events = [
        _ev("thread.message.sent", 1, {"content": "parent", "turn_index": 0}, thread_id="parent"),
        _ev("model.attempt.completed", 2, {
            "terminal_reason": "tool_calls", "content": "", "tool_calls": [{"id": "call"}],
            "is_final": False, "usage": {"total_tokens": 2},
        }, thread_id="parent"),
        _ev("thread.message.sent", 3, {"content": "child", "turn_index": 0}, thread_id="child"),
        _ev("model.attempt.completed", 4, {
            "terminal_reason": "completed", "content": "child done", "tool_calls": [],
            "is_final": True, "usage": {"total_tokens": 3},
        }, thread_id="child"),
        _ev("run.completed", 5, {}, thread_id="child"),
    ]
    states = []
    for thread_id in ("parent", "child"):
        first = initial_run_state("run_r", thread_id=thread_id)
        second = initial_run_state("run_r", thread_id=thread_id)
        for event in events:
            first = reduce(first, event)
            second = reduce(second, event)
        assert canonical_json(first.model_dump()) == canonical_json(second.model_dump())
        states.append(first)

    parent, child = states
    assert [turn["content"] for turn in parent.turns] == ["parent", ""]
    assert parent.status == RunStatus.awaiting_tool
    assert [turn["content"] for turn in child.turns] == ["child", "child done"]
    assert child.status == RunStatus.completed
    assert parent.token_usage["total_tokens"] == child.token_usage["total_tokens"] == 5


def test_unknown_event_fail_closed():
    s = initial_run_state("run_r")
    s = reduce(s, _ev("thread.message.sent", 1, {"content": "hi", "turn_index": 0}))
    turns_before = list(s.turns)
    status_before = s.status
    s = reduce(s, _ev("some.future.event", 2, {"anything": 1}))
    assert s.turns == turns_before
    assert s.status == status_before
    assert s.last_committed_sequence == 2  # 未知事件仅推进 cursor


def test_cursor_advances_with_event_sequence():
    s = initial_run_state("run_r")
    assert s.last_committed_sequence == 0
    s = reduce(s, _ev("thread.message.sent", 7, {"content": "hi", "turn_index": 0}))
    assert s.last_committed_sequence == 7


def test_token_accumulation_separate():
    s = initial_run_state("run_r")
    s = reduce(s, _ev("model.attempt.completed", 1, {"attempt_id": "a1", "terminal_reason": "tool_calls",
        "content": "", "reasoning": "", "tool_calls": [], "is_final": False,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}))
    s = reduce(s, _ev("model.attempt.completed", 2, {"attempt_id": "a2", "terminal_reason": "completed",
        "content": "done", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}))
    assert s.token_usage["prompt_tokens"] == 13
    assert s.token_usage["completion_tokens"] == 9
    assert s.token_usage["total_tokens"] == 22


def _dump_without_bookkeeping(state):
    d = state.model_dump()
    d.pop("last_committed_sequence", None)
    d.pop("processed_event_ids", None)
    return canonical_json(d)


@pytest.mark.parametrize("setup,event_type,payload,kind", [
    # 正常分支：状态改变
    ([], "thread.message.sent", {"content": "hi", "turn_index": 0}, "changed"),
    ([], "model.attempt.started", {"attempt_id": "a1"}, "changed"),
    ([], "model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "x", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}, "changed"),
    ([], "model.attempt.failed", {"attempt_id": "a1", "reason": "boom"}, "changed"),
    ([], "tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1", "tool_id": "builtin:Read"}, "changed"),
    ([("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1", "tool_id": "builtin:Read"})],
     "tool.execution.completed", {"transaction_id": "ttx_1", "call_id": "c1", "tool_id": "builtin:Read", "artifacts": []}, "changed"),
    ([("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1", "tool_id": "builtin:Read"})],
     "tool.execution.failed", {"transaction_id": "ttx_1", "call_id": "c1", "tool_id": "builtin:Read"}, "changed"),
    ([], "tool.approval.requested", {"ask_id": "ask_1", "call_id": "c1", "tool_name": "Bash"}, "changed"),
    ([("tool.approval.requested", {"ask_id": "ask_1", "call_id": "c1", "tool_name": "Bash"})],
     "tool.approval.resolved", {"ask_id": "ask_1"}, "changed"),
    ([], "context.compaction.started", {}, "changed"),
    ([], "context.compaction.completed", {}, "changed"),
    ([], "run.completed", {"final_output": "done", "terminal_reason": "completed"}, "changed"),
    ([], "run.failed", {"terminal_reason": "boom"}, "changed"),
    # 未知事件：fail-closed，仅推进 cursor
    ([], "some.future.event", {"anything": 1}, "cursor_only"),
])
def test_reduce_does_not_mutate_input_on_every_path(setup, event_type, payload, kind):
    s = initial_run_state("run_r")
    seq = 0
    for et, p in setup:
        seq += 1
        s = reduce(s, _ev(et, seq, p))
    seq += 1
    target = _ev(event_type, seq, payload)
    input_obj = s
    before = canonical_json(input_obj.model_dump())
    out = reduce(s, target)
    # 输入不可变：reduce 不得修改入参对象
    assert canonical_json(input_obj.model_dump()) == before
    if kind == "changed":
        assert canonical_json(out.model_dump()) != before
    elif kind == "cursor_only":
        # fail-closed：领域状态不变，仅推进 bookkeeping（cursor + processed_event_ids）
        assert out.last_committed_sequence == seq
        assert _dump_without_bookkeeping(out) == _dump_without_bookkeeping(input_obj)
    else:  # unchanged（duplicate）
        assert canonical_json(out.model_dump()) == before


def test_reduce_does_not_mutate_input_on_duplicate():
    s = initial_run_state("run_r")
    ev = _ev("model.attempt.completed", 1, {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "x", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})
    s = reduce(s, ev)
    input_obj = s
    before = canonical_json(input_obj.model_dump())
    out = reduce(s, ev)  # 重复 event_id：不重放、不推进 cursor、不修改输入
    assert canonical_json(input_obj.model_dump()) == before
    assert canonical_json(out.model_dump()) == before
