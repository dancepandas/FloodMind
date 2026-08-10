"""ResumeService 测试（目标 §16.4 全流程 + lease fencing + 事件 + 版本校验）。"""

import pytest

from floodmind.agent.native.types import AgentLoopState
from floodmind.agent.runtime.reducer import initial_run_state
from floodmind.agent.runtime.services.checkpoint_service import (
    CheckpointConsistencyError,
    CheckpointService,
)
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.resume_service import (
    ResumeBusyError,
    ResumeService,
    open_lease,
)


def _authority(tmp_path):
    return open_journal_authority(
        tmp_path,
        conversation_id="c",
        task_id="t",
        run_id="run_1",
        thread_id="th",
        turn_id="tu",
    )


def _save_checkpoint(tmp_path, svc, auth, *, tool_registry_version="tools-v1",
                     session_id="sess_1"):
    run_state = auth.replay()
    return svc.save(
        AgentLoopState(session_id=session_id, run_id="run_1"),
        journal_cursor=auth.cursor(),
        reducer_version="1",
        tool_registry_version=tool_registry_version,
        run_state=run_state,
        metadata={
            "conversation_id": "c", "task_id": "t", "run_id": "run_1",
            "thread_id": "th", "turn_id": "tu", "runtime_dir": str(tmp_path),
        },
    )


# ── lease fencing ─────────────────────────────────────────────────

def test_lease_is_fenced_until_release(tmp_path):
    lease = open_lease(tmp_path, "run_1", owner="p1", ttl_seconds=300)
    assert lease.acquired
    with pytest.raises(ResumeBusyError):
        open_lease(tmp_path, "run_1", owner="p2", ttl_seconds=300)  # 已被 p1 持有
    lease.release()
    lease2 = open_lease(tmp_path, "run_1", owner="p2", ttl_seconds=300)
    assert lease2.acquired


def test_lease_expired_can_be_reacquired(tmp_path):
    lease = open_lease(tmp_path, "run_1", owner="p1", ttl_seconds=-10)  # 已过期
    assert lease.acquired
    lease2 = open_lease(tmp_path, "run_1", owner="p2", ttl_seconds=300)
    assert lease2.acquired


# ── resume 全流程 ────────────────────────────────────────────────

def test_resume_reconciles_and_replays(tmp_path):
    auth = _authority(tmp_path)
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    auth.emit("tool.call.proposed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "idempotency_key": "ik"})
    auth.emit("tool.execution.indeterminate", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "reason": "timeout", "idempotency_key": "ik"})
    outcome = ResumeService().resume(
        runtime_dir=tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="th", turn_id="tu", checkpoint_id="",
    )
    assert outcome.reconciled.indeterminate_resolved >= 1
    assert outcome.run_state.pending_tool_transactions == []   # reconcile 后无僵尸
    types = [e.event_type for e in auth.read_after(0)]
    assert "resume.started" in types and "resume.completed" in types


