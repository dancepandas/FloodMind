"""Tests for session store FTS5 full-text search."""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from floodmind.memory import session_store


class TestSessionStoreFTS5:
    """Test FTS5 virtual table and search functionality."""

    def _fresh_db(self):
        """Create a temporary database for isolation."""
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "test_sessions.db")

        # Patch _db_path to return our temp db
        orig_db_path = session_store._db_path
        session_store._db_path = lambda: Path(db_path)
        # Reset thread-local connection
        session_store._local = __import__("threading").local()
        return tmpdir, orig_db_path

    def _restore_db(self, tmpdir, orig_db_path):
        """Restore original database path."""
        session_store._db_path = orig_db_path
        session_store._local = __import__("threading").local()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_fts5_table_created(self):
        """FTS5 virtual table is created by schema."""
        tmpdir, orig = self._fresh_db()
        try:
            conn = session_store._get_conn()
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables]
            assert "search_index" in table_names
        finally:
            self._restore_db(tmpdir, orig)

    def test_fts5_triggers_exist(self):
        """FTS5 sync triggers are created."""
        tmpdir, orig = self._fresh_db()
        try:
            conn = session_store._get_conn()
            triggers = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
            trigger_names = [t[0] for t in triggers]
            assert "trg_parts_insert_fts" in trigger_names
            assert "trg_parts_delete_fts" in trigger_names
            assert "trg_parts_update_fts" in trigger_names
        finally:
            self._restore_db(tmpdir, orig)

    def test_search_returns_results(self):
        """Adding messages creates searchable index."""
        tmpdir, orig = self._fresh_db()
        try:
            sid = session_store.create_session(title="水文分析会话")["id"]
            session_store.add_message(
                sid, "user",
                parts=[{"type": "text", "text": "请分析长江宜昌站的洪水数据"}]
            )
            session_store.add_message(
                sid, "assistant",
                parts=[
                    {"type": "text", "text": "好的，我来分析长江宜昌站的数据。"},
                    {"type": "tool", "text": "最大洪峰流量为 45000 m3/s"},
                ]
            )

            # FTS5 simple tokenizer matches whole phrases; use full phrase
            results = session_store.search_sessions("请分析长江宜昌站的洪水数据", limit=10)
            assert len(results) > 0
            assert any("长江宜昌站" in r["highlighted_text"] for r in results)
        finally:
            self._restore_db(tmpdir, orig)

    def test_search_empty_query(self):
        """Empty query returns empty results."""
        tmpdir, orig = self._fresh_db()
        try:
            assert session_store.search_sessions("") == []
            assert session_store.search_sessions("   ") == []
        finally:
            self._restore_db(tmpdir, orig)

    def test_search_no_match(self):
        """Non-matching query returns empty results."""
        tmpdir, orig = self._fresh_db()
        try:
            sid = session_store.create_session()["id"]
            session_store.add_message(sid, "user", parts=[{"type": "text", "text": "hello"}])
            results = session_store.search_sessions("xyznotfound12345")
            assert results == []
        finally:
            self._restore_db(tmpdir, orig)

    def test_rebuild_search_index(self):
        """Rebuild repopulates the index."""
        tmpdir, orig = self._fresh_db()
        try:
            sid = session_store.create_session()["id"]
            session_store.add_message(sid, "user", parts=[{"type": "text", "text": "flood data"}])

            # Clear index manually
            conn = session_store._get_conn()
            conn.execute("DELETE FROM search_index")
            conn.commit()

            # Rebuild
            count = session_store.rebuild_search_index()
            assert count >= 1

            # Search works again
            results = session_store.search_sessions("flood")
            assert len(results) > 0
        finally:
            self._restore_db(tmpdir, orig)

    def test_delete_syncs_fts(self):
        """Deleting parts removes them from FTS index."""
        tmpdir, orig = self._fresh_db()
        try:
            sid = session_store.create_session()["id"]
            mid = session_store.add_message(
                sid, "user", parts=[{"type": "text", "text": "unique_keyword_xyz"}]
            )

            # Verify indexed
            results = session_store.search_sessions("unique_keyword_xyz")
            assert len(results) > 0

            # Delete the message (cascades to parts)
            conn = session_store._get_conn()
            conn.execute("DELETE FROM messages WHERE id = ?", (mid,))
            conn.commit()

            # Should be gone from search
            results = session_store.search_sessions("unique_keyword_xyz")
            assert len(results) == 0
        finally:
            self._restore_db(tmpdir, orig)

    def test_parts_not_indexed_when_empty(self):
        """Empty text parts are not indexed."""
        tmpdir, orig = self._fresh_db()
        try:
            sid = session_store.create_session()["id"]
            session_store.add_message(sid, "user", parts=[{"type": "text", "text": ""}])

            conn = session_store._get_conn()
            count = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]
            assert count == 0
        finally:
            self._restore_db(tmpdir, orig)

    def test_only_textual_types_indexed(self):
        """Non-text types (like 'file') are not indexed by trigger."""
        tmpdir, orig = self._fresh_db()
        try:
            sid = session_store.create_session()["id"]
            session_store.add_message(sid, "user", parts=[{"type": "file", "text": "image data"}])

            conn = session_store._get_conn()
            count = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]
            # file type is not in ('text', 'tool', 'reasoning', 'error')
            assert count == 0
        finally:
            self._restore_db(tmpdir, orig)
