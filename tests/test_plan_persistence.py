"""计划持久化与跨 run 恢复集成测试（D5）。

用裸 agent（不经完整 __init__）+ 临时 state_dir 验证：
- create_plan/update_plan 通过 _emit_plan_full 落盘 plan.json/plan.md；
- PlanStore.load_open 能恢复未完成计划、跳过已完成计划；
- _inject_restored_plan_context 把恢复计划注入模型上下文；
- _plan_session_dir 仅依赖 state_dir，不向其他目录兜底。
"""

from pathlib import Path

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.agent.native.types import AgentLoopState, ExecutionPlan, RunContext
from floodmind.agent.runtime.services.plan_store import PlanStore


def _make_bare_agent() -> NativeFloodAgent:
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent.session_id = ""
    agent._current_run_context = None
    agent._event_bus = EventBus()
    agent._tracing_service = None
    agent._last_loop_state = AgentLoopState(session_id="test-session", run_id="run-1")
    return agent


def _run_context(tmp_path, session_id="sess-1", agent_tier="main") -> RunContext:
    return RunContext(
        session_id=session_id,
        user_text="继续上次任务",
        output_dir=str(tmp_path / "out"),
        state_dir=str(tmp_path / "state"),
        agent_tier=agent_tier,
    )


def _expected_session_dir(tmp_path, session_id="sess-1") -> Path:
    return tmp_path / "state" / "sessions" / session_id


class TestPlanSessionDir:
    def test_returns_sessions_subdir(self, tmp_path):
        agent = _make_bare_agent()
        ctx = _run_context(tmp_path)
        assert agent._plan_session_dir(ctx) == _expected_session_dir(tmp_path)

    def test_none_without_state_dir(self, tmp_path):
        agent = _make_bare_agent()
        ctx = _run_context(tmp_path)
        ctx.state_dir = ""
        assert agent._plan_session_dir(ctx) is None

    def test_none_without_context(self):
        agent = _make_bare_agent()
        assert agent._plan_session_dir(None) is None


class TestPlanPersistenceOnEmit:
    def test_create_plan_persists_files(self, tmp_path):
        agent = _make_bare_agent()
        agent._current_run_context = _run_context(tmp_path)

        result = agent._handle_create_plan(
            user_goal="完成水文分析", deliverables="report",
            steps=[{"step_id": "step-1", "title": "读取数据", "purpose": "读数据"}],
        )
        assert "执行计划已创建" in result

        session_dir = _expected_session_dir(tmp_path)
        assert (session_dir / "plan.json").exists()
        assert (session_dir / "plan.md").exists()

    def test_update_plan_persists_step_changes(self, tmp_path):
        agent = _make_bare_agent()
        agent._current_run_context = _run_context(tmp_path)
        agent._handle_create_plan(
            user_goal="g", deliverables="report",
            steps=[{"step_id": "step-1", "title": "一"}, {"step_id": "step-2", "title": "二"}],
        )
        agent._handle_update_plan(action="update_step", step_id="step-1", status="completed")

        store = PlanStore(_expected_session_dir(tmp_path))
        plan = store.load()
        assert plan is not None
        assert plan.find_step("step-1")["status"] == "completed"
        assert plan.find_step("step-2")["status"] == "pending"

    def test_persist_without_run_context_is_silent_noop(self, tmp_path):
        """裸 agent（无 run 上下文）emit 计划不得抛异常，也不写文件。"""
        agent = _make_bare_agent()  # _current_run_context = None
        result = agent._handle_create_plan(
            user_goal="g", deliverables="", steps=[{"step_id": "s", "title": "t"}],
        )
        assert "执行计划已创建" in result
        assert not (tmp_path / "plan.json").exists()


