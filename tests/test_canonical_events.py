from floodmind.agent.runtime.contracts.canonical_events import (
    EVENT_TYPES, Actor, EventEnvelope, EventIntegrity, Provenance,
    canonical_payload_sha256,
)


def test_envelope_defaults():
    e = EventEnvelope(event_id="evt_1", event_type="run.started", sequence=1)
    assert e.schema_version == "1.0"
    assert e.actor.type == "system"
    assert e.conversation_id == ""
    assert e.integrity.event_sha256 == ""


def test_required_event_types_present():
    for name in ("run.started", "model.attempt.completed", "tool.execution.started",
                 "tool.result.committed", "context.compaction.completed",
                 "thread.created", "artifact.committed", "checkpoint.created"):
        assert name in EVENT_TYPES


def test_actor_provenance_literals():
    Actor(type="user", id="u1")
    Provenance(source="provider", provider="openai", request_id="r1")


def test_canonical_payload_sha256_deterministic():
    p1 = {"b": 2, "a": 1}
    p2 = {"a": 1, "b": 2}
    assert canonical_payload_sha256(p1) == canonical_payload_sha256(p2)
    assert len(canonical_payload_sha256(p1)) == 64
