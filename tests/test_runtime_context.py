import dataclasses
import pytest

from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext


def _ctx() -> RuntimeContext:
    return RuntimeContext(
        conversation_id="conv_1", task_id="task_1", run_id="run_1",
        thread_id="thread_1", turn_id="turn_1",
    )


def test_frozen_rejects_mutation():
    ctx = _ctx()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.agent_tier = "sub"  # type: ignore[misc]


def test_identity_fields_default_services_to_none():
    ctx = _ctx()
    assert ctx.permission_service is None
    assert ctx.runtime_mode == "execution"


def test_requires_identity_fields():
    with pytest.raises(TypeError):
        RuntimeContext()  # type: ignore[call-arg]
