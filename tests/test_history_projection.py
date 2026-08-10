from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.history_projection import (
    project_conversation,
    project_current,
)


def test_project_current_roundtrip(tmp_path):
    auth = open_journal_authority(
        tmp_path,
        conversation_id="conv_1",
        task_id="task_1",
        run_id="run_1",
        thread_id="thread_1",
        turn_id="turn_1",
    )
    auth.emit("thread.message.sent", {"content": "hi", "turn_index": 0})
    auth.emit(
        "model.attempt.completed",
        {
            "attempt_id": "a1",
            "terminal_reason": "tool_calls",
            "content": "",
            "reasoning": "think",
            "tool_calls": [
                {
                    "tool_name": "Read",
                    "tool_input": "{}",
                    "tool_output": "ok",
                    "status": "succeeded",
                }
            ],
            "is_final": False,
            "usage": {},
        },
    )

    turns = project_current(auth)

    assert turns[0] == {"role": "user", "content": "hi", "turn_index": 0}
    assert turns[1]["role"] == "assistant"
    assert turns[1]["tool_calls"][0]["tool_name"] == "Read"


def test_project_conversation_aggregates_runs(tmp_path):
    first = open_journal_authority(
        tmp_path,
        conversation_id="conv_9",
        task_id="task_1",
        run_id="run_1",
        thread_id="thread_1",
        turn_id="turn_1",
    )
    first.emit("thread.message.sent", {"content": "first", "turn_index": 0})
    first.emit(
        "model.attempt.completed",
        {
            "attempt_id": "a1",
            "terminal_reason": "completed",
            "content": "one",
            "reasoning": "",
            "tool_calls": [],
            "is_final": True,
            "usage": {},
        },
    )
    second = open_journal_authority(
        tmp_path,
        conversation_id="conv_9",
        task_id="task_2",
        run_id="run_2",
        thread_id="thread_2",
        turn_id="turn_2",
    )
    second.emit("thread.message.sent", {"content": "second", "turn_index": 0})

    turns = project_conversation(tmp_path, "conv_9")

    assert [turn["content"] for turn in turns if turn["role"] == "user"] == [
        "first",
        "second",
    ]
