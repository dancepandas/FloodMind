from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.history_projection import project_current
from floodmind.agent.runtime.reducer import reduce, initial_run_state


def test_tool_and_terminal_event_sequence(tmp_path):
    auth = open_journal_authority(
        tmp_path,
        conversation_id="c",
        task_id="t",
        run_id="r",
        thread_id="th",
        turn_id="tu",
    )
    auth.emit(
        "model.attempt.completed",
        {
            "attempt_id": "a1",
            "terminal_reason": "tool_calls",
            "content": "",
            "reasoning": "",
            "tool_calls": [],
            "is_final": False,
            "usage": {},
        },
    )
    auth.emit(
        "tool.execution.started",
        {
            "transaction_id": "ttx_1",
            "call_id": "c1",
            "tool_id": "builtin:Read",
            "arguments": "{}",
        },
    )
    auth.emit(
        "tool.execution.completed",
        {
            "transaction_id": "ttx_1",
            "call_id": "c1",
            "tool_id": "builtin:Read",
            "status": "succeeded",
            "result_summary": "ok",
            "full_ref": "",
            "artifacts": ["art_1"],
        },
    )
    auth.emit(
        "run.completed",
        {"final_output": "done", "terminal_reason": "completed"},
    )
    state = auth.replay(0)
    assert state.status.value == "completed"
    assert state.artifacts == ["art_1"]
    assert state.last_committed_sequence == 4
