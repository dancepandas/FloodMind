"""
SQLite 会话存储（精简角色）。

**注意**：对话历史的权威源是 ``chat_history.json``（``memory._turns``），不是本库。
本 SQLite 库当前的生产职责单一：``sync_events`` 表 —— SSE 流式事件回放日志
（``append_sync_event`` / ``get_sync_events`` / ``get_last_event_index``，由 web_server 调用）。

历史关系表（sessions / messages / parts / tool_states / revert_points / checkpoints）
是早期 OpenCode 风格设计的遗留 —— 对应的 CRUD 函数与 cli 会话命令已移除，
表结构暂留以保持老库前向兼容，将在后续（D-1c 步骤4）整体退役。

FTS5 全文检索（search_index 虚表 + 触发器）已移除 —— 无生产调用方；
跨会话检索改为扫 chat_history.json 实现。
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
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    parent_id   TEXT,
    mode        TEXT NOT NULL DEFAULT 'primary',
    status      TEXT NOT NULL DEFAULT 'idle',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    agent       TEXT NOT NULL DEFAULT '',
    mode        TEXT NOT NULL DEFAULT '',
    parent_id   TEXT,
    created_at  TEXT NOT NULL,
    completed_at TEXT,
    error       TEXT,
    tokens_input    INTEGER DEFAULT 0,
    tokens_output   INTEGER DEFAULT 0,
    tokens_reasoning INTEGER DEFAULT 0,
    tokens_cache_read  INTEGER DEFAULT 0,
    tokens_cache_write INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS parts (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    type        TEXT NOT NULL CHECK(type IN ('text','tool','reasoning','file','compaction','error','step_start','step_finish','patch','retry')),
    text        TEXT NOT NULL DEFAULT '',
    metadata    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tool_states (
    part_id     TEXT PRIMARY KEY REFERENCES parts(id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','completed','error')),
    tool_name   TEXT NOT NULL DEFAULT '',
    call_id     TEXT NOT NULL DEFAULT '',
    input_json  TEXT NOT NULL DEFAULT '{}',
    output_text TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    started_at  TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS revert_points (
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_id  TEXT NOT NULL,
    reverted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_parent  ON messages(session_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_parts_message    ON parts(message_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_parts_session    ON parts(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_states_session ON tool_states(session_id);

CREATE TABLE IF NOT EXISTS checkpoints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    step            INTEGER NOT NULL DEFAULT 0,
    agent_name      TEXT NOT NULL DEFAULT 'build',
    plan_json       TEXT NOT NULL DEFAULT '{}',
    messages_json   TEXT NOT NULL DEFAULT '[]',
    events_json     TEXT NOT NULL DEFAULT '[]',
    artifact_snapshot TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints(session_id, step);

-- SyncEvent table: persistent event log for state replay / resume
CREATE TABLE IF NOT EXISTS sync_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    event_index INTEGER NOT NULL DEFAULT 0,
    event_type  TEXT NOT NULL DEFAULT '',
    event_data  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sync_events_session ON sync_events(session_id, event_index);
"""

# ── Part type extension migration ────────────────────────────────

def _migrate_parts_type_constraint(conn: sqlite3.Connection) -> None:
    try:
        info = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='parts'"
        ).fetchone()
        if info and "step_start" in info["sql"]:
            return  # Already migrated
    except Exception:
        pass

    logger.info("Migrating parts table CHECK constraint for new part types...")
    # Drop leftover from failed migration
    conn.execute("DROP TABLE IF EXISTS parts_old")
    conn.executescript("""
        ALTER TABLE parts RENAME TO parts_old;
        CREATE TABLE parts (
            id          TEXT PRIMARY KEY,
            message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            type        TEXT NOT NULL CHECK(type IN ('text','tool','reasoning','file','compaction','error','step_start','step_finish','patch','retry')),
            text        TEXT NOT NULL DEFAULT '',
            metadata    TEXT NOT NULL DEFAULT '{}'
        );
        INSERT INTO parts SELECT * FROM parts_old;
        DROP TABLE parts_old;
        CREATE INDEX IF NOT EXISTS idx_parts_message ON parts(message_id, sort_order);
        CREATE INDEX IF NOT EXISTS idx_parts_session ON parts(session_id);
    """)
    conn.commit()
    logger.info("Parts table migration complete")


def _migrate_sessions_schema(conn: sqlite3.Connection) -> None:
    """Add cost, model, permission columns to sessions/messages tables (incremental, idempotent)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    additions = [
        ("tokens_cache_read", "INTEGER DEFAULT 0"),
        ("tokens_cache_write", "INTEGER DEFAULT 0"),
        ("cost", "REAL DEFAULT 0.0"),
        ("model_info", "TEXT DEFAULT '{}'"),
        ("permission_rules", "TEXT DEFAULT '[]'"),
        ("version", "TEXT DEFAULT '1.0.0'"),
    ]
    for col_name, col_def in additions:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_def}")
            logger.info("Added column sessions.%s", col_name)
    msg_existing = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    msg_additions = [
        ("cost", "REAL DEFAULT 0.0"),
        ("provider_info", "TEXT DEFAULT '{}'"),
    ]
    for col_name, col_def in msg_additions:
        if col_name not in msg_existing:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {col_name} {col_def}")
            logger.info("Added column messages.%s", col_name)
    conn.commit()


def _drop_legacy_fts(conn: sqlite3.Connection) -> None:
    """Remove leftover FTS5 search_index table + sync triggers from older schemas.

    FTS5 full-text search has been retired (zero production callers; MemorySearch uses
    DualMemory.search_history over chat_history.json). Legacy database files may still
    carry the search_index virtual table and the parts→search_index triggers, which
    would otherwise keep firing on writes to the dead index. Drop them idempotently so
    neither fresh nor legacy DBs retain dead FTS5 machinery.
    """
    conn.executescript("""
        DROP TRIGGER IF EXISTS trg_parts_insert_fts;
        DROP TRIGGER IF EXISTS trg_parts_delete_fts;
        DROP TRIGGER IF EXISTS trg_parts_update_fts;
        DROP TABLE IF EXISTS search_index;
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_local = threading.local()


def _get_conn(path: Optional[str] = None) -> sqlite3.Connection:
    """Get a per-thread SQLite connection."""
    target = path or str(_db_path())
    key = f"conn_{target}"
    conn = getattr(_local, key, None)
    if conn is None:
        _db_dir().mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        # Run migrations (idempotent, pass conn to avoid recursion)
        try:
            _migrate_parts_type_constraint(conn)
        except Exception:
            logger.warning("parts migration failed, skipping", exc_info=True)
        try:
            _migrate_sessions_schema(conn)
        except Exception:
            logger.warning("sessions schema migration failed, skipping", exc_info=True)
        try:
            _drop_legacy_fts(conn)
        except Exception:
            logger.debug("legacy FTS cleanup skipped", exc_info=True)
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
    conn.commit()


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
