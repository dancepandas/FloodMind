from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.contracts.run_state import RunStatus
from floodmind.agent.runtime.contracts.tool_transaction import ToolStatus
from floodmind.agent.runtime.reducer import initial_run_state, reduce
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def _evt(auth, event_type, payload, **scope):
    return auth.new_envelope(event_type, payload, **scope)


def test_full_lifecycle_to_succeeded(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    tx = "ttx_1"
    events = [
        _evt(auth, "tool.call.proposed", {"transaction_id": tx, "call_id": "call_1",
            "tool_id": "builtin:Write", "arguments_sha256": "h", "idempotency_key": "ik"}),
        _evt(auth, "tool.call.validated", {"transaction_id": tx, "call_id": "call_1"}),
        _evt(auth, "tool.permission.evaluated", {"transaction_id": tx, "call_id": "call_1",
            "decision": "allow", "approval_fingerprint": ""}),
        _evt(auth, "tool.execution.started", {"transaction_id": tx, "call_id": "call_1",
            "tool_id": "builtin:Write", "arguments": "{}"}),
        _evt(auth, "tool.execution.completed", {"transaction_id": tx, "call_id": "call_1",
            "tool_id": "builtin:Write", "status": "succeeded",
            "result_summary": "ok", "full_ref": "", "artifacts": [], "idempotency_key": "ik"}),
    ]
    s = initial_run_state("run_1", thread_id="th")
    for e in events:
        s = reduce(s, e)
    assert s.pending_tool_transactions == []  # 终态移出
    assert any(t["role"] == "tool" for t in s.turns)
    assert s.status == RunStatus.awaiting_model


def test_indeterminate_stays_pending_then_result_committed_removes(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    tx = "ttx_2"
    events = [
        _evt(auth, "tool.call.proposed", {"transaction_id": tx, "call_id": "call_2",
            "tool_id": "builtin:Bash", "arguments_sha256": "h", "idempotency_key": "ik"}),
        _evt(auth, "tool.execution.started", {"transaction_id": tx, "call_id": "call_2",
            "tool_id": "builtin:Bash", "arguments": "{}"}),
        _evt(auth, "tool.execution.indeterminate", {"transaction_id": tx, "call_id": "call_2",
            "tool_id": "builtin:Bash", "reason": "timeout", "idempotency_key": "ik"}),
    ]
    s = initial_run_state("run_1", thread_id="th")
    for e in events:
        s = reduce(s, e)
    assert len(s.pending_tool_transactions) == 1
    assert s.pending_tool_transactions[0].status == ToolStatus.indeterminate
    # 幂等：replay 相同事件不重复副作用
    s2 = initial_run_state("run_1", thread_id="th")
    for e in events:
        s2 = reduce(s2, e)
    assert s.model_dump() == s2.model_dump()
    # result.committed 落定并移出
    s3 = reduce(s, _evt(auth, "tool.result.committed",
        {"transaction_id": tx, "call_id": "call_2", "tool_id": "builtin:Bash",
         "result_ref": "art_1", "verdict": "succeeded"}))
    assert s3.pending_tool_transactions == []


def test_permission_allow_walks_chain_and_records_fingerprint(tmp_path):
    """F1：permission.evaluated(allow) 沿 §6.4 链推进并记录 approval fingerprint。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    tx = "ttx_f1"
    events = [
        _evt(auth, "tool.call.proposed", {"transaction_id": tx, "call_id": "call_f1",
            "tool_id": "builtin:Write", "idempotency_key": "ik"}),
        _evt(auth, "tool.call.validated", {"transaction_id": tx, "call_id": "call_f1"}),
        _evt(auth, "tool.permission.evaluated", {"transaction_id": tx, "call_id": "call_f1",
            "decision": "allow", "approval_fingerprint": "fp_x"}),
    ]
    s = initial_run_state("run_1", thread_id="th")
    for e in events:
        s = reduce(s, e)
    assert len(s.pending_tool_transactions) == 1
    t = s.pending_tool_transactions[0]
    assert t.status == ToolStatus.approved
    assert t.permission_fingerprint == "fp_x"


def test_stale_started_after_indeterminate_is_noop(tmp_path):
    """F2：过期的 tool.execution.started 不得把 indeterminate 翻回 running。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    tx = "ttx_f2"
    events = [
        _evt(auth, "tool.call.proposed", {"transaction_id": tx, "call_id": "call_f2",
            "tool_id": "builtin:Bash", "idempotency_key": "ik"}),
        _evt(auth, "tool.execution.started", {"transaction_id": tx, "call_id": "call_f2",
            "tool_id": "builtin:Bash", "arguments": "{}"}),
        _evt(auth, "tool.execution.indeterminate", {"transaction_id": tx, "call_id": "call_f2",
            "tool_id": "builtin:Bash", "reason": "timeout", "idempotency_key": "ik"}),
    ]
    s = initial_run_state("run_1", thread_id="th")
    for e in events:
        s = reduce(s, e)
    assert s.pending_tool_transactions[0].status == ToolStatus.indeterminate
    s = reduce(s, _evt(auth, "tool.execution.started", {"transaction_id": tx,
        "call_id": "call_f2", "tool_id": "builtin:Bash", "arguments": "{}"}))
    assert len(s.pending_tool_transactions) == 1
    assert s.pending_tool_transactions[0].status == ToolStatus.indeterminate


def test_permission_deny_removes_transaction(tmp_path):
    """permission.evaluated(deny) 终态移出 pending。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    tx = "ttx_f3"
    events = [
        _evt(auth, "tool.call.proposed", {"transaction_id": tx, "call_id": "call_f3",
            "tool_id": "builtin:Write", "idempotency_key": "ik"}),
        _evt(auth, "tool.permission.evaluated", {"transaction_id": tx, "call_id": "call_f3",
            "decision": "deny", "approval_fingerprint": ""}),
    ]
    s = initial_run_state("run_1", thread_id="th")
    for e in events:
        s = reduce(s, e)
    assert s.pending_tool_transactions == []


def test_proposed_bad_input_fails_closed(tmp_path):
    """F6/F7：缺 transaction_id 不建 pending；garbage side_effect_class 不抛异常。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    s0 = initial_run_state("run_1", thread_id="th")
    s0 = reduce(s0, _evt(auth, "tool.call.proposed", {"call_id": "c", "tool_id": "builtin:Read"}))
    assert s0.pending_tool_transactions == []
    s1 = initial_run_state("run_1", thread_id="th")
    s1 = reduce(s1, _evt(auth, "tool.call.proposed", {"transaction_id": "ttx_g",
        "call_id": "c", "tool_id": "builtin:Read", "side_effect_class": "not-a-class"}))
    assert len(s1.pending_tool_transactions) == 1
    assert s1.pending_tool_transactions[0].side_effect_class.value == "read"
