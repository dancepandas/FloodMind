"""Regression tests for session_store schema + the D-1c storage slim-down.

Covers the most safety-critical change in the refactor: the live ``sync_events``
table. Verifies that:

1. Legacy databases (old relational tables + FTS5 + a sync_events table whose
   session_id has a foreign key to sessions) upgrade cleanly: FTS5 is dropped,
   sync_events keeps all its rows, and its foreign key is removed.
2. After upgrade, ``append_sync_event`` for a session NOT present in any
   sessions table no longer raises IntegrityError. This is the regression for a
   real bug: web sessions are managed by session_manager (filesystem), not this
   DB, so the legacy FK rejected every event append — silently swallowed by
   web_server, which broke SSE event persistence and stream resume.
3. Fresh databases contain only the live sync_events table.
4. The legacy migration is idempotent.
"""
import sqlite3

from floodmind.memory import session_store


# A legacy schema: retired relational tables + FTS5 + a sync_events table whose
# session_id REFERENCES sessions(id) (the FK that caused the append bug).
_LEGACY_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE messages (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    event_index INTEGER NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL DEFAULT '',
    event_data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE search_index USING fts5(text, part_id UNINDEXED, session_id UNINDEXED);
CREATE TRIGGER trg_parts_insert_fts AFTER INSERT ON sessions
BEGIN
    INSERT INTO search_index (text, part_id, session_id) VALUES (NEW.title, NEW.id, NEW.id);
END;
"""


def _build_legacy_db(path: str) -> None:
    """Create a legacy sessions.db (relational tables + FTS5 + sync_events with FK + data)."""
    conn = sqlite3.connect(path)
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO sessions (id, title, created_at, updated_at) "
        "VALUES ('ses_legacy', 'legacy', '2024-01-01', '2024-01-01')"
    )
    # Note: sync_events rows for 'ses_legacy' satisfy the legacy FK (ses_legacy exists above).
    for i in range(5):
        conn.execute(
            "INSERT INTO sync_events (session_id, event_index, event_type, event_data, created_at) "
            "VALUES ('ses_legacy', ?, 'token', '{\"v\": 1}', '2024-01-01')",
            (i,),
        )
    conn.commit()
    conn.close()


def _upgrade(path: str) -> sqlite3.Connection:
    """Replicate _get_conn's setup on a fresh, caller-owned connection (no caching)."""
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(session_store.SCHEMA_SQL)
    session_store._migrate_legacy_schema(conn)
    return conn


def _names(conn: sqlite3.Connection, kind: str):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ? ORDER BY name", (kind,)
    ).fetchall()]


def _sync_events_sql(conn: sqlite3.Connection) -> str:
    return conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sync_events'"
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Legacy upgrade
# ---------------------------------------------------------------------------

def test_legacy_db_upgrades_fts5_dropped_fk_removed_data_intact(tmp_path):
    """Legacy DB upgrades: FTS5 gone, sync_events FK removed, rows preserved."""
    path = str(tmp_path / "sessions.db")
    _build_legacy_db(path)

    conn = _upgrade(path)
    try:
        tables = _names(conn, "table")
        triggers = _names(conn, "trigger")

        # FTS5 fully removed
        assert "search_index" not in tables
        assert not any(t.startswith("trg_parts") for t in triggers), triggers

        # sync_events foreign key removed
        assert "REFERENCES sessions" not in _sync_events_sql(conn)

        # LIVE sync_events data preserved through the rebuild (zero loss)
        count = conn.execute(
            "SELECT COUNT(*) FROM sync_events WHERE session_id = 'ses_legacy'"
        ).fetchone()[0]
        assert count == 5
    finally:
        conn.close()


