"""Tests for CheckpointService and AgentLoopState serialization."""

import inspect
import tempfile
from pathlib import Path

import pytest

from floodmind.agent.native.types import AgentLoopState, ExecutionPlan
from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult
from floodmind.agent.runtime.services.checkpoint_service import (
    CheckpointNotFoundError,
    CheckpointService,
)


class TestCheckpointService:
    def _make_service(self):
        tmp = tempfile.mkdtemp()
        return CheckpointService(base_dir=tmp, keep_count=3), tmp

    def _make_state(self, session_id: str = "test-session", status: str = "awaiting_llm"):
        return AgentLoopState(
            session_id=session_id,
            run_id="run-1",
            status=status,
            iteration=2,
            messages=[{"role": "user", "content": "hello"}],
            tool_results=[
                ToolResult(tool_call_id="tc1", name="test_tool", content="ok", status="completed"),
            ],
        )

    def test_save_and_load_state(self):
        svc, _ = self._make_service()
        state = self._make_state()
        record = svc.save(state)

        assert record.session_id == "test-session"
        assert record.status == "awaiting_llm"
        assert record.iteration == 2

        loaded = svc.load("test-session", record.checkpoint_id, state_class=AgentLoopState)
        assert loaded.session_id == "test-session"
        assert loaded.status == "awaiting_llm"
        assert loaded.iteration == 2
        assert len(loaded.messages) == 1
        assert loaded.messages[0]["content"] == "hello"
        assert len(loaded.tool_results) == 1
        assert loaded.tool_results[0].name == "test_tool"

    def test_load_latest(self):
        svc, _ = self._make_service()
        state1 = self._make_state(status="awaiting_llm")
        svc.save(state1)

        state2 = self._make_state(status="awaiting_tool")
        svc.save(state2)

        loaded = svc.load("test-session", state_class=AgentLoopState)
        assert loaded.status == "awaiting_tool"

    def test_load_not_found(self):
        svc, _ = self._make_service()
        with pytest.raises(CheckpointNotFoundError):
            svc.load("nonexistent")

    def test_list_and_cleanup(self):
        svc, _ = self._make_service()
        for i in range(5):
            state = self._make_state(status="awaiting_llm")
            state.iteration = i
            svc.save(state)

        summaries = svc.list("test-session")
        # keep_count=3, 只保留最近 3 个
        assert len(summaries) == 3
        # 按时间倒序
        assert summaries[0].iteration == 4
        assert summaries[1].iteration == 3
        assert summaries[2].iteration == 2

    def test_save_signature_has_no_file_snapshot_parameter(self):
        sig = inspect.signature(CheckpointService.save)
        assert "files_dirs" not in sig.parameters

    def test_save_does_not_create_files_snapshot(self):
        svc, base_dir = self._make_service()
        session_id = "test-session"
        state = self._make_state(session_id=session_id)
        record = svc.save(state)

        checkpoint_dir = Path(base_dir) / session_id / "checkpoints" / record.checkpoint_id
        assert (checkpoint_dir / "state.json").exists()
        assert (checkpoint_dir / "manifest.json").exists()
        assert not (checkpoint_dir / "files").exists()
        assert record.files_snapshot_path is None

        manifest = svc.load_manifest(session_id, record.checkpoint_id)
        assert manifest.files_snapshot_dir is None
        assert manifest.files_snapshot_base_dirs == []
        assert svc.list(session_id)[0].has_files_snapshot is False

    def test_rollback_files_noops_for_state_only_checkpoint(self):
        svc, base_dir = self._make_service()
        session_id = "test-session"
        workspace = Path(base_dir) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        original_file = workspace / "data.txt"
        original_file.write_text("version 1", encoding="utf-8")

        state = self._make_state(session_id=session_id)
        record = svc.save(state)

        original_file.write_text("version 2", encoding="utf-8")
        restored = svc.rollback_files(session_id, record.checkpoint_id)
        assert restored == []
        assert original_file.read_text(encoding="utf-8") == "version 2"

    def test_execution_plan_in_state(self):
        svc, _ = self._make_service()
        plan = ExecutionPlan(
            plan_id="p1",
            steps=[
                {"step_id": "s1", "status": "completed"},
                {"step_id": "s2", "status": "pending"},
            ],
        )
        state = self._make_state()
        state.plan = plan

        record = svc.save(state)
        loaded = svc.load("test-session", record.checkpoint_id, state_class=AgentLoopState)

        assert loaded.plan is not None
        assert loaded.plan.plan_id == "p1"
        assert loaded.plan.find_step("s1")["status"] == "completed"
        assert loaded.plan.find_step("s2")["status"] == "pending"


class TestAgentLoopStateSerialization:
    def test_tool_result_with_metadata_roundtrip(self):
        svc, _ = TestCheckpointService()._make_service()
        state = AgentLoopState(
            session_id="s1",
            status="awaiting_permission",
            pending_ask_id="ask-123",
            pending_tool_calls=[ToolCall(id="tc1", name="Bash", arguments={"command": "ls"})],
        )
        record = svc.save(state)
        loaded = svc.load("s1", record.checkpoint_id, state_class=AgentLoopState)

        assert loaded.pending_ask_id == "ask-123"
        assert len(loaded.pending_tool_calls) == 1
        assert loaded.pending_tool_calls[0].name == "Bash"
