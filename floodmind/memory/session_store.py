"""
SQLite 会话存储（精简角色）。

**注意**：对话历史的权威源是 ``chat_history.json``（``memory._turns``），不是本库。
本 SQLite 库当前的生产职责单一：``sync_events`` 表 —— SSE 流式事件回放日志
（``append_sync_event`` / ``get_sync_events`` / ``get_last_event_index``，由 web_server 调用）。

历史关系表（sessions / messages / parts / tool_states / revert_points / checkpoints）
是早期 OpenCode 风格设计的遗留，现仅由 cli 会话管理命令与测试使用，计划退役。

FTS5 全文检索（search_index 虚表 + 触发器）已移除 —— 无生产调用方；
跨会话检索改为扫 chat_history.json 实现。
"""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import base64

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


def _uid(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


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


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def create_session(session_id: Optional[str] = None, title: str = "", parent_id: Optional[str] = None) -> Dict[str, Any]:
    sid = session_id or _uid("ses_")
    now = _now()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO sessions (id, title, parent_id, created_at, updated_at) VALUES (?,?,?,?,?)",
        (sid, title, parent_id, now, now),
    )
    conn.commit()
    return get_session(sid)


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def list_sessions() -> List[Dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM messages WHERE session_id = s.id) AS msg_count "
        "FROM sessions s ORDER BY s.updated_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def rename_session(session_id: str, title: str) -> None:
    conn = _get_conn()
    conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?", (title, _now(), session_id))
    conn.commit()


def delete_session(session_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


def touch_session(session_id: str) -> None:
    conn = _get_conn()
    conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id))
    conn.commit()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def add_message(
    session_id: str,
    role: str,
    *,
    agent: str = "",
    mode: str = "",
    parent_id: Optional[str] = None,
    parts: Optional[List[Dict[str, Any]]] = None,
    created_at: Optional[str] = None,
) -> str:
    """Add a message with parts. Returns message_id."""
    mid = _uid("msg_")
    now = created_at or _now()
    conn = _get_conn()
    conn.execute(
        """INSERT INTO messages (id, session_id, role, agent, mode, parent_id, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (mid, session_id, role, agent, mode, parent_id, now),
    )
    if parts:
        for i, p in enumerate(parts):
            _insert_part(conn, session_id, mid, i, p)
    touch_session(session_id)
    conn.commit()
    return mid


def complete_message(
    message_id: str,
    *,
    error: Optional[str] = None,
    tokens: Optional[Dict[str, int]] = None,
    append_parts: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Mark a message completed, optionally with tokens and extra parts."""
    conn = _get_conn()
    now = _now()
    updates = {"completed_at = ?": now}
    params: List[Any] = [now]
    if error is not None:
        updates["error = ?"] = error
        params.append(error)
    if tokens:
        for k in ("tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write"):
            if k in tokens:
                updates[f"{k} = ?"] = tokens[k]
                params.append(tokens[k])
    set_clause = ", ".join(f"{k}" for k in updates)
    params.append(message_id)
    conn.execute(f"UPDATE messages SET {set_clause} WHERE id = ?", params)
    if append_parts:
        row = conn.execute("SELECT session_id, COALESCE(MAX(sort_order), -1) + 1 FROM parts WHERE message_id = ?", (message_id,)).fetchone()
        if row and row[0] is not None:
            base_order = row[1] if row[1] is not None else 0
            for i, p in enumerate(append_parts):
                _insert_part(conn, row[0], message_id, base_order + i, p)
    conn.commit()


def get_messages(session_id: str) -> List[Dict[str, Any]]:
    """Get all messages for a session with their parts and tool states."""
    conn = _get_conn()
    messages = conn.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at",
        (session_id,),
    ).fetchall()

    result = []
    for msg in messages:
        msg_dict = dict(msg)
        parts = conn.execute(
            "SELECT * FROM parts WHERE message_id = ? ORDER BY sort_order",
            (msg["id"],),
        ).fetchall()
        msg_dict["parts"] = []
        for p in parts:
            part_dict = dict(p)
            if p["type"] == "tool":
                tool = conn.execute(
                    "SELECT * FROM tool_states WHERE part_id = ?", (p["id"],)
                ).fetchone()
                if tool:
                    part_dict["tool_state"] = dict(tool)
            msg_dict["parts"].append(part_dict)
        result.append(msg_dict)
    return result


