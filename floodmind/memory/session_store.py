"""
SQLite-based session store.

Replaces JSON file persistence with a proper relational store.
Schema inspired by OpenCode's session system.

Tables:
  sessions     — session metadata (title, parent, status, timestamps)
  messages     — each user/assistant message in a session
  parts        — individual message parts (text, tool, reasoning, file)
  tool_states  — tool execution state per part (pending/running/completed/error)
  revert_points— marks message IDs where sessions were truncated

Operations:
  create / get / list / delete / rename
  fork    — clone a session up to a given message
  revert  — truncate messages after a point (marks revert for Redo)
  compact — summarize older messages via LLM, inject as compaction part
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
    type        TEXT NOT NULL CHECK(type IN ('text','tool','reasoning','file','compaction','error')),
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
"""

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


def update_tool_state(part_id: str, session_id: str, **kwargs) -> None:
    """Update tool state fields."""
    allowed = {"status", "tool_name", "call_id", "input_json", "output_text", "title", "error", "started_at", "completed_at"}
    sets = {}
    params = []
    for k, v in kwargs.items():
        if k in allowed:
            col = k if k != "input_json" else "input_json"
            if k == "input_json" and isinstance(v, dict):
                v = json.dumps(v)
            sets[col] = v
            params.append(v)
    if not sets:
        return
    params.append(part_id)
    set_clause = ", ".join(f"{k} = ?" for k in sets)
    conn = _get_conn()
    conn.execute(f"UPDATE tool_states SET {set_clause} WHERE part_id = ?", params)
    conn.commit()


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


def unrevert_session(session_id: str) -> Optional[str]:
    """Remove the revert point. Returns the previously reverted message ID."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT message_id FROM revert_points WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM revert_points WHERE session_id = ?", (session_id,))
        touch_session(session_id)
        conn.commit()
        return row["message_id"]
    return None


def get_revert_point(session_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM revert_points WHERE session_id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


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


# ---------------------------------------------------------------------------
# Migration from old JSON format
# ---------------------------------------------------------------------------

def migrate_from_json(sessions_dir: str = "./data/sessions") -> int:
    """
    Migrate existing JSON session files to SQLite.
    Returns the number of sessions migrated.
    """
    import json as _json

    base = Path(sessions_dir)
    if not base.exists():
        return 0

    migrated = 0
    for session_dir in base.iterdir():
        if not session_dir.is_dir() or session_dir.name.startswith("."):
            continue

        sid = session_dir.name

        # Try to read session.json
        session_file = session_dir / "session.json"
        title = ""
        if session_file.exists():
            try:
                data = _json.loads(session_file.read_text(encoding="utf-8"))
                title = data.get("title", "")
            except Exception:
                pass

        # Create session in SQLite
        existing = get_session(sid)
        if existing:
            continue

        create_session(session_id=sid, title=title)

        # Migrate message files
        msg_files = sorted(
            [f for f in session_dir.glob("msg_*.json")],
            key=lambda x: x.stat().st_mtime,
        )
        for mf in msg_files:
            try:
                msg = _json.loads(mf.read_text(encoding="utf-8"))
                add_message(
                    session_id=sid,
                    role=msg.get("role", "user"),
                    agent=msg.get("agent", ""),
                    created_at=msg.get("timestamp", _now()),
                    parts=[{"type": "text", "text": msg.get("content", "")}],
                )
            except Exception:
                pass

        # Migrate chat_history.json to parts
        history_file = session_dir / "memory" / "chat_history.json"
        if history_file.exists():
            try:
                hist = _json.loads(history_file.read_text(encoding="utf-8"))
                for turn in hist.get("turns", []):
                    # Add user message
                    if turn.get("user_input"):
                        add_message(sid, "user", parts=[{"type": "text", "text": turn["user_input"]}],
                                    created_at=turn.get("timestamp", _now()))
                    # Add assistant with parts
                    parts = []
                    if turn.get("reasoning"):
                        parts.append({"type": "reasoning", "text": turn["reasoning"]})
                    for tc in turn.get("tool_calls", []):
                        parts.append({
                            "type": "tool",
                            "tool_name": tc.get("tool_name", ""),
                            "tool_state": {
                                "status": "completed",
                                "tool_name": tc.get("tool_name", ""),
                                "output_text": tc.get("tool_output", ""),
                            },
                        })
                    if turn.get("final_answer"):
                        parts.append({"type": "text", "text": turn["final_answer"]})
                    if parts:
                        add_message(sid, "assistant", parts=parts,
                                    created_at=turn.get("timestamp", _now()))
            except Exception:
                pass

        migrated += 1
        logger.info("Migrated session %s to SQLite", sid)

    logger.info("Migration complete: %d sessions", migrated)
    return migrated
