"""SqliteJournalIndex — Journal 的可重建只读索引（§18：SQLite 只作 Index）。

JSONL Journal 是唯一权威；本索引是派生加速器，可随时丢弃并从 segments 重建。
绝不作为第二独立事实源。
"""
import json
import sqlite3
import threading
from pathlib import Path
from typing import List

from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.services.journal_writer import (
    _SEGMENT_PREFIX, _SEGMENT_SUFFIX,
)


class SqliteJournalIndex:
    """SQLite 索引：`journal_events` 表 + `seq_idx`。read_after 走索引加速。"""

    def __init__(self, journal_dir: Path, run_id: str):
        self._journal_dir = Path(journal_dir)
        self._db = self._journal_dir / "journal.sqlite3"
        self._run_id = run_id
        self._conn = sqlite3.connect(str(self._db))
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS journal_events ("
                " run_id TEXT NOT NULL, sequence INTEGER NOT NULL,"
                " event_id TEXT PRIMARY KEY, event_type TEXT, thread_id TEXT,"
                " payload_json TEXT, recorded_at TEXT, envelope_json TEXT)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_seq ON journal_events(run_id, sequence)"
            )
            self._conn.commit()

    def index_event(self, envelope: EventEnvelope) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO journal_events VALUES (?,?,?,?,?,?,?,?)",
                (self._run_id, envelope.sequence, envelope.event_id,
                 envelope.event_type, envelope.thread_id,
                 json.dumps(envelope.payload, ensure_ascii=False),
                 envelope.recorded_at.isoformat(), envelope.model_dump_json()),
            )
            self._conn.commit()

    def read_after(self, after_sequence: int = 0) -> List[EventEnvelope]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT envelope_json FROM journal_events"
                " WHERE run_id=? AND sequence>? ORDER BY sequence",
                (self._run_id, after_sequence),
            ).fetchall()
        return [self._row_to_envelope(r) for r in rows]

    def rebuild_from(self, journal_dir: Path) -> int:
        """从 JSONL segments 全量重建索引（先清空）。返回索引条数。"""
        journal_dir = Path(journal_dir)
        with self._lock:
            self._conn.execute("DELETE FROM journal_events WHERE run_id=?", (self._run_id,))
            count = 0
            for seg in sorted(journal_dir.glob(f"{_SEGMENT_PREFIX}*{_SEGMENT_SUFFIX}")):
                for line in seg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        env = EventEnvelope.model_validate_json(line)
                    except Exception:
                        continue  # 半写/坏行跳过（与 JournalWriter.read_from 同语义）
                    if env.sequence is None:
                        continue
                    self._conn.execute(
                        "INSERT OR REPLACE INTO journal_events VALUES (?,?,?,?,?,?,?,?)",
                        (self._run_id, env.sequence, env.event_id, env.event_type,
                         env.thread_id, json.dumps(env.payload, ensure_ascii=False),
                         env.recorded_at.isoformat(), env.model_dump_json()),
                    )
                    count += 1
            self._conn.commit()
        return count

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _row_to_envelope(self, row) -> EventEnvelope:
        return EventEnvelope.model_validate_json(row[0])
