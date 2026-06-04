"""Tests for DualMemory module."""

import json
import os
import tempfile

from floodmind.memory.dual_memory import DualMemory, LongTermMemory


class TestLongTermMemory:
    def test_add_and_search(self, temp_dir):
        mem = LongTermMemory(memory_file=os.path.join(temp_dir, "ltm.json"))
        mem.add_entry("敖江流域 霍口水库 断面预报方案", category="水文")
        mem.add_entry("Excel导出 模板配置", category="办公")

        results = mem.search("霍口水库")
        assert len(results) > 0
        assert "霍口水库" in results[0]["content"]

    def test_search_no_match(self, temp_dir):
        mem = LongTermMemory(memory_file=os.path.join(temp_dir, "ltm.json"))
        mem.add_entry("test entry", category="general")
        results = mem.search("xyzabc")
        assert results == []

    def test_get_recent(self, temp_dir):
        mem = LongTermMemory(memory_file=os.path.join(temp_dir, "ltm.json"))
        for i in range(5):
            mem.add_entry(f"entry {i}", category="test")
        recent = mem.get_recent(3)
        assert len(recent) == 3
        assert "entry 4" in recent[-1]["content"]

    def test_clear(self, temp_dir):
        mem = LongTermMemory(memory_file=os.path.join(temp_dir, "ltm.json"))
        mem.add_entry("test", category="general")
        mem.clear()
        assert len(mem.entries) == 0

    def test_persistence(self, temp_dir):
        path = os.path.join(temp_dir, "ltm.json")
        mem = LongTermMemory(memory_file=path)
        mem.add_entry("persist test", category="general")

        mem2 = LongTermMemory(memory_file=path)
        assert len(mem2.entries) == 1
        assert mem2.entries[0]["content"] == "persist test"


class TestDualMemory:
    def test_add_user_and_ai_messages(self, temp_dir):
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("预报敖江流域")
        dm.add_ai_message("已生成预报结果")

        msgs = dm.get_short_term_messages()
        assert len(msgs) == 2
        assert msgs[0].content == "预报敖江流域"
        assert msgs[1].content == "已生成预报结果"

    def test_add_ai_message_with_trace(self, temp_dir):
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("分析数据")
        dm.add_ai_message_with_trace(
            content="分析完成",
            reasoning="首先读取数据文件...",
            tool_calls=[{"tool_name": "Read", "tool_input": "data.csv", "tool_output": "100 rows"}],
        )

        turns = dm.get_turns()
        assert len(turns) == 1
        assert turns[0]["reasoning"] == "首先读取数据文件..."
        assert len(turns[0]["tool_calls"]) == 1

    def test_chat_history_persistence(self, temp_dir):
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("hello")
        dm.add_ai_message("hi there")
        dm.save_chat_history()

        path = os.path.join(temp_dir, "chat_history.json")
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["session_id"] == "test"
        assert len(data["turns"]) == 1

    def test_load_from_disk(self, temp_dir):
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("hello")
        dm.add_ai_message("hi")
        dm.save_chat_history()

        dm2 = DualMemory(session_id="test", persist_dir=temp_dir)
        msgs = dm2.get_short_term_messages()
        assert len(msgs) == 2

    def test_clear_all(self, temp_dir):
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("hello")
        dm.add_ai_message("hi")
        dm.clear_all()

        assert dm.short_term_count == 0
        assert len(dm.get_turns()) == 0
