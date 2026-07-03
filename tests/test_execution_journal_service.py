"""Tests for ExecutionJournalService."""

import json
import tempfile
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult
from floodmind.agent.runtime.services.execution_journal_service import ExecutionJournalService


class TestExecutionJournalService:
    def _make_service(self, inline_threshold: int = 1000):
        tmp = tempfile.mkdtemp()
        return ExecutionJournalService(base_dir=tmp, inline_threshold=inline_threshold), tmp

    def test_short_result_inline(self):
        svc, _ = self._make_service()
        tool_call = ToolCall(id="tc1", name="Read", arguments={"file_path": "test.txt"})
        tool_result = ToolResult(tool_call_id="tc1", name="Read", content="short content", status="completed")

        inline_content, entry = svc.process_tool_result("s1", tool_call, tool_result)

        assert inline_content == "short content"
        assert entry.inline is True
        assert entry.full_ref is None
        assert entry.summary == "short content"

    def test_long_result_archived(self):
        svc, tmp = self._make_service(inline_threshold=50)
        long_content = "line " * 100  # > 50 chars
        tool_call = ToolCall(id="tc2", name="Read", arguments={"file_path": "big.txt"})
        tool_result = ToolResult(tool_call_id="tc2", name="Read", content=long_content, status="completed")

        inline_content, entry = svc.process_tool_result("s1", tool_call, tool_result)

        assert entry.inline is False
        assert entry.full_ref is not None
        assert "已归档" in inline_content
        assert "共 100 行" in entry.summary or "字符" in entry.summary

        # 验证归档文件存在且可读取
        archived = svc.get_full_result("s1", entry.full_ref)
        assert archived is not None
        assert archived.content == long_content
        assert archived.tool_name == "Read"

    def test_summarize_file_content(self):
        svc, _ = self._make_service()
        content = "\n".join(f"line {i}" for i in range(50))
        tool_result = ToolResult(tool_call_id="tc3", name="Read", content=content, status="completed")
        summary = svc.summarize_tool_result(tool_result)
        assert "50 行" in summary

    def test_summarize_bash_output(self):
        svc, _ = self._make_service()
        content = "\n".join(f"output {i}" for i in range(30))
        tool_result = ToolResult(tool_call_id="tc4", name="Bash", content=content, status="completed")
        summary = svc.summarize_tool_result(tool_result)
        assert "30 行" in summary

    def test_summarize_search_output(self):
        svc, _ = self._make_service()
        content = "\n".join(f"result/{i}.py" for i in range(20))
        tool_result = ToolResult(tool_call_id="tc5", name="Grep", content=content, status="completed")
        summary = svc.summarize_tool_result(tool_result)
        assert "20 条" in summary

    def test_record_turn_and_read(self):
        svc, _ = self._make_service()
        tool_call = ToolCall(id="tc1", name="Read", arguments={})
        tool_result_entry = svc.process_tool_result("s1", tool_call, ToolResult(
            tool_call_id="tc1", name="Read", content="ok", status="completed"
        ))[1]

        svc.record_turn(
            session_id="s1",
            turn_index=0,
            checkpoint_id="ckpt-1",
            current_answer="I'll read the file",
            tool_calls=[tool_call],
            tool_result_entries=[tool_result_entry],
            token_usage={"prompt": 100, "completion": 20},
        )

        entries = svc.get_recent_summaries("s1", n=5)
        assert len(entries) == 1
        assert entries[0].turn_index == 0
        assert entries[0].checkpoint_id == "ckpt-1"
        assert entries[0].llm.answer_fragment == "I'll read the file"
        assert len(entries[0].tool_results) == 1

    def test_get_recent_summaries_returns_last_n(self):
        svc, _ = self._make_service()
        for i in range(10):
            svc.record_turn(
                session_id="s1",
                turn_index=i,
                checkpoint_id=None,
                current_answer=f"turn {i}",
                tool_calls=[],
                tool_result_entries=[],
            )

        entries = svc.get_recent_summaries("s1", n=3)
        assert len(entries) == 3
        assert entries[0].turn_index == 7
        assert entries[1].turn_index == 8
        assert entries[2].turn_index == 9

    def test_turns_jsonl_format(self):
        svc, tmp = self._make_service()
        svc.record_turn(
            session_id="s1",
            turn_index=0,
            checkpoint_id="ckpt-1",
            current_answer="hello",
            tool_calls=[],
            tool_result_entries=[],
        )

        turns_path = Path(tmp) / "s1" / "journal" / "turns.jsonl"
        assert turns_path.exists()
        lines = turns_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["turn_index"] == 0
        assert data["checkpoint_id"] == "ckpt-1"

    def test_turns_jsonl_rotation(self):
        tmp = tempfile.mkdtemp()
        svc = ExecutionJournalService(base_dir=tmp, max_turns_file_size_bytes=200)

        for i in range(5):
            svc.record_turn(
                session_id="s1",
                turn_index=i,
                checkpoint_id=None,
                current_answer=f"turn {i}" + "x" * 100,
                tool_calls=[],
                tool_result_entries=[],
            )

        turns_path = Path(tmp) / "s1" / "journal" / "turns.jsonl"
        # 至少应有一个归档文件
        backups = sorted(turns_path.parent.glob("turns.jsonl.*"))
        assert len(backups) >= 1

        entries = svc.get_recent_summaries("s1", n=5)
        assert len(entries) == 5
        assert entries[-1].turn_index == 4

    def test_get_recent_summaries_falls_back_to_backup(self):
        tmp = tempfile.mkdtemp()
        svc = ExecutionJournalService(base_dir=tmp, max_turns_file_size_bytes=200)

        for i in range(4):
            svc.record_turn(
                session_id="s1",
                turn_index=i,
                checkpoint_id=None,
                current_answer=f"turn {i}" + "x" * 100,
                tool_calls=[],
                tool_result_entries=[],
            )

        # 手动构造 backup（模拟滚动后只剩 backup 的情况）
        turns_path = Path(tmp) / "s1" / "journal" / "turns.jsonl"
        backup_path = turns_path.with_suffix(".jsonl.1")
        if turns_path.exists():
            turns_path.rename(backup_path)

        entries = svc.get_recent_summaries("s1", n=4)
        assert len(entries) == 4
        assert entries[0].turn_index == 0
