"""P8 Task 1 — SqliteJournalIndex 派生索引：可重建、与 JSONL 权威一致、非第二事实源。"""
from pathlib import Path

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
