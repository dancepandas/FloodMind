from floodmind.agent.runtime.contracts.transactions import (
    ToolStatus, ToolTransaction, arguments_sha256, canonical_arguments,
    compute_fingerprint,
)


def test_canonical_arguments_are_key_sorted():
    assert canonical_arguments({"b": 2, "a": 1}) == {"a": 1, "b": 2}


def test_arguments_sha256_is_deterministic_and_sensitive_to_tool_id():
    a = arguments_sha256("Write", "1", {"file_path": "/x", "content": "hi"})
    b = arguments_sha256("Write", "1", {"content": "hi", "file_path": "/x"})
    assert a == b
    c = arguments_sha256("Read", "1", {"file_path": "/x", "content": "hi"})
    assert a != c


def test_fingerprint_changes_when_any_part_changes():
    f1 = compute_fingerprint({"tool": "Write", "targets": "/a"})
    f2 = compute_fingerprint({"tool": "Write", "targets": "/b"})
    assert f1 != f2


def test_transaction_default_status():
    t = ToolTransaction(transaction_id="ttx_1", call_id="call_1", tool_id="builtin:Read")
    assert t.status == ToolStatus.proposed
    assert t.side_effect_class.value == "read"
