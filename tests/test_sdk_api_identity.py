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


def test_interleaved_agents_bind_their_own_memory_without_polluting_caller(tmp_path):
    import contextvars
    from floodmind.tools.base_tools import get_memory_instance, set_memory_instance

    def run():
        sentinel = object()
        set_memory_instance(sentinel)
        agents = [
            _agent(Workspace.from_folder(tmp_path / name, session_id=name).ensure(), tmp_path)
            for name in ("a", "b")
        ]
        seen = []
        for agent in agents:
            def stream_chat(*args, **kwargs):
                seen.append(get_memory_instance())
                return iter([ModelEvent(type="token", content="ok"), ModelEvent(type="done")])
            agent._agent._model_client.stream_chat = stream_chat
        for agent in [agents[0], agents[1], agents[0]]:
            agent.run("hi")
        assert seen == [agents[0]._agent.memory, agents[1]._agent.memory, agents[0]._agent.memory]
        assert get_memory_instance() is sentinel

    contextvars.copy_context().run(run)


def test_interleaved_agents_search_only_their_own_journal(tmp_path):
    import contextvars
    from floodmind.tools.base_tools import search_memory

    def run():
        agents = [
            _agent(Workspace.from_folder(tmp_path / name, session_id=name).ensure(), tmp_path)
            for name in ("a", "b")
        ]
        agents[0].run("isolation-marker private-a")
        agents[1].run("isolation-marker private-b")
        observed = []
        for agent in agents:
            def stream_chat(*args, **kwargs):
                observed.append(search_memory.func(keywords="isolation-marker"))
                return iter([ModelEvent(type="token", content="ok"), ModelEvent(type="done")])
            agent.raw._model_client.stream_chat = stream_chat
        agents[0].run("search previous history")
        agents[1].run("search previous history")
        agents[0].run("search previous history again")
        assert "private-a" in observed[0] and "private-b" not in observed[0]
        assert "private-b" in observed[1] and "private-a" not in observed[1]
        assert "private-a" in observed[2] and "private-b" not in observed[2]

    contextvars.copy_context().run(run)


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

    # SDK 保留只读回放句柄；底层活跃写者在运行结束后解除绑定。
    assert agent._agent._journal_authority is None
    auth = agent._journal_authority
    assert auth is not None
    assert auth.conversation_id == "c"
    assert auth.run_id == "run_1"
    assert auth.cursor() > 0
    types = [e.event_type for e in auth.read_after(0)]
    assert "resume.started" in types
    assert "resume.completed" in types


def test_resume_binds_memory_and_restores_caller_context(tmp_path, monkeypatch):
    import contextvars
    from floodmind.tools.base_tools import get_memory_instance, set_memory_instance

    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    record, _ = _seed_checkpoint(workspace, tmp_path)
    agent = _agent(workspace, tmp_path)
    executor = agent._agent._orchestrator_executor
    original = executor.run_from_state
    observed = {}

    def run_from_state(context, state, **kwargs):
        observed["memory"] = get_memory_instance()
        assert agent._agent.enqueue_user_message("queued during resume")
        observed["pending"] = agent._agent.memory.get_pending_user_messages()
        assert context.runtime_context.path_service is agent._agent._path_service
        return original(context, state, **kwargs)

    monkeypatch.setattr(executor, "run_from_state", run_from_state)

    def run():
        sentinel = object()
        set_memory_instance(sentinel)
        assert agent.resume(record.checkpoint_id) == "ok"
        assert get_memory_instance() is sentinel

    contextvars.copy_context().run(run)
    assert observed["memory"] is agent._agent.memory
    assert "queued during resume" in observed["pending"]
    assert agent._agent._journal_authority is None
    assert executor._journal_authority is None
    assert not agent._agent.enqueue_user_message("after resume")


