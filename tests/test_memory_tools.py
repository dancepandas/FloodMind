"""Tests for memory tools."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult
from floodmind.agent.runtime.services.execution_journal_service import ExecutionJournalService
from floodmind.tools.base_tools import set_memory_instance
from floodmind.tools.memory_tools import (
    conversation_search,
    core_memory_append,
    core_memory_read,
    journal_get_full_result,
    journal_search,
    _get_session_id,
    _core_memory_path,
)
from floodmind.tools.session_context import set_session_context


class TestMemoryTools:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        set_session_context("test-session", output_dir=self.tmp)
        # 清理可能存在的 core_memory 文件
        from floodmind.tools.memory_tools import _core_memory_path
        cp = _core_memory_path("test-session")
        if cp.exists():
            cp.unlink()

    def teardown_method(self):
        set_session_context("", output_dir="")

    def test_core_memory_append_and_read(self):
        result = core_memory_append.func(category="user_preferences", fact="喜欢先出计划")
        assert "已记录" in result

        result = core_memory_read.func()
        assert "喜欢先出计划" in result
        assert "user_preferences" in result

        # 重复追加不应重复
        result = core_memory_append.func(category="user_preferences", fact="喜欢先出计划")
        assert "已存在" in result

        result = core_memory_read.func(category="user_preferences")
        # 只应有一条
        assert result.count("喜欢先出计划") == 1

    def test_core_memory_read_category_empty(self):
        result = core_memory_read.func(category="nonexistent")
        assert "没有记录" in result

    def test_conversation_search(self):
        memory = MagicMock()
        memory.search_history.return_value = "第1轮: 用户问了一个问题\n回答: 这是答案"
        set_memory_instance(memory)

        result = conversation_search.func(query="问题")
        assert "第1轮" in result
        memory.search_history.assert_called_once_with("问题", 3)

    def test_conversation_search_no_memory(self):
        set_memory_instance(None)
        result = conversation_search.func(query="问题")
        assert "记忆系统未初始化" in result

    def test_journal_search_and_get_full_result(self, monkeypatch):
        monkeypatch.chdir(self.tmp)
        svc = ExecutionJournalService(inline_threshold=10)

        # 创建一些 journal 记录（内容超过 100 字符以触发归档，因为 threshold 有最小值 100）
        long_content = "word " * 50  # 250+ chars
        tool_call = ToolCall(id="tc1", name="Read", arguments={})
        tool_result = ToolResult(tool_call_id="tc1", name="Read", content=long_content, status="completed")
        inline, entry = svc.process_tool_result("test-session", tool_call, tool_result)

        svc.record_turn(
            session_id="test-session",
            turn_index=0,
            checkpoint_id=None,
            current_answer="读取配置文件",
            tool_calls=[tool_call],
            tool_result_entries=[entry],
        )

        # 用 journal_search 查找
        result = journal_search.func(query="配置文件")
        assert "Turn 0" in result
        assert "Read" in result
        assert entry.full_ref in result

        # 用 journal_get_full_result 读取完整结果
        result = journal_get_full_result.func(ref_id=entry.full_ref)
        assert "完整工具结果" in result
        assert long_content in result

    def test_journal_get_full_result_not_found(self):
        result = journal_get_full_result.func(ref_id="not-exist")
        assert "未找到归档结果" in result

    def test_session_id_helper(self):
        assert _get_session_id() == "test-session"
        assert _core_memory_path("test-session").name == "core_memory.json"
