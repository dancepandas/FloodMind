"""Tests for Journal-projected DualMemory and independent long-term facts."""

import os

from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.memory.dual_memory import DualMemory, LongTermMemory


class TestLongTermMemory:
    def test_add_and_search(self, temp_dir):
        mem = LongTermMemory(memory_file=os.path.join(temp_dir, "ltm.json"))
        mem.add_entry("敖江流域 霍口水库 断面预报方案", category="水文")
        mem.add_entry("Excel导出 模板配置", category="办公")
        results = mem.search("霍口水库")
        assert results and "霍口水库" in results[0]["content"]

    def test_search_no_match(self, temp_dir):
        mem = LongTermMemory(memory_file=os.path.join(temp_dir, "ltm.json"))
        mem.add_entry("test entry", category="general")
        assert mem.search("xyzabc") == []

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
        assert mem.entries == []

    def test_persistence(self, temp_dir):
        path = os.path.join(temp_dir, "ltm.json")
        LongTermMemory(memory_file=path).add_entry("persist test", category="general")
        restored = LongTermMemory(memory_file=path)
        assert restored.entries[0]["content"] == "persist test"


def _bound_memory(temp_dir):
    authority = open_journal_authority(
        temp_dir,
        conversation_id="conv_test",
        task_id="task_test",
        run_id="run_test",
        thread_id="thread_test",
        turn_id="turn_test",
    )
    memory = DualMemory(session_id="test", persist_dir=temp_dir)
    memory.bind_journal(authority, temp_dir, "conv_test")
    return memory, authority


def _complete(authority, content, *, reasoning="", tool_calls=None, is_final=True):
    authority.emit(
        "model.attempt.completed",
        {
            "attempt_id": "attempt_test",
            "terminal_reason": "completed" if is_final else "tool_calls",
            "content": content,
            "reasoning": reasoning,
            "tool_calls": tool_calls or [],
            "is_final": is_final,
            "usage": {},
        },
    )


class TestDualMemory:
    def test_reads_user_and_assistant_events(self, temp_dir):
        memory, authority = _bound_memory(temp_dir)
        authority.emit("thread.message.sent", {"content": "预报敖江流域", "turn_index": 0})
        _complete(authority, "已生成预报结果")
        turns = memory.get_turns()
        assert [turn["role"] for turn in turns] == ["user", "assistant"]
        assert turns[1]["content"] == "已生成预报结果"

    def test_reads_trace_and_each_model_attempt(self, temp_dir):
        memory, authority = _bound_memory(temp_dir)
        authority.emit("thread.message.sent", {"content": "分析并绘图", "turn_index": 0})
        _complete(
            authority,
            "",
            reasoning="先读数据",
            tool_calls=[{"tool_name": "Read", "tool_output": "ok"}],
            is_final=False,
        )
        _complete(authority, "图已生成")
        turns = memory.get_turns()
        assert [turn["role"] for turn in turns] == ["user", "assistant", "assistant"]
        assert turns[1]["reasoning"] == "先读数据"
        assert turns[1]["is_final"] is False
        assert turns[2]["is_final"] is True

    def test_pending_user_messages_come_from_projection(self, temp_dir):
        memory, authority = _bound_memory(temp_dir)
        authority.emit("thread.message.sent", {"content": "任务A", "turn_index": 0})
        _complete(authority, "完成A")
        authority.emit("thread.message.sent", {"content": "任务B", "turn_index": 1})
        authority.emit("thread.message.sent", {"content": "任务C", "turn_index": 2})
        assert memory.get_pending_user_messages() == ["任务B", "任务C"]

    def test_history_text_skips_trailing_user(self, temp_dir):
        memory, authority = _bound_memory(temp_dir)
        authority.emit("thread.message.sent", {"content": "任务A", "turn_index": 0})
        _complete(authority, "完成A")
        authority.emit("thread.message.sent", {"content": "任务B", "turn_index": 1})
        text = memory.get_chat_history_for_system_prompt()
        assert "任务A" in text and "完成A" in text
        assert "任务B" not in text

    def test_legacy_history_writers_are_removed(self, temp_dir):
        memory, _ = _bound_memory(temp_dir)
        for name in (
            "add_user_message",
            "add_ai_message",
            "add_ai_message_with_trace",
            "add_assistant_round",
            "save_chat_history",
            "_load_from_disk",
        ):
            assert not hasattr(memory, name)

    def test_clear_all_does_not_delete_journal_history(self, temp_dir):
        memory, authority = _bound_memory(temp_dir)
        authority.emit("thread.message.sent", {"content": "hello", "turn_index": 0})
        memory.clear_all()
        assert memory.get_user_messages() == ["hello"]
