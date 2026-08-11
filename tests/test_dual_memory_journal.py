from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.memory.dual_memory import DualMemory


def test_dual_memory_reads_current_and_conversation_projection(tmp_path):
    auth = open_journal_authority(
        tmp_path,
        conversation_id="conv_memory",
        task_id="task_1",
        run_id="run_1",
        thread_id="thread_1",
        turn_id="turn_1",
    )
    memory = DualMemory("session_1")
    memory.bind_journal(auth, tmp_path, "conv_memory")

    auth.emit("thread.message.sent", {"content": "hello", "turn_index": 0})
    auth.emit(
        "model.attempt.completed",
        {
            "attempt_id": "attempt_1",
            "terminal_reason": "completed",
            "content": "world",
            "reasoning": "",
            "tool_calls": [],
            "is_final": True,
            "usage": {},
        },
    )

    assert memory.get_user_messages() == ["hello"]
    assert memory.get_turns()[1]["content"] == "world"
    assert memory.turn_count == 2
    assert "hello" in memory.get_chat_history_for_system_prompt()
    assert "world" in memory.get_chat_history_for_system_prompt()


def test_dual_memory_has_no_legacy_history_writers():
    memory = DualMemory("session_2")

    for name in (
        "add_user_message",
        "add_ai_message",
        "add_ai_message_with_trace",
        "add_assistant_round",
        "save_chat_history",
        "_load_from_disk",
    ):
        assert not hasattr(memory, name)
