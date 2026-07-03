"""Regression tests for session_store schema migrations and legacy DB upgrade.

Covers the FTS5-removal upgrade path: a legacy sessions.db (old parts CHECK
constraint + FTS5 search_index virtual table + sync triggers + LIVE sync_events
data) must upgrade cleanly through the _get_conn migration sequence with zero
data loss and no leftover FTS5 objects.

These tests directly verify that removing the redundant DROP TRIGGER from
_migrate_parts_type_constraint (FTS5 cleanup is now solely owned by
_drop_legacy_fts) leaves the legacy upgrade path correct.
"""
import sqlite3

from floodmind.memory import session_store


# Legacy parts CHECK constraint — predates step_start/step_finish/patch/retry part types.
# Its absence is what triggers _migrate_parts_type_constraint on upgrade.
_LEGACY_SCHEMA = """
CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    parent_id   TEXT,
    mode        TEXT NOT NULL DEFAULT 'primary',
    status      TEXT NOT NULL DEFAULT 'idle',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    agent       TEXT NOT NULL DEFAULT '',
    mode        TEXT NOT NULL DEFAULT '',
    parent_id   TEXT,
    created_at  TEXT NOT NULL,
    completed_at TEXT,
    error       TEXT,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    tokens_reasoning INTEGER DEFAULT 0,
    tokens_cache_read INTEGER DEFAULT 0,
    tokens_cache_write INTEGER DEFAULT 0
);
CREATE TABLE parts (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    type        TEXT NOT NULL CHECK(type IN ('text','tool','reasoning','file','compaction','error')),
    text        TEXT NOT NULL DEFAULT '',
    metadata    TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE tool_states (
    part_id     TEXT PRIMARY KEY REFERENCES parts(id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending',
    tool_name   TEXT NOT NULL DEFAULT '',
    call_id     TEXT NOT NULL DEFAULT '',
    input_json  TEXT NOT NULL DEFAULT '{}',
    output_text TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    started_at  TEXT,
    completed_at TEXT
);
CREATE TABLE revert_points (
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_id  TEXT NOT NULL,
    reverted_at TEXT NOT NULL
);
CREATE TABLE checkpoints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    step            INTEGER NOT NULL DEFAULT 0,
    agent_name      TEXT NOT NULL DEFAULT 'build',
    plan_json       TEXT NOT NULL DEFAULT '{}',
    messages_json   TEXT NOT NULL DEFAULT '[]',
    events_json     TEXT NOT NULL DEFAULT '[]',
    artifact_snapshot TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL
);
CREATE TABLE sync_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    event_index INTEGER NOT NULL DEFAULT 0,
    event_type  TEXT NOT NULL DEFAULT '',
    event_data  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE VIRTUAL TABLE search_index USING fts5(text, part_id UNINDEXED, session_id UNINDEXED);
CREATE TRIGGER trg_parts_insert_fts AFTER INSERT ON parts
WHEN NEW.type IN ('text','tool','reasoning','error') AND NEW.text != ''
BEGIN
    INSERT INTO search_index (text, part_id, session_id) VALUES (NEW.text, NEW.id, NEW.session_id);
END;
CREATE TRIGGER trg_parts_delete_fts AFTER DELETE ON parts
BEGIN
    DELETE FROM search_index WHERE part_id = OLD.id;
END;
"""


def _build_legacy_db(path: str) -> None:
    """Create a legacy sessions.db (old parts CHECK + FTS5 + sync_events data)."""
    conn = sqlite3.connect(path)
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO sessions (id, title, created_at, updated_at) "
        "VALUES ('ses_legacy', 'legacy', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO messages (id, session_id, role, created_at) "
        "VALUES ('msg_1', 'ses_legacy', 'user', '2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO parts (id, message_id, session_id, sort_order, type, text, metadata) "
        "VALUES ('prt_1', 'msg_1', 'ses_legacy', 0, 'text', 'analyze flood data', '{}')"
    )
    for i in range(5):
        conn.execute(
            "INSERT INTO sync_events (session_id, event_index, event_type, event_data, created_at) "
            "VALUES ('ses_legacy', ?, 'token', '{\"v\": 1}', '2024-01-01T00:00:00')",
            (i,),
        )
    conn.commit()
    conn.close()


def _upgrade(path: str) -> sqlite3.Connection:
    """Replicate _get_conn's migration sequence on a fresh, caller-owned connection."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(session_store.SCHEMA_SQL)
    conn.commit()
    session_store._migrate_parts_type_constraint(conn)
    session_store._migrate_sessions_schema(conn)
    session_store._drop_legacy_fts(conn)
    return conn


def _names(conn: sqlite3.Connection, kind: str):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ? ORDER BY name", (kind,)
    ).fetchall()]


def test_legacy_db_upgrades_fts5_removed_sync_events_intact(tmp_path):
    """Legacy DB upgrades: FTS5 objects gone, parts CHECK migrated, sync_events intact."""
    path = str(tmp_path / "sessions.db")
    _build_legacy_db(path)

    conn = _upgrade(path)
    try:
        tables = _names(conn, "table")
        triggers = _names(conn, "trigger")

        # FTS5 fully removed — no search_index, no parts→search_index triggers
        assert "search_index" not in tables, f"search_index lingered: {tables}"
        assert not any(t.startswith("trg_parts") for t in triggers), f"triggers lingered: {triggers}"

        # parts migrated to new CHECK (now includes step_start)
        parts_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='parts'"
        ).fetchone()[0]
        assert "step_start" in parts_sql

        # LIVE sync_events data preserved through the upgrade (zero loss)
        count = conn.execute(
            "SELECT COUNT(*) FROM sync_events WHERE session_id = 'ses_legacy'"
        ).fetchone()[0]
        assert count == 5
    finally:
        conn.close()


def test_fresh_db_has_no_fts5(tmp_path):
    """A brand-new DB never creates FTS5 objects, but does create the live sync_events table."""
    path = str(tmp_path / "sessions.db")
    conn = _upgrade(path)
    try:
        tables = _names(conn, "table")
        triggers = _names(conn, "trigger")
        assert "search_index" not in tables
        assert not any(t.startswith("trg_parts") for t in triggers)
        assert "sync_events" in tables  # the one live table
    finally:
        conn.close()


def test_drop_legacy_fts_idempotent(tmp_path):
    """_drop_legacy_fts is safe to call repeatedly on an FTS5-free DB (no-op, no error)."""
    path = str(tmp_path / "sessions.db")
    conn = _upgrade(path)
    try:
        for _ in range(3):
            session_store._drop_legacy_fts(conn)  # must not raise
        assert "search_index" not in _names(conn, "table")
    finally:
        conn.close()