def test_resume_routes_permission_requests_to_sdk_host_and_cleans_emitter(tmp_path):
    from floodmind.agent.runtime.contracts.permissions import PermissionAskRequest
    from floodmind.agent.runtime.services.ask_service import get_ask_service

    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    record, _ = _seed_checkpoint(workspace, tmp_path)
    agent = _agent(workspace, tmp_path)
    service = get_ask_service()
    observed = []

    def on_event(event):
        observed.append(event)
        if event["type"] == "permission_ask":
            assert agent.respond_permission(event["ask_id"], True)

    agent._on_event = on_event

    def stream_chat(*args, **kwargs):
        ask_id = service.start_ask(
            PermissionAskRequest(session_id="sdk-sess", tool_name="ContractApproval",
                                 reason="host approval", tool_input={}),
            journal_authority=agent.raw._journal_authority,
        )
        assert ask_id is not None, "resume did not bind the approval emitter"
        assert service.wait_response(ask_id, timeout=0) is True
        return iter([ModelEvent(type="token", content="approved"), ModelEvent(type="done")])

    agent.raw._model_client.stream_chat = stream_chat
    assert agent.resume(record.checkpoint_id) == "approved"
    assert [e["status"] for e in observed if e["type"] == "resume"] == ["started", "completed"]
    assert any(e["type"] == "permission_ask" for e in observed)
    count = len(observed)
    authority = agent._journal_authority
    assert service.start_ask(
        PermissionAskRequest(session_id="sdk-sess", tool_name="AfterResume", reason="inactive"),
        journal_authority=authority,
    ) is None
    agent.raw._orchestrator_executor.event_bus.emit({"type": "after_resume"})
    assert len(observed) == count


def test_resume_failure_releases_lease_and_active_bindings(tmp_path, monkeypatch):
    import pytest
    from floodmind.agent.runtime.services.resume_service import open_lease

    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    record, _ = _seed_checkpoint(workspace, tmp_path)
    agent = _agent(workspace, tmp_path)
    executor = agent._agent._orchestrator_executor
    events = []
    executor.event_bus.add_listener(events.append)

    def fail(*args, **kwargs):
        raise RuntimeError("resume execution failed")

    monkeypatch.setattr(executor, "run_from_state", fail)
    with pytest.raises(RuntimeError, match="resume execution failed"):
        agent.resume(record.checkpoint_id)

    assert agent._agent._journal_authority is None
    assert executor._journal_authority is None
    assert agent._agent._current_run_context is None
    assert agent._agent._stream_lock.acquire(blocking=False)
    agent._agent._stream_lock.release()
    assert [e["status"] for e in events if e["type"] == "resume"] == ["started", "failed"]
    lease = open_lease(
        tmp_path / "runtime", "run_1", owner="next",
        conversation_id="c", task_id="t",
    )
    try:
        assert lease.acquired
    finally:
        lease.release()


