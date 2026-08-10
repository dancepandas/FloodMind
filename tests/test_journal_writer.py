from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.services.journal_writer import (
    JournalWriteConflict, JournalWriter,
)


def _evt(seq: int, event_id: str = "") -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id or f"evt_{seq}",
        event_type="run.started",
        sequence=seq,
        run_id="run_1",
        payload={"seq": seq},
    )


def test_append_assigns_sequence_and_hashes(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    sealed = w.append(_evt(1))
    assert sealed.sequence == 1
    assert sealed.integrity.payload_sha256
    assert sealed.integrity.event_sha256
    assert w.current_sequence() == 1


def test_cas_conflict_detected(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1))
    with pytest.raises(JournalWriteConflict):
        w.append(_evt(2), expected_last_sequence=0)


def test_idempotent_event_id_not_appended_twice(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1, event_id="evt_x"))
    w.append(_evt(2, event_id="evt_x"))  # same id, should be no-op
    assert w.current_sequence() == 1


def test_read_from_replays_in_order(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1))
    w.append(_evt(2))
    assert [e.sequence for e in w.read_from()] == [1, 2]
    assert [e.sequence for e in w.read_from(after_sequence=1)] == [2]