def test_append_after_upgrade_no_fk_violation(tmp_path):
    """Regression: appending an event for a session not in any sessions table
    must NOT raise IntegrityError after the FK is removed.

    This is the bug that silently broke SSE event persistence: web sessions live
    in the filesystem, so their ids were never in the dark sessions table, and
    the legacy FK rejected every append.
    """
    path = str(tmp_path / "sessions.db")
    _build_legacy_db(path)
    conn = _upgrade(path)
    try:
        # 'web_session_x' was never inserted into sessions — under the legacy FK
        # this INSERT raised IntegrityError. After migration it must succeed.
        conn.execute(
            "INSERT INTO sync_events (session_id, event_index, event_type, event_data, created_at) "
            "VALUES ('web_session_x', 0, 'token', '{\"v\":1}', '2024-01-01')"
        )
        n = conn.execute(
            "SELECT COUNT(*) FROM sync_events WHERE session_id = 'web_session_x'"
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_fresh_db_has_only_sync_events(tmp_path):
    """A brand-new DB creates only the live sync_events table — no dark tables, no FTS5."""
    path = str(tmp_path / "sessions.db")
    conn = _upgrade(path)
    try:
        # sqlite_sequence is an internal SQLite bookkeeping table auto-created by
        # AUTOINCREMENT — not a business table, so exclude it.
        tables = {t for t in _names(conn, "table") if t != "sqlite_sequence"}
        assert tables == {"sync_events"}, tables
        assert not any(t.startswith("trg_parts") for t in _names(conn, "trigger"))
    finally:
        conn.close()


def test_migrate_legacy_schema_idempotent(tmp_path):
    """_migrate_legacy_schema is safe to call repeatedly (no-op once clean)."""
    path = str(tmp_path / "sessions.db")
    conn = _upgrade(path)
    try:
        for _ in range(3):
            session_store._migrate_legacy_schema(conn)  # must not raise
        assert "REFERENCES sessions" not in _sync_events_sql(conn)
        assert "search_index" not in _names(conn, "table")
    finally:
        conn.close()


def test_legacy_db_without_sync_events_upgrades(tmp_path):
    """An older DB that predates sync_events must upgrade cleanly: sync_events
    gets created (no FK), FTS5 dropped, no error."""
    path = str(tmp_path / "sessions.db")
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT);
        CREATE VIRTUAL TABLE search_index USING fts5(text);
    """)
    raw.commit()
    raw.close()

    conn = _upgrade(path)
    try:
        tables = {t for t in _names(conn, "table") if t != "sqlite_sequence"}
        assert "sync_events" in tables          # created by SCHEMA_SQL
        assert "search_index" not in tables      # FTS5 dropped
        assert "REFERENCES sessions" not in _sync_events_sql(conn)
    finally:
        conn.close()


def test_migration_failure_does_not_poison_connection(tmp_path, monkeypatch):
    """Regression for a silent-data-loss bug introduced and fixed in step 4.

    If the legacy migration raises while a transaction is open (e.g. 'database
    is locked' mid-rebuild), the connection cached by _get_conn must NOT remain
    stuck in that open transaction. Otherwise every subsequent append_sync_event
    INSERT would run uncommitted inside it and silently vanish — the exact
    failure class this refactor eliminated. _get_conn must roll back on failure.
    """
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session_store, "_db_path", lambda: path)

    def boom(conn):
        # Open a transaction then fail mid-migration, mirroring a real failure
        # during the BEGIN...COMMIT FK rebuild.
        conn.execute("BEGIN")
        raise RuntimeError("simulated mid-migration failure")

    monkeypatch.setattr(session_store, "_migrate_legacy_schema", boom)

    # SCHEMA runs (sync_events created, autocommitted), then the migration fails
    # mid-transaction. _get_conn must roll back before caching the connection.
    session_store._get_conn(str(path))

    session_store.append_sync_event("ses_x", 0, "token", {"v": 1})

    # An independent raw connection must see the committed row. If the cached
    # connection were poisoned by an open transaction, this row would be trapped
    # uncommitted and the count would be 0.
    verify = sqlite3.connect(str(path))
    try:
        n = verify.execute(
            "SELECT COUNT(*) FROM sync_events WHERE session_id = 'ses_x'"
        ).fetchone()[0]
        assert n == 1, "append_sync_event was silently lost — cached connection poisoned"
    finally:
        verify.close()
