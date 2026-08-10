from floodmind.agent.runtime.services.idempotency import derive_idempotency_key, find_committed_result
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def test_derive_key_deterministic_and_read_is_empty():
    assert derive_idempotency_key(tool_id="builtin:Write", canonical_arguments='{"path":"/a"}',
                                  side_effect_class="reversible_write") == \
           derive_idempotency_key(tool_id="builtin:Write", canonical_arguments='{"path":"/a"}',
                                  side_effect_class="reversible_write")
    assert derive_idempotency_key(tool_id="builtin:Read", canonical_arguments='{"path":"/a"}',
                                  side_effect_class="read") == ""


def test_find_committed_result_reuses_succeeded(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    ik = derive_idempotency_key(tool_id="builtin:Write", canonical_arguments="{}",
                                side_effect_class="reversible_write")
    auth.emit("tool.execution.completed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Write", "status": "succeeded", "result_summary": "done",
        "full_ref": "ref://1", "artifacts": ["art_1"], "idempotency_key": ik})
    hit = find_committed_result(auth, ik)
    assert hit is not None and hit["result_summary"] == "done" and hit["artifacts"] == ["art_1"]
    assert find_committed_result(auth, "no-such-key") is None