def get_last_assistant_message(session_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM messages WHERE session_id = ? AND role = 'assistant' ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


# ── Cursor-based pagination helpers ────────────────────────────

def _encode_cursor(message_id: str, created_at: str) -> str:
    """Encode a pagination cursor: base64url(json({id, time})).

    The cursor encodes the (created_at, id) tuple of the last item
    on the current page, enabling stable keyset pagination.
    """
    payload = json.dumps({"id": message_id, "time": created_at})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> Tuple[str, str]:
    """Decode a pagination cursor → (message_id, created_at)."""
    # Restore base64 padding (urlsafe encoding strips =)
    padding = 4 - len(cursor) % 4
    if padding != 4:
        cursor += "=" * padding
    payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    return payload["id"], payload["time"]


def get_messages_page(
    session_id: str,
    limit: int = 50,
    before_cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """Cursor-based paginated message query.

    Returns:
        {
            "items": List[Dict],   # messages with populated parts/tool_states
            "more": bool,          # True if more messages exist after this page
            "cursor": str | None,  # cursor for the next page (None if no more)
        }
    """
    conn = _get_conn()

    if before_cursor:
        before_id, before_time = _decode_cursor(before_cursor)
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ?
              AND (created_at < ? OR (created_at = ? AND id < ?))
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (session_id, before_time, before_time, before_id, limit + 1),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (session_id, limit + 1),
        ).fetchall()

    more = len(rows) > limit
    items = rows[:limit]

    # Hydrate: batch-load parts for all returned messages
    result = []
    if items:
        msg_ids = [row["id"] for row in items]
        placeholders = ",".join("?" * len(msg_ids))
        part_rows = conn.execute(
            f"""SELECT * FROM parts
                WHERE message_id IN ({placeholders})
                ORDER BY message_id, sort_order""",
            msg_ids,
        ).fetchall()

        # Group parts by message_id
        parts_by_msg: Dict[str, List[Dict]] = {}
        for pr in part_rows:
            pd = dict(pr)
            parts_by_msg.setdefault(pr["message_id"], []).append(pd)

        # Batch-load tool states for all tool parts
        tool_part_ids = [pr["id"] for pr in part_rows if pr["type"] == "tool"]
        tool_states_by_part: Dict[str, Dict] = {}
        if tool_part_ids:
            ts_placeholders = ",".join("?" * len(tool_part_ids))
            ts_rows = conn.execute(
                f"SELECT * FROM tool_states WHERE part_id IN ({ts_placeholders})",
                tool_part_ids,
            ).fetchall()
            for ts in ts_rows:
                tool_states_by_part[ts["part_id"]] = dict(ts)

        for row in items:
            msg_dict = dict(row)
            msg_parts = parts_by_msg.get(row["id"], [])
            for p in msg_parts:
                if p["type"] == "tool":
                    p["tool_state"] = tool_states_by_part.get(p["id"])
            msg_dict["parts"] = msg_parts
            result.append(msg_dict)

    # Build next cursor from the last item
    next_cursor = None
    if more and items:
        last = items[-1]
        next_cursor = _encode_cursor(last["id"], last["created_at"])

    return {"items": result, "more": more, "cursor": next_cursor}


def _insert_part(conn: sqlite3.Connection, session_id: str, message_id: str, order: int, part: Dict[str, Any]) -> str:
    pid = part.get("id") or _uid("prt_")
    conn.execute(
        "INSERT INTO parts (id, message_id, session_id, sort_order, type, text, metadata) VALUES (?,?,?,?,?,?,?)",
        (pid, message_id, session_id, order, part["type"], part.get("text", ""), json.dumps(part.get("metadata", {}))),
    )
    if part["type"] == "tool":
        ts = part.get("tool_state", {})
        conn.execute(
            """INSERT INTO tool_states (part_id, session_id, status, tool_name, call_id, input_json, output_text, title, error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                pid, session_id,
                ts.get("status", "pending"),
                ts.get("tool_name", part.get("tool_name", "")),
                ts.get("call_id", ""),
                json.dumps(ts.get("input", {})),
                ts.get("output", ""),
                ts.get("title", ""),
                ts.get("error", ""),
            ),
        )
    return pid


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------

def fork_session(session_id: str, *, up_to_message_id: Optional[str] = None) -> str:
    """
    Fork a session: copy all messages up to (and including) up_to_message_id
    into a new session. If up_to_message_id is None, copy everything.

    Returns the new session ID.
    """
    source = get_session(session_id)
    if not source:
        raise ValueError(f"Session not found: {session_id}")

    new_id = _uid("ses_")
    create_session(session_id=new_id, title=f"{source['title']} (fork)")

    messages = get_messages(session_id)
    for msg in messages:
        # Strip part IDs to avoid collisions in the new session
        clean_parts = []
        for p in msg.get("parts", []):
            p = dict(p)
            p.pop("id", None)
            if "tool_state" in p:
                p["tool_state"] = dict(p["tool_state"])
                p["tool_state"].pop("part_id", None)
            clean_parts.append(p)
        add_message(new_id, msg["role"], agent=msg["agent"], mode=msg["mode"],
                    parent_id=msg.get("parent_id"), parts=clean_parts,
                    created_at=msg["created_at"])
        if up_to_message_id and msg["id"] == up_to_message_id:
            break

    logger.info("Forked session %s → %s", session_id, new_id)
    return new_id


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------

def revert_session(session_id: str, message_id: str) -> None:
    """
    Revert a session to a specific message: delete all messages after it,
    and mark the reverted point for possible redo.
    """
    conn = _get_conn()
    messages = conn.execute(
        "SELECT id, created_at FROM messages WHERE session_id = ? ORDER BY created_at",
        (session_id,),
    ).fetchall()

    # Find the target message index
    cut_idx = None
    for i, m in enumerate(messages):
        if m["id"] == message_id:
            cut_idx = i
            break

    if cut_idx is None:
        raise ValueError(f"Message {message_id} not found in session {session_id}")

    # Delete messages after the cut point
    for m in messages[cut_idx + 1:]:
        conn.execute("DELETE FROM messages WHERE id = ?", (m["id"],))

    # Clear any existing revert point (only one active revert at a time)
    conn.execute("DELETE FROM revert_points WHERE session_id = ?", (session_id,))

    # Mark the revert
    conn.execute(
        "INSERT INTO revert_points (session_id, message_id, reverted_at) VALUES (?,?,?)",
        (session_id, message_id, _now()),
    )

    touch_session(session_id)
    conn.commit()
    logger.info("Reverted session %s to message %s", session_id, message_id)


# ---------------------------------------------------------------------------
# Compact (summarize)
# ---------------------------------------------------------------------------

def compact_session(session_id: str, llm=None) -> Optional[str]:
    """
    Summarize early messages and insert a compaction marker.
    Keeps the most recent 4 messages intact.
    """
    messages = get_messages(session_id)
    if len(messages) <= 6:
        return None  # Not enough to compact

    keep_count = 4
    to_compact = messages[:-keep_count]

    # Build conversation text
    lines = []
    for m in to_compact:
        role = "User" if m["role"] == "user" else "Assistant"
        for p in m.get("parts", []):
            if p["type"] == "text" and p["text"]:
                lines.append(f"{role}: {p['text'][:500]}")
    conv_text = "\n".join(lines)

    if llm and conv_text:
        try:
            prompt = (
                "Summarize this conversation history concisely, preserving key decisions, "
                "facts, file paths, and action items:\n\n" + conv_text
            )
            response = llm.invoke(prompt)
            summary = (response.content if hasattr(response, "content") else str(response)).strip()
        except Exception:
            summary = f"[Compacted {len(to_compact)} messages]"
    else:
        summary = f"[Compacted {len(to_compact)} messages]"

    # Insert compaction as a part on the first kept message
    first_kept = messages[-keep_count]
    complete_message(
        first_kept["id"],
        append_parts=[{
            "type": "compaction",
            "text": summary,
            "metadata": {"compacted_count": len(to_compact)},
        }],
    )

    # Delete the compacted messages from DB
    conn = _get_conn()
    for m in to_compact:
        conn.execute("DELETE FROM messages WHERE id = ?", (m["id"],))
    touch_session(session_id)
    conn.commit()

    logger.info("Compacted session %s: %d messages → summary", session_id, len(to_compact))
    return summary


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_session_markdown(session_id: str) -> str:
    """Export a session as Markdown."""
    session = get_session(session_id)
    if not session:
        return ""
    messages = get_messages(session_id)

    lines = [
        f"# {session['title'] or 'Session ' + session_id[:8]}",
        "",
        f"Session ID: `{session_id}`",
        f"Created: {session['created_at']}",
        f"Updated: {session['updated_at']}",
        "",
        "---",
        "",
    ]

    for msg in messages:
        role_label = {"user": "## User", "assistant": "## Assistant", "system": "## System"}
        ts = msg.get("completed_at") or msg["created_at"]
        lines.append(f"{role_label.get(msg['role'], '## ' + msg['role'].title())} ({ts[:19]})")
        lines.append("")
        for p in msg.get("parts", []):
            if p["type"] == "text":
                lines.append(p["text"])
                lines.append("")
            elif p["type"] == "tool":
                ts_data = p.get("tool_state", {})
                lines.append(f"**Tool:** `{ts_data.get('tool_name', '?')}` ({ts_data.get('status', '?')})")
                if ts_data.get("output_text"):
                    lines.append("```")
                    lines.append(ts_data["output_text"][:2000])
                    lines.append("```")
                lines.append("")
            elif p["type"] == "reasoning":
                lines.append(f"> *Thought:* {p['text'][:500]}")
                lines.append("")
            elif p["type"] == "error":
                lines.append(f"> **Error:** {p['text']}")
                lines.append("")
        lines.append("")

    return "\n".join(lines)


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
