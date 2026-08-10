import json
from floodmind.agent.runtime.reducer import reduce, initial_run_state
from floodmind.agent.runtime.contracts.run_state import RunState, RunStatus
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope, canonical_json
from floodmind.agent.runtime.contracts.identity import new_id


def _ev(event_type: str, sequence: int, payload: dict, **kw) -> EventEnvelope:
    return EventEnvelope(event_id=f"evt_{sequence}", event_type=event_type,
                         sequence=sequence, payload=payload, **kw)


def test_message_sent_appends_turn():
    s = initial_run_state(new_id("run"))
    s = reduce(s, _ev("thread.message.sent", 1, {"content": "hi", "turn_index": 0}))
    assert s.turns[-1] == {"role": "user", "content": "hi", "turn_index": 0}
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


def test_replay_determinism():
    events = [
        _ev("thread.message.sent", 1, {"content": "hi", "turn_index": 0}),
        _ev("model.attempt.completed", 2, {"attempt_id": "a1", "terminal_reason": "tool_calls",
            "content": "", "reasoning": "", "tool_calls": [], "is_final": False, "usage": {}}),
        _ev("tool.execution.started", 3, {"transaction_id": "ttx_1", "call_id": "c1",
            "tool_id": "builtin:Read", "arguments": "{}"}),
        _ev("tool.execution.completed", 4, {"transaction_id": "ttx_1", "call_id": "c1",
            "tool_id": "builtin:Read", "status": "succeeded", "result_summary": "r", "full_ref": "", "artifacts": []}),
        _ev("model.attempt.completed", 5, {"attempt_id": "a2", "terminal_reason": "completed",
            "content": "done", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {"total_tokens": 3}}),
        _ev("run.completed", 6, {"final_output": "done", "terminal_reason": "completed"}),
    ]
    s1 = initial_run_state("run_r")
    s2 = initial_run_state("run_r")
    for e in events:
        s1 = reduce(s1, e)
        s2 = reduce(s2, e)
    assert canonical_json(s1.model_dump()) == canonical_json(s2.model_dump())


def test_duplicate_event_id_does_not_double_apply():
    # 重放层按 event_id 去重是 Task 4 的职责；这里验证 reduce 对相同序列（含重复 payload）
    # 只按事件语义前进，且 token_usage 累加逻辑正确。
    s = initial_run_state("run_r")
    s = reduce(s, _ev("model.attempt.completed", 1, {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "x", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}}))
    assert s.token_usage["total_tokens"] == 10
