import pytest

from floodmind.agent.runtime.contracts.identity import (
    ID_PREFIXES, Identity, is_valid_id, new_id,
)


def test_prefixes_cover_target_identities():
    for kind in ("conversation", "task", "run", "thread", "turn", "attempt",
                 "call", "transaction", "artifact", "checkpoint"):
        assert kind in ID_PREFIXES


def test_new_id_shape_and_validity():
    conv = new_id("conversation")
    assert conv.startswith("conv_")
    assert is_valid_id("conversation", conv)
    assert not is_valid_id("run", conv)


def test_new_id_is_unique():
    assert new_id("call") != new_id("call")


def test_new_id_rejects_unknown_kind():
    with pytest.raises(ValueError):
        new_id("nope")


def test_identity_defaults():
    i = Identity(conversation_id="conv_1", task_id="task_1", run_id="run_1",
                 thread_id="thread_1", turn_id="turn_1")
    assert i.attempt_id is None
    assert i.call_id is None