def test_resume_assembly_failure_releases_lease_and_bindings(tmp_path, monkeypatch):
    import pytest
    from floodmind.agent.runtime.services.resume_service import open_lease

    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    record, _ = _seed_checkpoint(workspace, tmp_path)
    agent = _agent(workspace, tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("context assembly failed")

    monkeypatch.setattr(agent.raw, "_set_session_context", fail)
    with pytest.raises(RuntimeError, match="context assembly failed"):
        agent.resume(record.checkpoint_id)

    assert agent.raw._journal_authority is None
    assert agent.raw._orchestrator_executor._journal_authority is None
    assert agent.raw._current_run_context is None
    lease = open_lease(
        tmp_path / "runtime", "run_1", owner="next",
        conversation_id="c", task_id="t",
    )
    try:
        assert lease.acquired
    finally:
        lease.release()


def test_resume_session_binding_failure_stops_before_model_and_releases_lease(tmp_path, monkeypatch):
    import pytest
    from floodmind.tools import base_tools
    from floodmind.agent.runtime.services.resume_service import open_lease

    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    record, _ = _seed_checkpoint(workspace, tmp_path)
    agent = _agent(workspace, tmp_path)
    calls = []

    def stream_chat(*args, **kwargs):
        calls.append(1)
        return iter([ModelEvent(type="done")])

    def fail_binding(*args, **kwargs):
        raise RuntimeError("session binding failed")

    agent.raw._model_client.stream_chat = stream_chat
    monkeypatch.setattr(base_tools, "set_session_context", fail_binding)
    with pytest.raises(RuntimeError, match="session binding failed"):
        agent.resume(record.checkpoint_id)

    assert calls == []
    assert agent.raw._journal_authority is None
    assert agent.raw._orchestrator_executor._journal_authority is None
    assert agent.raw._current_run_context is None
    assert agent.raw._stream_lock.acquire(blocking=False)
    agent.raw._stream_lock.release()
    lease = open_lease(
        tmp_path / "runtime", "run_1", owner="next",
        conversation_id="c", task_id="t",
    )
    try:
        assert lease.acquired
    finally:
        lease.release()


def test_resume_cleanup_failure_restores_context_and_releases_lease(tmp_path, monkeypatch):
    import pytest
    from floodmind.agent.runtime.services.resume_service import open_lease

    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    record, _ = _seed_checkpoint(workspace, tmp_path)
    agent = _agent(workspace, tmp_path)
    previous_context = object()
    agent.raw._current_run_context = previous_context
    original_bind = agent.raw.memory.bind_journal
    events = []
    agent.raw._orchestrator_executor.event_bus.add_listener(events.append)

    def bind_journal(authority, runtime_dir, conversation_id):
        if authority is None:
            raise RuntimeError("memory cleanup failed")
        return original_bind(authority, runtime_dir, conversation_id)

    monkeypatch.setattr(agent.raw.memory, "bind_journal", bind_journal)
    with pytest.raises(RuntimeError, match="memory cleanup failed"):
        agent.resume(record.checkpoint_id)

    assert [e["status"] for e in events if e["type"] == "resume"] == ["started", "failed"]
    assert agent.raw._current_run_context is previous_context
    assert agent.raw._journal_authority is None
    assert agent.raw._orchestrator_executor._journal_authority is None
    assert agent.raw._stream_lock.acquire(blocking=False)
    agent.raw._stream_lock.release()
    lease = open_lease(
        tmp_path / "runtime", "run_1", owner="next",
        conversation_id="c", task_id="t",
    )
    try:
        assert lease.acquired
    finally:
        lease.release()


def test_closing_stream_keeps_instance_locked_until_worker_cleanup(tmp_path):
    import threading
    import pytest

    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    agent = _agent(workspace, tmp_path)
    blocked = threading.Event()
    proceed = threading.Event()
    cleaned = threading.Event()
    workers = []
    original_deactivate = agent.raw._deactivate_run_authority

    def stream_chat(*args, **kwargs):
        workers.append(threading.current_thread())
        yield ModelEvent(type="token", content="started")
        blocked.set()
        assert proceed.wait(5), "test did not release model"
        yield ModelEvent(type="done")

    def deactivate(authority):
        try:
            return original_deactivate(authority)
        finally:
            cleaned.set()

    agent.raw._model_client.stream_chat = stream_chat
    agent.raw._deactivate_run_authority = deactivate
    stream = agent.raw.stream("hi")
    try:
        next(stream)
        assert blocked.wait(5)
        stream.close()
        acquired = agent.raw._stream_lock.acquire(blocking=False)
        if acquired:
            agent.raw._stream_lock.release()
        assert not acquired, "consumer released worker's lock"
        with pytest.raises(RuntimeError, match="并发"):
            next(agent.raw.stream("overlap"))
    finally:
        proceed.set()
        stream.close()
        assert cleaned.wait(5)
        workers[0].join(5)
        assert not workers[0].is_alive()
    # A completed next run proves cleanup released the worker-owned lock.
    agent.raw._model_client.stream_chat = lambda *args, **kwargs: iter([
        ModelEvent(type="token", content="ok"), ModelEvent(type="done"),
    ])
    assert agent.run("after cleanup") == "ok"


def test_normal_run_cleanup_failure_releases_resources_and_terminates_stream(tmp_path, monkeypatch):
    import queue
    import threading

    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    agent = _agent(workspace, tmp_path)
    original_bind = agent.raw.memory.bind_journal
    finished = threading.Event()
    events = queue.Queue()

    def bind_journal(authority, runtime_dir, conversation_id):
        if authority is None:
            raise RuntimeError("memory cleanup failed")
        return original_bind(authority, runtime_dir, conversation_id)

    monkeypatch.setattr(agent.raw.memory, "bind_journal", bind_journal)

    def consume():
        try:
            for event in agent.raw.stream("hi"):
                events.put(event)
        finally:
            finished.set()

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    assert finished.wait(5), "cleanup failure left public stream waiting indefinitely"
    assert agent.raw._current_run_context is None
    assert agent.raw._journal_authority is None
    assert agent.raw._orchestrator_executor._journal_authority is None
    assert agent.raw._stream_lock.acquire(blocking=False)
    agent.raw._stream_lock.release()
    observed = []
    while not events.empty():
        observed.append(events.get_nowait())
    assert any(event.get("type") == "error" and "memory cleanup failed" in event.get("content", "")
               for event in observed)


def test_resume_enforces_agent_token_budget_before_model_call(tmp_path):
    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    runtime_dir = tmp_path / "runtime"
    authority = open_journal_authority(
        runtime_dir, conversation_id="c", task_id="t",
        run_id="budget-run", thread_id="th", turn_id="tu",
    )
    state = AgentLoopState(session_id="sdk-sess", run_id="budget-run")
    state.token_usage.total_tokens = 100
    record = CheckpointService(base_dir=str(workspace.session_root)).save(
        state, journal_cursor=authority.cursor(), reducer_version="1",
        tool_registry_version="", run_state=authority.replay(),
        metadata={
            "conversation_id": "c", "task_id": "t", "run_id": "budget-run",
            "thread_id": "th", "turn_id": "tu", "runtime_dir": str(runtime_dir),
        },
    )
    model = ModelClient(
        api_key="mock-key", base_url="https://mock.api/v1", model_name="mock-model",
    )
    calls = []

    def stream_chat(*args, **kwargs):
        calls.append(1)
        return iter([ModelEvent(type="token", content="over budget"), ModelEvent(type="done")])

    model.stream_chat = stream_chat
    agent = Agent(llm=model, session_id="sdk-sess", bare=True, workspace=workspace,
                  max_total_tokens=100)
    events = []
    agent.raw._orchestrator_executor.event_bus.add_listener(events.append)
    output = agent.resume(record.checkpoint_id)
    assert "预算上限 100" in output
    assert calls == []
    assert [event["status"] for event in events if event["type"] == "resume"] == [
        "started", "failed",
    ]


def test_agent_list_and_kill_background_tasks(tmp_path):
    """#3 Agent.list_background_tasks()/kill_background_task() 公共 API（桌面端可去壳）。"""
    import sys
    import time

    workspace = Workspace.from_folder(tmp_path, session_id="sdk-sess").ensure()
    agent = _agent(workspace, tmp_path)
    svc = agent._agent._background_task_service
    t = svc.start(
        "sdk-sess", "sleep",
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(tmp_path),
    )
    deadline = time.time() + 10
    while t.status not in ("running",) and time.time() < deadline:
        time.sleep(0.05)
    tasks = agent.list_background_tasks()
    assert any(x["task_id"] == t.task_id for x in tasks)
    assert agent.kill_background_task("no-such-task") is False
    assert agent.kill_background_task(t.task_id) is True
    assert t.status == "killed"


def test_bind_workspace_repoints_bg_storage_root(tmp_path):
    """#4 bind_workspace 重设后台任务存储根（任务不落旧工作区）。"""
    ws_a = Workspace.from_folder(tmp_path / "a", session_id="s").ensure()
    ws_b = Workspace.from_folder(tmp_path / "b", session_id="s").ensure()
    agent = _agent(ws_a, tmp_path)
    svc = agent._agent._background_task_service
    assert str(svc._base_dir) == str(ws_a.session_root)
    agent.bind_workspace(ws_b)
    assert str(svc._base_dir) == str(ws_b.session_root)