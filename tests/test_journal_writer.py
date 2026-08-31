import os
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


def _mk_event(event_type: str, payload: dict) -> EventEnvelope:
    return EventEnvelope(event_id=f"evt_{payload.get('k', event_type)}", event_type=event_type, payload=payload, sequence=0)


def test_append_many_consecutive_sequences_and_chain(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    evs = [_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
           _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})]
    sealed = w.append_many(evs, expected_last_sequence=0)
    assert [e.sequence for e in sealed] == [1, 2]
    assert sealed[1].integrity.previous_event_sha256 == sealed[0].integrity.event_sha256
    # 重读一致性
    reread = w.read_from(0)
    assert [e.sequence for e in reread] == [1, 2]


def test_append_many_cas_conflict(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_mk_event("thread.message.sent", {"k": "x", "content": "hi"}))
    try:
        w.append_many([_mk_event("model.attempt.completed", {"k": "y"})], expected_last_sequence=0)
    except JournalWriteConflict:
        return
    assert False, "expected JournalWriteConflict"


def test_append_many_idempotent_retry(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    evs = [_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
           _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})]
    first = w.append_many(evs, expected_last_sequence=0)
    second = w.append_many(evs, expected_last_sequence=0)
    assert second == first
    assert len(w.read_from(0)) == 2  # 未重复写


def test_append_many_mismatched_retry_rejected(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    evs = [_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
           _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})]
    w.append_many(evs, expected_last_sequence=0)
    evs_changed = [_mk_event("thread.message.sent", {"k": "a", "content": "CHANGED"}),
                   _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})]
    with pytest.raises(ValueError):
        w.append_many(evs_changed, expected_last_sequence=0)


def test_append_many_ids_from_unrelated_groups_rejected(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    w.append_many([_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
                   _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})],
                  expected_last_sequence=0)  # seq 1,2
    w.append_many([_mk_event("tool.execution.completed", {"k": "c", "content": "done"}),
                   _mk_event("artifact.committed", {"k": "d", "content": "saved"})],
                  expected_last_sequence=2)  # seq 3,4
    # 从两个无关组各取一个 id 拼装（sealed 序列 1 与 4，非连续）→ 不能证明是整组重试
    with pytest.raises(ValueError):
        w.append_many([_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
                       _mk_event("artifact.committed", {"k": "d", "content": "saved"})],
                      expected_last_sequence=4)


def test_append_many_partial_overlap_rejected(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    w.append_many([_mk_event("thread.message.sent", {"k": "a", "content": "hi"})], expected_last_sequence=0)
    with pytest.raises(ValueError):
        w.append_many([_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
                       _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})],
                      expected_last_sequence=0)


def test_append_many_duplicate_ids_rejected(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    ev = _mk_event("thread.message.sent", {"k": "a", "content": "hi"})
    with pytest.raises(ValueError):
        w.append_many([ev, ev], expected_last_sequence=0)


def test_append_many_no_duplicate_rows_and_contiguous_sequences(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    g1 = [_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
          _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})]
    g2 = [_mk_event("tool.execution.completed", {"k": "c", "content": "done"})]
    w.append_many(g1, expected_last_sequence=0)
    w.append_many(g2, expected_last_sequence=2)
    w.append_many(g1, expected_last_sequence=2)  # 整组重试：跳过 CAS，不重复写
    events = w.read_from(0)
    assert [e.sequence for e in events] == [1, 2, 3]
    ids = [e.event_id for e in events]
    assert len(ids) == len(set(ids))  # 无重复行
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    lines = [l for l in seg.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 3  # 每事件恰好一行


def test_append_many_write_failure_leaves_no_torn_prefix(tmp_path, monkeypatch):
    w = JournalWriter(tmp_path, "run_1")
    evs = [_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
           _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})]
    real_fsync = os.fsync
    calls = {"n": 0}

    def flaky_fsync(fd):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("injected fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr("floodmind.agent.runtime.services.journal_writer.os.fsync", flaky_fsync)
    with pytest.raises(OSError):
        w.append_many(evs, expected_last_sequence=0)
    monkeypatch.undo()

    # 段中无撕裂行：每个非空行都能解析为 EventEnvelope
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    lines = [l for l in seg.read_text(encoding="utf-8").splitlines() if l.strip()]
    parsed = [EventEnvelope.model_validate_json(l) for l in lines]
    seqs = [e.sequence for e in parsed]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))  # 无重复
    assert seqs == list(range(1, len(seqs) + 1))  # 连续

    # reconcile 恢复的状态与磁盘一致（truthful）
    assert w.current_sequence() == (seqs[-1] if seqs else 0)
    on_disk_ids = {e.event_id for e in parsed}
    for e in evs:
        if e.event_id in on_disk_ids:
            assert w.sealed(e.event_id) is not None

    # 后续全新 append 成功，sequence 连续且不重复
    ev = _mk_event("thread.message.sent", {"k": "n", "content": "new"})
    sealed = w.append(ev)
    assert sealed.sequence == (seqs[-1] + 1 if seqs else 1)
    all_events = w.read_from(0)
    ids = [x.event_id for x in all_events]
    assert len(ids) == len(set(ids))