def test_resume_settles_pre_execution_zombies(tmp_path):
    """F：proposed/validated/permission_evaluated 僵尸在 reconcile 集合之外，resume 必须额外落定。"""
    auth = _authority(tmp_path)
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    auth.emit("tool.call.proposed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "idempotency_key": "ik"})
    outcome = ResumeService().resume(
        runtime_dir=tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="th", turn_id="tu", checkpoint_id="",
    )
    assert outcome.run_state.pending_tool_transactions == []  # 无僵尸残留
    ind = [e for e in auth.read_after(0)
           if e.event_type == "tool.execution.indeterminate"
           and e.payload.get("reason") == "reconciled_pending"]
    assert len(ind) == 1 and ind[0].payload["transaction_id"] == "ttx_1"
    committed = [e for e in auth.read_after(0)
                 if e.event_type == "tool.result.committed"
                 and e.payload["transaction_id"] == "ttx_1"]
    assert len(committed) == 1 and committed[0].payload["verdict"] == "failed"


def test_resume_emits_started_and_completed_events(tmp_path):
    auth = _authority(tmp_path)
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    outcome = ResumeService().resume(
        runtime_dir=tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="th", turn_id="tu", checkpoint_id="",
    )
    events = auth.read_after(0)
    types = [e.event_type for e in events]
    assert "resume.started" in types and "resume.completed" in types
    assert types.index("resume.started") < types.index("resume.completed")
    started = next(e for e in events if e.event_type == "resume.started")
    assert started.payload["checkpoint_id"] == ""
    assert started.payload["cursor"] >= 0
    completed = next(e for e in events if e.event_type == "resume.completed")
    # resume.completed 在 append 前记录 cursor（前一个事件尾），journal_cursor 为其后。
    assert completed.payload["cursor"] == outcome.journal_cursor - 1


def test_resume_appends_user_message_as_new_event(tmp_path):
    auth = _authority(tmp_path)
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    outcome = ResumeService().resume(
        runtime_dir=tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="th", turn_id="tu", checkpoint_id="", user_message="续接指令",
    )
    msgs = [e for e in auth.read_after(0) if e.event_type == "thread.message.sent"]
    assert msgs[-1].payload["content"] == "续接指令"
    assert msgs[-1].payload["turn_index"] == 1
    assert outcome.run_state.turns[-1]["content"] == "续接指令"
    assert outcome.run_state.turns[-1]["role"] == "user"


def test_resume_releases_lease_on_failure(tmp_path):
    auth = _authority(tmp_path)
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    with pytest.raises(ValueError):
        ResumeService().resume(
            runtime_dir=tmp_path, conversation_id="c", task_id="t", run_id="run_1",
            thread_id="th", turn_id="tu", checkpoint_id="ckpt-1",
        )
    # lease 已释放，可重新获取
    lease = open_lease(tmp_path, "run_1", owner="p2")
    assert lease.acquired


# ── checkpoint tool_registry_version 校验 ───────────────────────

def test_replay_from_checkpoint_tool_registry_version_validation(tmp_path):
    svc = CheckpointService(base_dir=str(tmp_path))
    auth = _authority(tmp_path)
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    record = _save_checkpoint(tmp_path, svc, auth, tool_registry_version="tools-v2")

    manifest = svc.load_manifest("sess_1", record.checkpoint_id)
    assert manifest.tool_registry_version == "tools-v2"

    # expected 空 → 跳过校验
    resumed = svc.replay_from_checkpoint(auth, "sess_1", record.checkpoint_id)
    assert resumed.last_committed_sequence == auth.cursor()
    # expected 匹配 → 通过
    resumed = svc.replay_from_checkpoint(
        auth, "sess_1", record.checkpoint_id,
        expected_tool_registry_version="tools-v2")
    assert resumed.last_committed_sequence == auth.cursor()
    # expected 不匹配 → fail closed
    with pytest.raises(CheckpointConsistencyError, match="tool registry"):
        svc.replay_from_checkpoint(
            auth, "sess_1", record.checkpoint_id,
            expected_tool_registry_version="tools-v3")


def test_resume_with_checkpoint_id_validates_and_continues(tmp_path):
    svc = CheckpointService(base_dir=str(tmp_path))
    auth = _authority(tmp_path)
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    record = _save_checkpoint(tmp_path, svc, auth, tool_registry_version="tools-v1")

    outcome = ResumeService().resume(
        runtime_dir=tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="th", turn_id="tu", checkpoint_id=record.checkpoint_id,
        session_id="sess_1", checkpoint_service=svc,
        expected_tool_registry_version="tools-v1",
    )
    assert outcome.journal_cursor > record.journal_cursor
    contents = [e.payload["content"] for e in auth.read_after(0)
                if e.event_type == "thread.message.sent"]
    assert contents == ["q1"]
    # run_state 反映 resume.completed 之前的事件尾；journal_cursor 包含该 meta 事件。
    assert outcome.run_state.last_committed_sequence == outcome.journal_cursor - 1


def test_resume_checkpoint_tool_registry_mismatch_fails_closed(tmp_path):
    svc = CheckpointService(base_dir=str(tmp_path))
    auth = _authority(tmp_path)
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    record = _save_checkpoint(tmp_path, svc, auth, tool_registry_version="tools-v1")

    with pytest.raises(CheckpointConsistencyError, match="tool registry"):
        ResumeService().resume(
            runtime_dir=tmp_path, conversation_id="c", task_id="t", run_id="run_1",
            thread_id="th", turn_id="tu", checkpoint_id=record.checkpoint_id,
            session_id="sess_1", checkpoint_service=svc,
            expected_tool_registry_version="tools-v2",
        )


# ── lease 在成功 resume 后仍需显式 release（CLI 调用方职责）──────────

def test_resume_holds_lease_then_release_unblocks_next_resume(tmp_path):
    """成功 resume 后 lease 仍被持有（fencing 覆盖整个 run）；
    显式 release 后同一 run 可再次 resume，无 stuck lease。"""
    auth = _authority(tmp_path)
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    outcome = ResumeService().resume(
        runtime_dir=tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="th", turn_id="tu", checkpoint_id="",
    )
    # fencing：run 进行期间，其他 owner 无法抢占同一 run
    with pytest.raises(ResumeBusyError):
        open_lease(tmp_path, "run_1", owner="other",
                   conversation_id="c", task_id="t")
    # 释放后同一 run 的后续 resume 成功
    outcome.lease.release()
    outcome2 = ResumeService().resume(
        runtime_dir=tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="th", turn_id="tu", checkpoint_id="",
    )
    assert outcome2.journal_cursor > outcome.journal_cursor


def test_cli_resume_releases_lease_after_run(tmp_path, monkeypatch):
    """CLI resume 路径在 run 到达终态后必须释放 lease（try/finally），
    否则 lease 文件残留 300s TTL，同一 run 的后续 resume 被 ResumeBusyError 阻塞。"""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from click.testing import CliRunner

    from floodmind.agent.runtime.services.checkpoint_service import CheckpointService
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority
    from floodmind.agent.runtime.services.runtime_layout import lease_file
    from floodmind.agent.runtime.services.workspace_service import build_folder_workspace
    from floodmind.cli import main

    # 真实 checkpoint：journal 在 tmp_path，checkpoint 在 workspace.session_root
    ws = build_folder_workspace("sess_1", primary_dir=tmp_path)
    svc = CheckpointService(base_dir=str(ws.session_root))
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    record = svc.save(
        AgentLoopState(session_id="sess_1", run_id="run_1"),
        journal_cursor=auth.cursor(), reducer_version="1",
        run_state=auth.replay(),
        metadata={
            "conversation_id": "c", "task_id": "t", "run_id": "run_1",
            "thread_id": "th", "turn_id": "tu", "runtime_dir": str(tmp_path),
        },
    )

    executor = SimpleNamespace(
        run_from_state=lambda context, state, run_state=None: SimpleNamespace(
            final_output="done"
        )
    )
    agent = SimpleNamespace(
        _orchestrator_executor=executor,
        _current_run_context=None,
        _journal_authority=None,
        _last_loop_state=None,
    )

    monkeypatch.setattr("floodmind.cli._validate_api_key", lambda: None)
    monkeypatch.setattr("floodmind.cli._build_cli_workspace", lambda sid: ws)
    monkeypatch.setattr(
        "floodmind.agent.native.model_client.ModelClient.from_settings",
        lambda **kw: MagicMock(),
    )
    monkeypatch.setattr("floodmind.agent.create_flood_agent", lambda **kw: agent)

    result = CliRunner().invoke(
        main,
        ["run", "续接", "--resume", "sess_1", "--checkpoint", record.checkpoint_id],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "done" in result.output
    # run 到达终态后 lease 文件已被释放：同一 run 可重新获取 lease
    lease_path = lease_file(tmp_path, "c", "t", "run_1")
    assert not lease_path.exists()
    lease2 = open_lease(tmp_path, "run_1", owner="next",
                        conversation_id="c", task_id="t")
    assert lease2.acquired
