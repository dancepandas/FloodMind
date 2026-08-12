"""P8 Task 2 — SDK Agent 标准身份 + events_after + resume 契约。"""

from floodmind.agent.api import Agent
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import AgentLoopState, ModelEvent
from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.services.checkpoint_service import CheckpointService
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def _agent(workspace, tmp_path):
    mc = ModelClient(
        api_key="mock-key", base_url="https://mock.api/v1", model_name="mock-model"
    )
    mc.stream_chat = lambda *args, **kwargs: iter([
        ModelEvent(type="token", content="ok"),
        ModelEvent(type="done"),
    ])
    return Agent(llm=mc, session_id="sdk-sess", bare=True, workspace=workspace)


def _seed_checkpoint(workspace, tmp_path, *, run_id="run_1"):
    """构造一份合法的 checkpoint（manifest + reducer snapshot）。"""
    runtime_dir = tmp_path / "runtime"
    authority = open_journal_authority(
        runtime_dir, conversation_id="c", task_id="t",
        run_id=run_id, thread_id="th", turn_id="tu",
    )
    run_state = authority.replay()  # 与 cursor=0 一致
    svc = CheckpointService(base_dir=str(workspace.session_root))
    record = svc.save(
        AgentLoopState(session_id="sdk-sess", run_id=run_id),
        journal_cursor=authority.cursor(),
        reducer_version="1",
        tool_registry_version="",
        run_state=run_state,
        metadata={
            "conversation_id": "c", "task_id": "t", "run_id": run_id,
            "thread_id": "th", "turn_id": "tu", "runtime_dir": str(runtime_dir),
        },
    )
    return record, authority


def test_agent_standard_identity_after_run(tmp_path):
    workspace = Workspace.from_cwd(session_id="sdk-sess").ensure()
    agent = _agent(workspace, tmp_path)
    out = agent.run("hi")
    assert isinstance(out, str)
    assert agent.run_id and agent.run_id.startswith("run_")
    assert isinstance(agent.conversation_id, str)


def test_agent_events_after_replays_committed(tmp_path):
    workspace = Workspace.from_cwd(session_id="sdk-sess").ensure()
    agent = _agent(workspace, tmp_path)
    agent.run("hi")
    events = agent.events_after(0)
    assert isinstance(events, list)
    if events:
        assert all(e["sequence"] >= 0 for e in events)
        seqs = [e["sequence"] for e in events]
        assert seqs == sorted(seqs)


def test_agent_resume_returns_string_and_binds_authority(tmp_path):
    """Agent.resume() 必须真正走 ResumeService 路径，不能再次落到被拒绝的
    stream(resume_checkpoint_id=...) 路径。

    预置 workspace + 一份合法 checkpoint + 残余 journal events；resume 后：
      - 返回字符串；
      - 底层 _journal_authority 绑到 resumed run 的 conversation/run；
      - resume.started / resume.completed 事件落到 journal。
    """
    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    record, seeded_auth = _seed_checkpoint(workspace, tmp_path)

    # 模拟原 run 已经有些事件，验证 resume 会 replay 后再续接。
    seeded_auth.emit("thread.message.sent", {"content": "prior turn", "turn_index": 0})

    mc = ModelClient(
        api_key="mock-key", base_url="https://mock.api/v1", model_name="mock-model"
    )
    mc.stream_chat = lambda *args, **kwargs: iter([
        ModelEvent(type="token", content="resumed"),
        ModelEvent(type="done"),
    ])
    agent = Agent(
        llm=mc, session_id="sdk-sess", bare=True, workspace=workspace,
    )

    # 捕获公开 resume 事件（v2.0.1：Agent.resume 在 orchestrator event_bus 上发 resume started/completed）
    resume_events = []
    exec_bus = agent._agent._orchestrator_executor.event_bus
    orig_emit = exec_bus.emit
    exec_bus.emit = lambda ev: (resume_events.append(ev), orig_emit(ev))[1]

    out = agent.resume(record.checkpoint_id, user_message="continue please")
    assert isinstance(out, str)

    # 公开流上发出 resume started/completed，且携带 checkpoint_id
    resume_msgs = [e for e in resume_events if e["type"] == "resume"]
    assert {e["status"] for e in resume_msgs} == {"started", "completed"}
    assert all(e["checkpoint_id"] == record.checkpoint_id for e in resume_msgs)
    assert resume_msgs[0]["status"] == "started"  # started 先于 completed

    # resumed authority 已绑到 executor，identity 与 manifest 一致
    auth = agent._agent._journal_authority
    assert auth is not None
    assert auth.conversation_id == "c"
    assert auth.run_id == "run_1"
    assert auth.cursor() > 0
    types = [e.event_type for e in auth.read_after(0)]
    assert "resume.started" in types
    assert "resume.completed" in types