def test_append_many_partial_write_failure_recovers_truthfully(tmp_path, monkeypatch):
    w = JournalWriter(tmp_path, "run_1")
    evs = [_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
           _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})]
    real_path_open = Path.open

    class _FlakyWriteFile:
        """真实二进制文件句柄的包装：第一次 write 只写前缀（截断最后一行）后抛错。"""

        def __init__(self, real):
            self._real = real

        def write(self, data):
            self._real.write(data[:-5])  # 写掉除最后 5 字节外的全部 → 最后一行被截断
            raise OSError("injected partial write failure")

        def flush(self):
            return self._real.flush()

        def fileno(self):
            return self._real.fileno()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._real.__exit__(*exc)

    def flaky_open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if mode == "ab" and self.name.endswith(".jsonl"):
            return _FlakyWriteFile(real_path_open(self, mode, buffering, encoding, errors, newline))
        return real_path_open(self, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", flaky_open)
    with pytest.raises(OSError):
        w.append_many(evs, expected_last_sequence=0)
    monkeypatch.undo()

    # 无撕裂行：每个非空行都能解析为 EventEnvelope
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    lines = [l for l in seg.read_text(encoding="utf-8").splitlines() if l.strip()]
    parsed = [EventEnvelope.model_validate_json(l) for l in lines]
    seqs = [e.sequence for e in parsed]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))  # 无重复
    assert seqs == list(range(1, len(seqs) + 1))  # 连续

    # reconcile 恢复的状态只反映完整落盘的事件（truthful）
    assert w.current_sequence() == (seqs[-1] if seqs else 0)
    assert w.sealed("evt_a") is not None  # 完整行已落盘
    assert w.sealed("evt_b") is None      # 撕裂尾被 repair_tail 截断，未落盘

    # 后续全新组 append_many 成功，sequence 连续且不重复
    evs2 = [_mk_event("thread.message.sent", {"k": "n", "content": "new"})]
    sealed2 = w.append_many(evs2, expected_last_sequence=w.current_sequence())
    assert sealed2[0].sequence == (seqs[-1] + 1 if seqs else 1)
    all_events = w.read_from(0)
    ids = [x.event_id for x in all_events]
    assert len(ids) == len(set(ids))


def test_journal_dir_override(tmp_path):
    custom = tmp_path / "custom" / "runs" / "run_9" / "journal"
    default_dir = tmp_path / "runs" / "run_9" / "journal"
    w = JournalWriter(tmp_path, "run_9", journal_dir=custom)
    w.append(_mk_event("thread.message.sent", {"k": "z", "content": "hi"}))
    assert (custom / "events-000001.jsonl").exists()
    assert (custom / "index.json").exists()
    assert not default_dir.exists()  # 默认路径未被创建
    assert len(w.read_from(0)) == 1


# ── D04/D05/D14 回归：撕裂尾健壮性 ──────────────────────────────

def _append_raw_bytes(tmp_path: Path, raw: bytes) -> Path:
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1))
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    with seg.open("ab") as f:
        f.write(raw)
    return seg


def test_multibyte_torn_tail_does_not_crash_reads(tmp_path: Path):
    """多字节撕裂尾只影响坏行，journal 仍可打开、可读、可追加（D05）。"""
    torn = '{"event_id": "evt_bad", "payload": {"text": "中文被切'.encode("utf-8")[:-2]
    _append_raw_bytes(tmp_path, b"\n" + torn)
    w = JournalWriter(tmp_path, "run_1")  # 构造即 reconcile
    assert w.current_sequence() == 1
    assert len(w.read_from(0)) == 1
    sealed = w.append(_evt(2))  # 坏尾之后仍可正常追加
    assert sealed.sequence == 2


def test_mid_file_corruption_refuses_repair_truncate(tmp_path: Path):
    """坏行之后仍有合法事件时，repair_tail 拒绝截断（D04）。"""
    import pytest as _pytest
    from floodmind.agent.runtime.services.journal_writer import JournalMidFileCorruption

    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1))
    w.append(_evt(2))
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    content = seg.read_bytes()
    lines = content.split(b"\n")
    # 在两条合法事件中间插入坏行
    corrupted = lines[0] + b"\n" + b"{broken json" + b"\n" + lines[1] + b"\n"
    seg.write_bytes(corrupted)
    w2 = JournalWriter(tmp_path, "run_1")
    with _pytest.raises(JournalMidFileCorruption):
        w2.repair_tail()
    # 合法事件 1、2 在读路径仍可用（读侧坏行即段尾，本段事件 1 可见）
    events = w2.read_from(0)
    assert [e.sequence for e in events] == [1]
