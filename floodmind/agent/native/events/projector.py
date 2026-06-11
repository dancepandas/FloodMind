"""
Event projector — persists SessionEvents to storage.

Design:
- Events are appended to an in-memory buffer
- Buffer is flushed to SQLite periodically (not per-event)
- Flushing never blocks the LLM stream
"""

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from .schema import SessionEvent

logger = logging.getLogger(__name__)


class EventProjector:
    """Project SessionEvents into persistent storage."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path
        self._buffer: List[SessionEvent] = []
        self._buffer_lock = threading.Lock()
        self._flush_interval = 5.0  # seconds
        self._max_buffer_size = 100
        self._flush_timer: Optional[threading.Timer] = None
        self._shutdown = False

    def project(self, event: SessionEvent) -> None:
        """Add event to buffer (non-blocking)."""
        with self._buffer_lock:
            self._buffer.append(event)
            should_flush = len(self._buffer) >= self._max_buffer_size

        if should_flush:
            self._do_flush()
        else:
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        """Schedule a delayed flush if none is pending."""
        if self._flush_timer is not None:
            return
        self._flush_timer = threading.Timer(self._flush_interval, self._do_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _do_flush(self) -> None:
        """Persist buffered events to SQLite."""
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None

        with self._buffer_lock:
            batch = self._buffer[:]
            self._buffer.clear()

        if not batch:
            return

        try:
            self._persist_batch(batch)
        except Exception as e:
            logger.warning("EventProjector flush failed: %s", e)

    def _persist_batch(self, events: List[SessionEvent]) -> None:
        """Write events to the session_events table."""
        if not self._db_path:
            return

        import sqlite3
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT,
                    session_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    payload TEXT,
                    created_at REAL DEFAULT (strftime('%s', 'now'))
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_events_sid ON session_events(session_id, timestamp)"
            )

            rows = []
            for e in events:
                rows.append((
                    e.event_id,
                    e.session_id,
                    e.type,
                    e.timestamp,
                    json.dumps(e.payload, ensure_ascii=False),
                ))

            conn.executemany(
                "INSERT INTO session_events (event_id, session_id, type, timestamp, payload) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            logger.debug("EventProjector flushed %d events", len(events))
        finally:
            conn.close()

    def get_events(
        self,
        session_id: str,
        after_timestamp: float = 0.0,
        event_types: Optional[List[str]] = None,
    ) -> List[SessionEvent]:
        """Read events from SQLite."""
        if not self._db_path:
            return []

        import sqlite3
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            query = (
                "SELECT event_id, session_id, type, timestamp, payload "
                "FROM session_events WHERE session_id = ? AND timestamp > ?"
            )
            params: List[Any] = [session_id, after_timestamp]

            if event_types:
                placeholders = ",".join("?" * len(event_types))
                query += f" AND type IN ({placeholders})"
                params.extend(event_types)

            query += " ORDER BY timestamp, id"

            cursor = conn.execute(query, params)
            results = []
            for row in cursor.fetchall():
                event_id, sid, typ, ts, payload_json = row
                payload = json.loads(payload_json) if payload_json else {}
                results.append(SessionEvent(
                    type=typ,
                    session_id=sid,
                    timestamp=ts,
                    payload=payload,
                    event_id=event_id or "",
                ))
            return results
        finally:
            conn.close()

    def shutdown(self) -> None:
        """Flush remaining events and stop timer."""
        self._shutdown = True
        if self._flush_timer:
            self._flush_timer.cancel()
        self._do_flush()
