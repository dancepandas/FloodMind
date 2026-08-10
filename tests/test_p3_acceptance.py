"""P3 验收（设计 §25.4 Tool/Permission + §25.6 Resume Fault Injection 对应项）。"""

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from floodmind.agent.runtime.services.approval_fingerprint import compute_approval_fingerprint
from floodmind.agent.runtime.services.idempotency import (
    derive_idempotency_key, find_committed_result,
)
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.reconciliation_service import ReconciliationService
from floodmind.agent.runtime.contracts.tool_transaction import ToolStatus, canonical_arguments


def test_approval_fingerprint_param_change_invalidates():
    fp_a = compute_approval_fingerprint(
        tool_id="builtin:Write", tool_version="1",
        canonical_arguments=canonical_arguments({"path": "/safe/a"}),
        resolved_targets=["/safe/a"], cwd="/safe", environment_identity="e",
        workspace_id="w", workspace_generation="g", sandbox_permissions=[],
        agent_tier="main", runtime_mode="execution", side_effect_class="reversible_write",
        policy_version="v1")
    fp_b = compute_approval_fingerprint(
        tool_id="builtin:Write", tool_version="1",
        canonical_arguments=canonical_arguments({"path": "/safe/b"}),
        resolved_targets=["/safe/b"], cwd="/safe", environment_identity="e",
        workspace_id="w", workspace_generation="g", sandbox_permissions=[],
        agent_tier="main", runtime_mode="execution", side_effect_class="reversible_write",
        policy_version="v1")
    assert fp_a != fp_b  # 参数变化 → 旧批准失效


def test_idempotency_key_prevents_duplicate_write(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    ik = derive_idempotency_key(tool_id="builtin:Write",
                                canonical_arguments=canonical_arguments({"path": "/f"}),
                                side_effect_class="reversible_write")
    assert ik != ""
    auth.emit("tool.execution.completed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Write", "status": "succeeded", "result_summary": "wrote",
        "full_ref": "", "artifacts": [], "idempotency_key": ik})
    hit = find_committed_result(auth, ik)
    assert hit is not None  # 重放同键不再重写


def test_timeout_marks_indeterminate_then_reconcile(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    tx = "ttx_t"
    auth.emit("tool.call.proposed", {"transaction_id": tx, "call_id": "c_t",
        "tool_id": "builtin:Bash", "idempotency_key": "ik_t"})
    auth.emit("tool.execution.started", {"transaction_id": tx, "call_id": "c_t",
        "tool_id": "builtin:Bash", "arguments": "{}"})
    auth.emit("tool.execution.indeterminate", {"transaction_id": tx, "call_id": "c_t",
        "tool_id": "builtin:Bash", "reason": "timeout", "idempotency_key": "ik_t"})
    state = auth.replay()
    assert any(t.status == ToolStatus.indeterminate for t in state.pending_tool_transactions)
    svc = ReconciliationService()
    assert svc.retry_allowed(state, tx) is False          # 未 reconcile 禁止重试
    svc.reconcile(auth, state)
    assert auth.replay().pending_tool_transactions == []  # 已落定


def test_resume_fault_injection_no_duplicate_side_effect(tmp_path):
    """§25.6：Tool Started 后、Side Effect 完成但 Result 未提交前 crash → 恢复不重复副作用。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    ik = derive_idempotency_key(tool_id="builtin:Write",
                                canonical_arguments=canonical_arguments({"path": "/f"}),
                                side_effect_class="reversible_write")
    # crash 点在 started 之后、completed 之前
    auth.emit("tool.call.proposed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Write", "idempotency_key": ik})
    auth.emit("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Write", "arguments": canonical_arguments({"path": "/f"})})
    # 恢复：reconcile 先落定（不能直接重试），幂等键保证不重复写
    state = auth.replay()
    svc = ReconciliationService()
    assert svc.retry_allowed(state, "ttx_1") is False
    svc.reconcile(auth, state)
    committed = find_committed_result(auth, ik)
    # 副作用从未 confirmed：reconcile 落定为 failed，幂等查询返回 None（不假装已写）
    assert committed is None
    assert all(e.payload.get("verdict") == "failed"
               for e in auth.read_after(0) if e.event_type == "tool.result.committed")
