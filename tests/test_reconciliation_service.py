"""ReconciliationService 测试（目标 §16.4 step5 / §6.5 indeterminate 先 reconcile）。"""

from floodmind.agent.runtime.contracts.tool_transaction import ToolStatus
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.reconciliation_service import (
    ReconcileResult, ReconciliationService,
)


def test_indeterminate_is_reconciled_before_retry(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("tool.call.proposed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "idempotency_key": "ik"})
    auth.emit("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "arguments": "{}"})
    auth.emit("tool.execution.indeterminate", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "reason": "timeout", "idempotency_key": "ik"})
    state = auth.replay()
    svc = ReconciliationService()
    assert svc.retry_allowed(state, "ttx_1") is False          # 未 reconcile 禁止重试
    res = svc.reconcile(auth, state)
    assert res.indeterminate_resolved == 1
    assert res.approvals_closed == 0 and res.child_threads_closed == 0
    assert res.background_killed == 0 and res.artifacts_cleaned == 0
    assert res.safe is True
    after = auth.replay()
    assert after.pending_tool_transactions == []               # 已落定移出
    assert svc.retry_allowed(after, "ttx_1") is True           # 落定后允许新事务
    tail = [e for e in auth.read_after(0) if e.event_type == "tool.result.committed"]
    assert len(tail) == 1 and tail[0].payload["verdict"] == "failed"
    assert tail[0].payload["transaction_id"] == "ttx_1"


def test_running_tool_reconciled_as_indeterminate_then_committed(tmp_path):
    """F：僵尸 running → 先 indeterminate(reconciled_pending)，再 result.committed(failed)。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("tool.call.proposed", {"transaction_id": "ttx_r", "call_id": "c_r",
        "tool_id": "builtin:Bash", "idempotency_key": "ik"})
    auth.emit("tool.execution.started", {"transaction_id": "ttx_r", "call_id": "c_r",
        "tool_id": "builtin:Bash", "arguments": "{}"})
    state = auth.replay()
    assert state.pending_tool_transactions[0].status == ToolStatus.running
    svc = ReconciliationService()
    res = svc.reconcile(auth, state)
    assert res.indeterminate_resolved >= 1
    after = auth.replay()
    assert after.pending_tool_transactions == []
    types = [e.event_type for e in auth.read_after(0)]
    # proposed + started(原始) + indeterminate + result.committed(reconcile)
    ind = [e for e in auth.read_after(0)
           if e.event_type == "tool.execution.indeterminate"
           and e.payload.get("reason") == "reconciled_pending"]
    assert len(ind) == 1 and ind[0].payload["transaction_id"] == "ttx_r"
    committed = [e for e in auth.read_after(0)
                 if e.event_type == "tool.result.committed"
                 and e.payload["transaction_id"] == "ttx_r"]
    assert len(committed) == 1 and committed[0].payload["verdict"] == "failed"
    assert types.index("tool.execution.indeterminate") < types.index("tool.result.committed")


def test_approval_required_tool_is_reconciled(tmp_path):
    """F：approval_required（无 resolved）→ 落定 failed 移出 pending，防止僵尸等待。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("tool.call.proposed", {"transaction_id": "ttx_a", "call_id": "c_a",
        "tool_id": "builtin:Write", "idempotency_key": "ik"})
    auth.emit("tool.approval.required", {"transaction_id": "ttx_a", "call_id": "c_a",
        "tool_name": "Write", "reason": "needs check", "approval_fingerprint": "fp"})
    state = auth.replay()
    assert state.pending_tool_transactions[0].status == ToolStatus.approval_required
    res = ReconciliationService().reconcile(auth, state)
    assert res.indeterminate_resolved >= 1
    after = auth.replay()
    assert after.pending_tool_transactions == []
    committed = [e for e in auth.read_after(0)
                 if e.event_type == "tool.result.committed"
                 and e.payload["transaction_id"] == "ttx_a"]
    assert len(committed) == 1 and committed[0].payload["verdict"] == "failed"


def test_dangling_approval_is_denied(tmp_path):
    """F：悬空 pending_approvals（无 resolved）→ deny 落定。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("tool.approval.requested", {"ask_id": "ask_1", "call_id": "c1",
        "tool_name": "Bash", "reason": "needs check"})
    state = auth.replay()
    assert len(state.pending_approvals) == 1
    res = ReconciliationService().reconcile(auth, state)
    assert res.approvals_closed == 1
    after = auth.replay()
    assert after.pending_approvals == []
    tail = [e for e in auth.read_after(0) if e.event_type == "tool.approval.resolved"]
    assert len(tail) == 1
    assert tail[0].payload["ask_id"] == "ask_1"
    assert tail[0].payload["approved"] is False


def test_running_child_thread_is_cancelled(tmp_path):
    """F：running child_thread → thread.cancelled。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("thread.created", {"thread_id": "child_1", "parent_call_id": "c1"})
    state = auth.replay()
    assert len(state.child_threads) == 1
    assert state.child_threads[0].status == "running"
    res = ReconciliationService().reconcile(auth, state)
    assert res.child_threads_closed == 1
    after = auth.replay()
    assert after.child_threads[0].status == "cancelled"
    tail = [e for e in auth.read_after(0) if e.event_type == "thread.cancelled"]
    assert len(tail) == 1
    assert tail[0].payload["thread_id"] == "child_1"
    assert tail[0].payload["summary"] == "reconciled"


def test_reconcile_is_deterministic(tmp_path):
    """F：相同输入事件 → reconcile 产出相同事件序列 → 重放状态一致。"""

    def setup(dirpath):
        auth = open_journal_authority(dirpath, conversation_id="c", task_id="t",
                                      run_id="run_1", thread_id="th", turn_id="tu")
        auth.emit("tool.call.proposed", {"transaction_id": "ttx_1", "call_id": "c1",
            "tool_id": "builtin:Bash", "idempotency_key": "ik"})
        auth.emit("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1",
            "tool_id": "builtin:Bash", "arguments": "{}"})
        auth.emit("tool.execution.indeterminate", {"transaction_id": "ttx_1", "call_id": "c1",
            "tool_id": "builtin:Bash", "reason": "timeout", "idempotency_key": "ik"})
        auth.emit("tool.approval.requested", {"ask_id": "ask_1", "call_id": "c2",
            "tool_name": "Bash", "reason": "r"})
        auth.emit("thread.created", {"thread_id": "child_1", "parent_call_id": "c3"})
        return auth

    auth1 = setup(tmp_path / "r1")
    auth2 = setup(tmp_path / "r2")
    s1 = auth1.replay()
    s2 = auth2.replay()
    r1 = ReconciliationService().reconcile(auth1, s1)
    r2 = ReconciliationService().reconcile(auth2, s2)
    assert r1 == r2
    # event_id 由 uuid 生成（身份），但 event_type + payload 必须确定一致
    seq1 = [(e.event_type, e.payload) for e in auth1.read_after(0)]
    seq2 = [(e.event_type, e.payload) for e in auth2.read_after(0)]
    assert seq1 == seq2
    # 重放折叠状态一致（processed_event_ids 是事件身份，天然不同，排除）
    d1 = auth1.replay().model_dump()
    d2 = auth2.replay().model_dump()
    d1.pop("processed_event_ids", None)
    d2.pop("processed_event_ids", None)
    assert d1 == d2
