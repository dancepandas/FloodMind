"""P8 Task 1 — SqliteJournalIndex 派生索引：可重建、与 JSONL 权威一致、非第二事实源。"""
from pathlib import Path
import threading

from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.journal_index import SqliteJournalIndex


def _seed_journal(tmp_path, events):
    auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th", turn_id="tu",
    )
    for et, payload in events:
        auth.emit(et, payload)
    return auth


def test_index_rebuild_matches_segment_scan(tmp_path):
    auth = _seed_journal(tmp_path, [
        ("run.started", {"a": 1}),
        ("model.attempt.completed", {"content": "hi", "is_final": True}),
        ("run.completed", {}),
    ])
    idx = SqliteJournalIndex(auth._writer._base_dir / "conversations" / "c" / "tasks" / "t"
                             / "runs" / "run_1" / "journal", "run_1")
    rebuilt = idx.rebuild_from(Path(idx._db).parent)
    assert rebuilt == 3
    # 索引 read_after 与 JSONL 权威扫描一致
    assert idx.read_after(0) == auth.read_after(0)
    assert [e.sequence for e in idx.read_after(0)] == [1, 2, 3]
    assert idx.read_after(2) == auth.read_after(2)
    idx.close()


def test_index_is_derived_not_authoritative(tmp_path):
    """§18：索引可丢弃可重建；JSONL 是唯一权威。"""
    auth = _seed_journal(tmp_path, [("run.started", {})])
    jdir = Path(auth._writer._base_dir) / "conversations" / "c" / "tasks" / "t" \
        / "runs" / "run_1" / "journal"
    idx = SqliteJournalIndex(jdir, "run_1")
    idx.rebuild_from(jdir)
    # 删除索引文件：JSONL 权威不受影响
    idx.close()
    Path(idx._db).unlink()
    assert auth.read_after(0)  # 仍能读
    idx2 = SqliteJournalIndex(jdir, "run_1")
    assert idx2.rebuild_from(jdir) == 1
    idx2.close()


def test_authority_read_after_uses_index(tmp_path):
    """JournalAuthority 挂 index 后 read_after 返回相同结果。"""
    auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th", turn_id="tu", index=True,
    )
    auth.emit("run.started", {})
    auth.emit("run.completed", {})
    assert [e.sequence for e in auth.read_after(0)] == [1, 2]
    assert auth.read_after(1)[0].event_type == "run.completed"


def test_stale_index_falls_back_to_authoritative(tmp_path):
    auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th", turn_id="tu", index=True,
    )
    first = auth.emit("run.started", {})
    auth.emit("run.completed", {})
    auth._index.rebuild_from(auth._journal_dir)
    with auth._index._lock:
        auth._index._conn.execute(
            "DELETE FROM journal_events WHERE run_id=? AND sequence>?",
            ("run_1", first.sequence),
        )
        auth._index._conn.commit()
    assert [e.sequence for e in auth.read_after(0)] == [1, 2]
    assert auth._index.max_sequence() == 2


def test_index_true_on_existing_journal_rebuilds(tmp_path):
    seeded = _seed_journal(tmp_path, [("run.started", {}), ("run.completed", {})])
    auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th", turn_id="tu", index=True,
    )
    assert auth is not seeded
    assert [e.sequence for e in auth.read_after(0)] == [1, 2]


def test_cross_thread_emit_with_index(tmp_path):
    auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th", turn_id="tu", index=True,
    )
    errors = []

    def emit():
        try:
            auth.emit("run.started", {})
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=emit)
    thread.start()
    thread.join()
    assert errors == []
    assert [e.event_type for e in auth._index.read_after(0)] == ["run.started"]


def test_index_write_failure_isolated(tmp_path, monkeypatch):
    auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th", turn_id="tu", index=True,
    )

    def fail(_envelope):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(auth._index, "index_event", fail)
    event = auth.emit("run.started", {})
    assert event.sequence == 1
    assert [e.event_id for e in auth.read_after(0)] == [event.event_id]


def test_rebuild_ignores_non_numeric_segment(tmp_path):
    auth = _seed_journal(tmp_path, [("run.started", {})])
    jdir = auth._writer._journal_dir
    backup_event = auth.new_envelope("run.completed", {})
    backup_event.sequence = 2
    (jdir / "events-backup.jsonl").write_text(
        backup_event.model_dump_json() + "\n", encoding="utf-8",
    )
    idx = SqliteJournalIndex(jdir, "run_1")
    assert idx.rebuild_from(jdir) == 1
    assert [e.sequence for e in idx.read_after(0)] == [1]
    idx.close()
