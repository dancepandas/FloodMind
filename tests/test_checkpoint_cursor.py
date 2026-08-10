import json

import pytest
from pydantic import ValidationError

from floodmind.agent.native.executor import project_run_state_to_loop_state
from floodmind.agent.native.types import AgentLoopState, ToolCall
from floodmind.agent.runtime.contracts.checkpoints import CheckpointManifest
from floodmind.agent.runtime.contracts.run_state import PendingApproval, RunStatus
from floodmind.agent.runtime.reducer import initial_run_state
from floodmind.agent.runtime.services.checkpoint_service import (
    CheckpointConsistencyError,
    CheckpointService,
)
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def _authority(tmp_path):
    return open_journal_authority(
        tmp_path,
        conversation_id="c",
        task_id="t",
        run_id="run_1",
        thread_id="th",
        turn_id="tu",
    )


def _emit_completed_turn(auth):
    auth.emit("thread.message.sent", {"content": "hi", "turn_index": 0})
    auth.emit(
        "model.attempt.completed",
        {
            "attempt_id": "a1",
            "terminal_reason": "completed",
            "content": "ok",
            "reasoning": "",
            "tool_calls": [],
            "is_final": True,
            "usage": {},
        },
    )


def test_checkpoint_binds_cursor_and_runstate_snapshot(tmp_path):
    auth = _authority(tmp_path)
    _emit_completed_turn(auth)
    run_state = auth.replay()
    svc = CheckpointService(base_dir=str(tmp_path))
    loop_state = AgentLoopState(session_id="sess_1", run_id="run_1")

    record = svc.save(
        loop_state,
        journal_cursor=auth.cursor(),
        reducer_version="1",
        run_state=run_state,
    )

    assert record.journal_cursor == 2
    assert record.reducer_version == "1"
    state = svc.load("sess_1", record.checkpoint_id)
    assert state["checkpoint_id"] == record.checkpoint_id
    assert state["journal_cursor"] == 2
    manifest = svc.load_manifest("sess_1", record.checkpoint_id)
    assert manifest.reducer_version == "1"
    snapshot = svc.load_run_state("sess_1", record.checkpoint_id)
    assert snapshot == run_state


def test_unbound_manifest_is_rejected():
    with pytest.raises(ValidationError):
        CheckpointManifest.model_validate(
            {
                "checkpoint_id": "ckpt",
                "session_id": "sess",
                "run_id": "run",
                "status": "created",
                "iteration": 0,
                "created_at": "2026-08-10T00:00:00Z",
            }
        )


def test_checkpoint_snapshot_replays_only_suffix(tmp_path):
    auth = _authority(tmp_path)
    _emit_completed_turn(auth)
    base = auth.replay()
    svc = CheckpointService(base_dir=str(tmp_path))
    record = svc.save(
        AgentLoopState(session_id="sess_1", run_id="run_1"),
        journal_cursor=auth.cursor(),
        reducer_version="1",
        run_state=base,
        metadata={
            "conversation_id": "c",
            "task_id": "t",
            "run_id": "run_1",
            "thread_id": "th",
            "turn_id": "tu",
            "runtime_dir": str(tmp_path),
        },
    )
    auth.emit("thread.message.sent", {"content": "again", "turn_index": 1})

    resumed = svc.replay_from_checkpoint(auth, "sess_1", record.checkpoint_id)

    assert resumed.last_committed_sequence == 3
    assert len(resumed.turns) == 3
    assert resumed.turns[-1]["content"] == "again"
    assert resumed == auth.replay()


def test_checkpoint_projection_disagreement_fails_closed(tmp_path):
    auth = _authority(tmp_path)
    _emit_completed_turn(auth)
    run_state = auth.replay()
    svc = CheckpointService(base_dir=str(tmp_path))
    record = svc.save(
        AgentLoopState(session_id="sess_1", run_id="run_1"),
        journal_cursor=auth.cursor(),
        reducer_version="1",
        run_state=run_state,
    )
    snapshot_path = (
        tmp_path / "sess_1" / "checkpoints" / record.checkpoint_id / "run_state.json"
    )
    corrupted = json.loads(snapshot_path.read_text(encoding="utf-8"))
    corrupted["status"] = "failed"
    snapshot_path.write_text(json.dumps(corrupted), encoding="utf-8")

    with pytest.raises(CheckpointConsistencyError, match="projection"):
        svc.replay_from_checkpoint(auth, "sess_1", record.checkpoint_id)


def test_projection_clears_stale_pending_fields():
    loop = AgentLoopState(
        status="awaiting_permission",
        pending_ask_id="stale-ask",
        pending_tool_calls=[ToolCall(id="call", name="Bash", arguments={"command": "pwd"})],
    )
    loop.pending_tool_transaction_id = "stale-tx"
    run_state = initial_run_state("run_1")
    run_state.status = RunStatus.awaiting_model
    run_state.last_committed_sequence = 4

    projected = project_run_state_to_loop_state(loop, run_state)

    assert projected.status == "awaiting_llm"
    assert projected.pending_tool_calls == []
    assert projected.pending_ask_id is None
    assert projected.pending_tool_transaction_id == ""
    assert projected.journal_cursor == 4


@pytest.mark.parametrize(
    ("run_status", "loop_status"),
    [
        (RunStatus.created, "created"),
        (RunStatus.awaiting_model, "awaiting_llm"),
        (RunStatus.streaming_model, "awaiting_llm"),
        (RunStatus.awaiting_tool, "awaiting_tool"),
        (RunStatus.awaiting_approval, "awaiting_permission"),
        (RunStatus.executing_tool, "awaiting_tool"),
        (RunStatus.compacting, "context_compress"),
        (RunStatus.paused, "paused"),
        (RunStatus.completed, "completed"),
        (RunStatus.failed, "failed"),
    ],
)
def test_projection_maps_run_status(run_status, loop_status):
    loop = AgentLoopState()
    run_state = initial_run_state("run_1")
    run_state.status = run_status
    if run_status == RunStatus.awaiting_approval:
        run_state.pending_approvals = [
            PendingApproval(ask_id="ask", call_id="call", tool_name="Bash")
        ]

    projected = project_run_state_to_loop_state(loop, run_state)

    assert projected.status == loop_status
    assert projected.pending_ask_id == ("ask" if run_state.pending_approvals else None)