class TestPlanPersistenceAtRunEnd:
    def test_persists_auto_advanced_status(self, tmp_path):
        """run 收尾落盘必须覆盖 auto-advance 直接改状态、未经 emit 的情况。"""
        agent = _make_bare_agent()
        agent._current_run_context = _run_context(tmp_path)
        agent._handle_create_plan(
            user_goal="g", deliverables="report",
            steps=[{"step_id": "step-1", "title": "一"}, {"step_id": "step-2", "title": "二"}],
        )
        state = agent._last_loop_state
        assert state.plan is not None

        # 模拟 executor._auto_advance_plan：直接改状态与产物，不经过 update_plan
        state.plan.find_step("step-1")["status"] = "completed"
        state.plan.find_step("step-1")["output_artifacts"] = ["out/result.txt"]

        agent._persist_plan_state(state)

        plan = PlanStore(_expected_session_dir(tmp_path)).load()
        assert plan is not None
        assert plan.find_step("step-1")["status"] == "completed"
        assert plan.find_step("step-1")["output_artifacts"] == ["out/result.txt"]
        assert plan.find_step("step-2")["status"] == "pending"
        assert plan.terminal_status == "running"

    def test_no_plan_is_noop(self, tmp_path):
        agent = _make_bare_agent()
        agent._current_run_context = _run_context(tmp_path)
        state = AgentLoopState(session_id="sess-1", run_id="run-1")
        agent._persist_plan_state(state)
        assert not _expected_session_dir(tmp_path).exists()

    def test_none_state_is_noop(self, tmp_path):
        agent = _make_bare_agent()
        agent._current_run_context = _run_context(tmp_path)
        agent._persist_plan_state(None)
        assert not _expected_session_dir(tmp_path).exists()


class TestPlanRestore:
    def test_load_open_restores_unfinished_plan(self, tmp_path):
        agent = _make_bare_agent()
        agent._current_run_context = _run_context(tmp_path)
        agent._handle_create_plan(
            user_goal="跨回合任务", deliverables="report",
            steps=[{"step_id": "step-1", "title": "一"}, {"step_id": "step-2", "title": "二"}],
        )
        agent._handle_update_plan(action="update_step", step_id="step-1", status="completed")

        # 模拟新 run：重建 AgentLoopState + 初始 messages，然后从磁盘恢复
        store = PlanStore(_expected_session_dir(tmp_path))
        restored = store.load_open()
        assert restored is not None
        assert restored.plan_id == agent._last_loop_state.plan.plan_id

        new_state = AgentLoopState(session_id="sess-1", run_id="run-2")
        new_state.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "继续上次任务"},
        ]
        agent._inject_restored_plan_context(new_state, restored)

        assert new_state.plan is None  # 注入只改 messages，plan 由 run 循环赋值
        injected = [m for m in new_state.messages if m.get("role") == "user"]
        assert len(injected) == 2
        restored_msg = injected[0]
        assert "[恢复的执行计划]" in restored_msg["content"]
        assert "跨回合任务" in restored_msg["content"]
        assert "step-2" in restored_msg["content"]
        assert "(pending)" in restored_msg["content"]
        # 注入消息必须插在最后一条 user 消息之前
        assert new_state.messages.index(restored_msg) < new_state.messages.index(injected[1])

    def test_completed_plan_not_restored(self, tmp_path):
        agent = _make_bare_agent()
        agent._current_run_context = _run_context(tmp_path)
        agent._handle_create_plan(
            user_goal="g", deliverables="", steps=[{"step_id": "step-1", "title": "一"}],
        )
        agent._handle_update_plan(action="update_step", step_id="step-1", status="completed")

        # 全部完成：terminal_status 落盘为 completed，load_open 返回 None
        store = PlanStore(_expected_session_dir(tmp_path))
        assert store.load() is not None
        assert store.load().terminal_status == "completed"
        assert store.load_open() is None

    def test_inject_appends_when_no_user_message(self, tmp_path):
        agent = _make_bare_agent()
        plan = ExecutionPlan(plan_id="p", user_message="目标", steps=[
            {"step_id": "step-1", "title": "一", "status": "running"},
        ])
        state = AgentLoopState(session_id="s", run_id="r")
        state.messages = [{"role": "system", "content": "sys"}]
        agent._inject_restored_plan_context(state, plan)
        assert state.messages[-1]["role"] == "user"
        assert "[恢复的执行计划]" in state.messages[-1]["content"]
        assert "(running)" in state.messages[-1]["content"]

    def test_emit_plan_full_after_restore_is_idempotent(self, tmp_path):
        """恢复后再次 _emit_plan_full（幂等落盘）不改变计划内容。"""
        agent = _make_bare_agent()
        agent._current_run_context = _run_context(tmp_path)
        agent._handle_create_plan(
            user_goal="g", deliverables="", steps=[{"step_id": "step-1", "title": "一"}],
        )
        store = PlanStore(_expected_session_dir(tmp_path))
        before = store.load().model_dump(mode="json")

        restored = store.load_open()
        assert restored is not None
        agent._emit_plan_full(restored)

        after = PlanStore(_expected_session_dir(tmp_path)).load().model_dump(mode="json")
        assert after == before
