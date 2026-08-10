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


def test_cas_conflict_across_writer_instances(tmp_path: Path):
    w1 = JournalWriter(tmp_path, "run_1")
    w2 = JournalWriter(tmp_path, "run_1")
    w1.append(_evt(1))
    # w2 was constructed before the append; a stale expected value must conflict
    # against the authoritative on-disk tail, not a cached in-memory value.
    with pytest.raises(JournalWriteConflict):
        w2.append(_evt(2), expected_last_sequence=0)


def test_duplicate_event_id_returns_persisted_sealed_envelope(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    first = w.append(_evt(1, event_id="evt_x"))
    again = w.append(_evt(2, event_id="evt_x"))
    assert again.event_id == first.event_id
    assert again.sequence == first.sequence
    assert again.integrity.event_sha256 == first.integrity.event_sha256


def test_duplicate_event_id_with_wrong_cas_raises(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1, event_id="evt_x"))
    with pytest.raises(JournalWriteConflict):
        w.append(_evt(2, event_id="evt_x"), expected_last_sequence=5)


def test_crash_between_append_and_index_save_recovers_sequence(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1))
    # Simulate a crash window: JSONL already holds the event but index.json still
    # points at sequence 0. A fresh writer must reconcile to the journal tail.
    index = tmp_path / "runs" / "run_1" / "journal" / "index.json"
    index.write_text(
        '{"run_id":"run_1","last_sequence":0,"last_event_sha256":"","current_segment":1,"event_ids":[]}',
        encoding="utf-8",
    )
    w2 = JournalWriter(tmp_path, "run_1")
    assert w2.current_sequence() == 1
    w2.append(_evt(2))
    assert [e.sequence for e in w2.read_from()] == [1, 2]


def test_concurrent_rollover_is_not_missed(tmp_path: Path):
    w1 = JournalWriter(tmp_path, "run_1", max_segment_bytes=1024)
    w2 = JournalWriter(tmp_path, "run_1")  # constructed before w1 rolls
    for i in range(1, 30):
        w1.append(_evt(i))
    assert w1.segment_count() > 1
    # w2 cached current_segment=1; its append must observe w1's rolled tail.
    w2.append(_evt(100))
    seqs = [e.sequence for e in w2.read_from()]
    assert seqs == list(range(1, 30)) + [30]


def test_roll_segment_creates_sealed_files(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1", max_segment_bytes=1024)
    for i in range(1, 60):
        w.append(_evt(i))
    assert w.segment_count() > 1
    # sequence continuity across segments
    seqs = [e.sequence for e in w.read_from()]
    assert seqs == list(range(1, 60))


def test_repair_tail_truncates_half_write(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1))
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    expected = seg.read_bytes()
    with seg.open("ab") as f:
        f.write(b'{"event_id": "evt_partial"')  # half-written line
    w.repair_tail()
    # Byte assertion proves the tail was truly removed and the full event kept.
    assert seg.read_bytes() == expected
    assert [e.sequence for e in w.read_from()] == [1]


def test_repair_tail_clears_segment_with_only_half_write(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    with seg.open("ab") as f:
        f.write(b'{"event_id": "evt_partial"')  # only content is a half-write
    w.repair_tail()
    assert seg.read_bytes() == b""


def test_append_writes_canonical_json_line(tmp_path: Path):
    from floodmind.agent.runtime.contracts.canonical_events import canonical_json
    w = JournalWriter(tmp_path, "run_1")
    sealed = w.append(_evt(1))
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    line = seg.read_text(encoding="utf-8").splitlines()[0]
    assert line == canonical_json(sealed.model_dump())


def test_stale_reader_sees_concurrently_rolled_segments(tmp_path: Path):
    w1 = JournalWriter(tmp_path, "run_1", max_segment_bytes=1024)
    r2 = JournalWriter(tmp_path, "run_1")  # stale reader, never appends
    for i in range(1, 30):
        w1.append(_evt(i))
    assert w1.segment_count() > 1
    assert [e.sequence for e in r2.read_from()] == list(range(1, 30))


def test_unsafe_run_id_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        JournalWriter(tmp_path, "../evil")
