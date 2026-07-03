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

        turns = dm.get_turns()
        assert [t["role"] for t in turns] == ["user", "assistant"]
        assert turns[0]["content"] == "预报敖江流域"
        assert turns[1]["content"] == "已生成预报结果"

    def test_add_ai_message_with_trace(self, temp_dir):
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("分析数据")
        dm.add_ai_message_with_trace(
            content="分析完成",
            reasoning="首先读取数据文件...",
            tool_calls=[{"tool_name": "Read", "tool_input": "data.csv", "tool_output": "100 rows"}],
        )

        turns = dm.get_turns()
        # 扁平条目模型：user 与 assistant 各一条
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[0]["content"] == "分析数据"
        assert turns[1]["role"] == "assistant"
        assert turns[1]["reasoning"] == "首先读取数据文件..."
        assert len(turns[1]["tool_calls"]) == 1
        assert turns[1]["is_final"] is True

    def test_add_assistant_round_per_llm_call(self, temp_dir):
        """一次用户消息触发多次 LLM 调用 → 每次 LLM 调用各落一条 assistant 轮（history 粒度）。"""
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("分析并绘图")
        # 第 1 轮 LLM 调用：调工具
        dm.add_assistant_round(
            content="",
            reasoning="先读数据",
            tool_calls=[{"tool_name": "Read", "tool_input": "d.csv", "tool_output": "ok", "status": "completed"}],
            is_final=False,
        )
        # 第 2 轮 LLM 调用：终态回答
        dm.add_assistant_round(content="图已生成", reasoning="", tool_calls=[], is_final=True)

        turns = dm.get_turns()
        assert len(turns) == 3  # user + 2 assistant rounds
        assert [t["role"] for t in turns] == ["user", "assistant", "assistant"]
        assert turns[1]["is_final"] is False
        assert turns[2]["is_final"] is True

    def test_pending_user_messages(self, temp_dir):
        """尾部未被 assistant 轮回应的用户消息 = 当前 run 指令 + 排队指令。"""
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("任务A")
        dm.add_assistant_round(content="完成A", is_final=True)
        dm.add_user_message("任务B")  # 当前 run
        dm.add_user_message("任务C")  # 排队

        pending = dm.get_pending_user_messages()
        assert pending == ["任务B", "任务C"]

    def test_build_turns_text_skips_trailing_user(self, temp_dir):
        """_build_turns_text 跳过尾部未应答的 user（避免与 state.messages 的 user msg 重复）。"""
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("任务A")
        dm.add_assistant_round(content="完成A", is_final=True)
        dm.add_user_message("任务B")  # 尾部未应答

        text = dm._build_turns_text(dm._turns)
        assert "任务A" in text
        assert "完成A" in text
        assert "任务B" not in text  # 尾部 user 被跳过

    def test_migrate_old_turns(self, temp_dir):
        """旧版 user_input/final_answer 轮结构能迁移为扁平条目。"""
        dm = DualMemory(session_id="test", persist_dir=None)
        old = [
            {"turn_index": 0, "user_input": "旧问题", "reasoning": "r", "tool_calls": [], "final_answer": "旧回答", "timestamp": ""},
        ]
        flat = dm._migrate_old_turns(old)
        assert len(flat) == 2
        assert flat[0]["role"] == "user"
        assert flat[0]["content"] == "旧问题"
        assert flat[1]["role"] == "assistant"
        assert flat[1]["content"] == "旧回答"
        assert flat[1]["is_final"] is True

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
        # 扁平条目：user + assistant 两条
        assert len(data["turns"]) == 2

    def test_load_from_disk(self, temp_dir):
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("hello")
        dm.add_ai_message("hi")
        dm.save_chat_history()

        dm2 = DualMemory(session_id="test", persist_dir=temp_dir)
        turns = dm2.get_turns()
        assert [t["role"] for t in turns] == ["user", "assistant"]
        assert turns[0]["content"] == "hello"

    def test_clear_all(self, temp_dir):
        dm = DualMemory(session_id="test", persist_dir=temp_dir)
        dm.add_user_message("hello")
        dm.add_ai_message("hi")
        dm.clear_all()

        assert dm.turn_count == 0
        assert len(dm.get_turns()) == 0
