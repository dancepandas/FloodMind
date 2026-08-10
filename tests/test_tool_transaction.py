import hashlib
import pytest

from floodmind.agent.runtime.contracts.tool_transaction import (
    SideEffectClass, ToolStatus, ToolTransaction,
    canonical_arguments, arguments_sha256,
)
from floodmind.agent.runtime.contracts.canonical_events import canonical_json


def test_canonical_arguments_normalizes_key_order_and_unicode():
    a = {"b": "值", "a": 1, "nested": {"z": True, "x": [2, 1]}}
    s1 = canonical_arguments(a)
    s2 = canonical_arguments({"nested": {"x": [2, 1], "z": True}, "a": 1, "b": "值"})
    assert s1 == s2
    assert '"a":1' in s1 and '"b":"值"' in s1  # sorted keys, ensure_ascii=False


def test_arguments_sha256_is_deterministic_and_defined():
    canon = canonical_arguments({"path": "/tmp/a"})
    h = arguments_sha256("builtin:Read", "1", canon)
    assert h == hashlib.sha256(("builtin:Read" + "1" + canon).encode("utf-8")).hexdigest()
    assert h == arguments_sha256("builtin:Read", "1", canon)  # deterministic
    assert h != arguments_sha256("builtin:Read", "2", canon)  # tool_version changes hash


def test_transition_legal_and_illegal():
    tx = ToolTransaction(transaction_id="ttx_1", call_id="call_1", tool_id="builtin:Write")
    r1 = tx.transition(ToolStatus.validated)
    assert r1.status == ToolStatus.validated and tx.status == ToolStatus.proposed  # immutable
    # 合法链：validated -> permission_evaluated -> approved -> running -> succeeded
    r_final = r1.transition(ToolStatus.permission_evaluated).transition(
        ToolStatus.approved).transition(ToolStatus.running).transition(
            ToolStatus.succeeded)
    assert r_final.status == ToolStatus.succeeded
    with pytest.raises(ValueError):  # proposed -> running 非法（跳步）
        tx.transition(ToolStatus.running)
    with pytest.raises(ValueError):  # succeeded -> failed 非法（终态）
        r_final.transition(ToolStatus.failed)
