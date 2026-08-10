from datetime import datetime, timezone

from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
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
    second.emit(
        "model.attempt.completed",
        {
            "attempt_id": "a2",
            "terminal_reason": "completed",
            "content": "two",
            "reasoning": "",
            "tool_calls": [],
            "is_final": True,
            "usage": {},
        },
    )

    turns = project_conversation(tmp_path, "conv_9")

    assert [(turn["role"], turn["content"]) for turn in turns] == [
        ("user", "first"),
        ("assistant", "one"),
        ("user", "second"),
        ("assistant", "two"),
    ]


def test_project_conversation_equal_recorded_at_uses_sequence_then_event_id(tmp_path):
    fixed = datetime(2026, 8, 10, tzinfo=timezone.utc)
    first = open_journal_authority(
        tmp_path,
        conversation_id="conv_tie",
        task_id="task_z",
        run_id="run_z",
        thread_id="thread_z",
        turn_id="turn_z",
    )
    second = open_journal_authority(
        tmp_path,
        conversation_id="conv_tie",
        task_id="task_a",
        run_id="run_a",
        thread_id="thread_a",
        turn_id="turn_a",
    )
    # Directory traversal sees task_a/run_a first, but the tie-break must place
    # sequence 1 before sequence 2 regardless of directory order.
    second.append_group([
        EventEnvelope(
            event_id="evt_z",
            event_type="thread.message.sent",
            sequence=0,
            recorded_at=fixed,
            conversation_id="conv_tie",
            task_id="task_a",
            run_id="run_a",
            thread_id="thread_a",
            turn_id="turn_a",
            payload={"content": "sequence-two", "turn_index": 1},
        ),
        EventEnvelope(
            event_id="evt_a",
            event_type="thread.message.sent",
            sequence=0,
            recorded_at=fixed,
            conversation_id="conv_tie",
            task_id="task_a",
            run_id="run_a",
            thread_id="thread_a",
            turn_id="turn_a",
            payload={"content": "event-z", "turn_index": 2},
        ),
    ])
    first.emit("thread.message.sent", {"content": "sequence-one", "turn_index": 0})
    # Replace the writer-assigned time so all three events share the exact timestamp.
    events = first.read_after(0)
    tied = events[0].model_copy(update={"recorded_at": fixed, "event_id": "evt_m"})
    journal_dir = tmp_path / "conversations" / "conv_tie" / "tasks" / "task_z" / "runs" / "run_z" / "journal"
    for path in journal_dir.glob("events-*.jsonl"):
        path.unlink()
    (journal_dir / "index.json").unlink(missing_ok=True)
    first = open_journal_authority(
        tmp_path, conversation_id="conv_tie", task_id="task_z", run_id="run_z",
        thread_id="thread_z", turn_id="turn_z",
    )
    first.append_group([tied])

    turns = project_conversation(tmp_path, "conv_tie")

    assert [turn["content"] for turn in turns] == [
        "sequence-one",
        "sequence-two",
        "event-z",
    ]
