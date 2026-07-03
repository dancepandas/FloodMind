"""
SQLite 会话存储（精简角色）。

**注意**：对话历史的权威源是 ``chat_history.json``（``memory._turns``），不是本库。
本 SQLite 库的生产职责单一：``sync_events`` 表 —— SSE 流式事件回放日志
（``append_sync_event`` / ``get_sync_events`` / ``get_last_event_index``，由 web_server 调用）。

历史关系表（sessions/messages/parts/tool_states/revert_points/checkpoints）与 FTS5
全文检索已整体退役。``sync_events.session_id`` 不带外键 —— web 会话由 session_manager
（文件系统）管理、不在本库，外键会导致每次 append 对 web session 抛 IntegrityError
（曾静默吞掉，使 SSE 事件持久化/流式恢复失效）。

老库升级由 ``_migrate_legacy_schema`` 幂等处理：删 FTS5、数据保全地重建 sync_events 去
外键；暗关系表在老库上保留为无害孤儿（避免删除用户历史 cli-fork 数据），新库只建 sync_events。
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _db_dir() -> Path:
    return Path.home() / ".config" / "floodmind"


def _db_path() -> Path:
    return _db_dir() / "sessions.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- SyncEvent table: persistent event log for SSE state replay / resume.
-- session_id intentionally has NO foreign key — web sessions live in the
-- filesystem (session_manager), not in this DB, so an FK would reject every
-- append (IntegrityError). See _migrate_legacy_schema for the legacy-FK cleanup.
CREATE TABLE IF NOT EXISTS sync_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    event_index INTEGER NOT NULL DEFAULT 0,
    event_type  TEXT NOT NULL DEFAULT '',
    event_data  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sync_events_session ON sync_events(session_id, event_index);
"""


def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    """Clean up legacy schema remnants idempotently (runs on each new connection).

    On older database files, removes:
    1. FTS5 search_index virtual table + its parts->search_index sync triggers.
    2. The sync_events -> sessions foreign key: recreate sync_events WITHOUT the
       FK, preserving every row. This fixes a latent bug where appending events
       for web sessions (which are not in the dark sessions table) raised
       IntegrityError and was silently swallowed, breaking SSE event persistence
       and stream resume.
    3. Retired relational tables (sessions/messages/parts/tool_states/
       revert_points/checkpoints) are NOT dropped here — they may still hold
       historical data from the removed cli session commands; leaving them as
       harmless orphans avoids data loss. Fresh DBs never create them.

    On a fresh database this is a no-op (nothing to clean). The FK removal
    (step 2) runs inside an explicit transaction so concurrent readers never see
    an intermediate state (no `sync_events` table).
    """
    # 1. Drop FTS5 triggers + search_index virtual table.
    conn.executescript("""
        DROP TRIGGER IF EXISTS trg_parts_insert_fts;
        DROP TRIGGER IF EXISTS trg_parts_delete_fts;
        DROP TRIGGER IF EXISTS trg_parts_update_fts;
        DROP TABLE IF EXISTS search_index;
    """)

    # 2. If sync_events still carries the legacy FK to sessions, rebuild it
    #    without the FK (data-preserving, atomic).
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sync_events'"
    ).fetchone()
    if row and "REFERENCES sessions" in row["sql"]:
        conn.executescript("""
            BEGIN;
            DROP INDEX IF EXISTS idx_sync_events_session;
            ALTER TABLE sync_events RENAME TO sync_events_old;
            CREATE TABLE sync_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                event_index INTEGER NOT NULL DEFAULT 0,
                event_type  TEXT NOT NULL DEFAULT '',
                event_data  TEXT NOT NULL DEFAULT '{}',
                created_at  TEXT NOT NULL
            );
            INSERT INTO sync_events (id, session_id, event_index, event_type, event_data, created_at)
                SELECT id, session_id, event_index, event_type, event_data, created_at
                FROM sync_events_old;
            DROP TABLE sync_events_old;
            CREATE INDEX IF NOT EXISTS idx_sync_events_session ON sync_events(session_id, event_index);
            COMMIT;
        """)
        logger.info("sync_events: dropped legacy FK to sessions (IntegrityError bug fix)")

    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_local = threading.local()


def _get_conn(path: Optional[str] = None) -> sqlite3.Connection:
    """Get a per-thread SQLite connection.

    isolation_level=None puts the connection in autocommit mode so the legacy
    migration can drive explicit BEGIN/COMMIT transactions (needed for the
    atomic sync_events FK rebuild). Single-statement LIVE ops (append/get) are
    unaffected — append still persists immediately.
    """
    target = path or str(_db_path())
    key = f"conn_{target}"
    conn = getattr(_local, key, None)
    if conn is None:
        _db_dir().mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        try:
            _migrate_legacy_schema(conn)
        except Exception:
            # Roll back any transaction left open by a failed migration step.
            # In autocommit mode executescript does not auto-rollback, and a
            # cached connection stuck in an open transaction would silently drop
            # every subsequent append_sync_event write — the exact silent-data-
            # -loss class this refactor eliminated.
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning("legacy schema migration failed, skipping", exc_info=True)
        setattr(_local, key, conn)
    return conn


# ── SyncEvent — persistent event log for state replay ────────────────

def append_sync_event(session_id: str, event_index: int, event_type: str, event_data: dict) -> None:
    """Persist an event to the sync log for state replay / stream resume."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO sync_events (session_id, event_index, event_type, event_data, created_at) VALUES (?,?,?,?,?)",
        (session_id, event_index, event_type, json.dumps(event_data, ensure_ascii=False), _now()),
    )


def get_sync_events(session_id: str, after_index: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
    """Retrieve events after a given index (for stream resume / replay)."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT event_index, event_type, event_data, created_at
           FROM sync_events
           WHERE session_id = ? AND event_index > ?
           ORDER BY event_index
           LIMIT ?""",
        (session_id, after_index, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_last_event_index(session_id: str) -> int:
    """Get the highest event_index for a session (0 if no events)."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT MAX(event_index) FROM sync_events WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return row[0] if row and row[0] is not None else 0
