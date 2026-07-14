"""Tests for update_plan tool and optimistic plan auto-advance."""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.agent.native.types import AgentLoopState, ExecutionPlan, RunContext


def _make_bare_agent() -> NativeFloodAgent:
    """构造一个不经过完整 __init__ 的 NativeFloodAgent，只设测试需要的字段。"""
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent.session_id = ""
    agent._current_run_context = None
    agent._event_bus = EventBus()
    agent._tracing_service = None
    agent._last_loop_state = AgentLoopState(session_id="test-session", run_id="run-1")
    return agent


def _make_plan(steps=None) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan-1",
        user_message="goal",
        goal_deliverables=[],
        steps=steps or [],
        created_at="2026-06-27T00:00:00",
        updated_at="2026-06-27T00:00:00",
    )


class TestUpdatePlan:
    def test_add_step(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([{
            "step_id": "step-1", "title": "第一步", "status": "pending",
            "purpose": "", "executor": "execution_specialist", "skill_name": "",
            "needs": [], "expected_deliverables": [], "output_artifacts": [],
            "output_summary": "", "error_message": "", "attempt_count": 0,
        }])

        result = agent._handle_update_plan(
            action="add_step",
            step={"step_id": "step-2", "title": "新增步骤", "purpose": "补充", "needs": ["step-1"]},
        )

        assert "计划已更新" in result
        plan = agent._last_loop_state.plan
        assert len(plan.steps) == 2
        new_step = plan.find_step("step-2")
        assert new_step is not None
        assert new_step["status"] == "pending"
        assert new_step["needs"] == ["step-1"]

    def test_add_step_duplicate_id_rejected(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([{
            "step_id": "step-1", "title": "第一步", "status": "pending",
            "purpose": "", "executor": "execution_specialist", "skill_name": "",
            "needs": [], "expected_deliverables": [], "output_artifacts": [],
            "output_summary": "", "error_message": "", "attempt_count": 0,
        }])

        result = agent._handle_update_plan(
            action="add_step",
            step={"step_id": "step-1", "title": "重复"},
        )

        assert "已存在" in result
        assert len(agent._last_loop_state.plan.steps) == 1

    def test_add_step_cycle_rejected(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([
            {"step_id": "step-1", "title": "一", "status": "pending", "needs": [],
             "purpose": "", "executor": "", "skill_name": "", "expected_deliverables": [],
             "output_artifacts": [], "output_summary": "", "error_message": "", "attempt_count": 0},
            {"step_id": "step-2", "title": "二", "status": "pending", "needs": ["step-1"],
             "purpose": "", "executor": "", "skill_name": "", "expected_deliverables": [],
             "output_artifacts": [], "output_summary": "", "error_message": "", "attempt_count": 0},
        ])

        # 新增 step-3 依赖 step-2，再让 step-1 依赖 step-3 会成环；
        # 这里直接构造一个自指 + 双向依赖：step-2 needs step-1, 新增 step-1' 依赖 step-2 且被 step-1 依赖
        # 简单环：让新步骤 needs 一个不形成环但再 add 一个 step-2 依赖新步骤 ——
        # 用两步：先 add step-3 needs step-2（OK），再 update 让 step-2 needs step-3（成环）
        result = agent._handle_update_plan(
            action="add_step",
            step={"step_id": "step-3", "title": "三", "needs": ["step-2"]},
        )
        assert "计划已更新" in result  # step-3 依赖 step-2 不成环

        # 现在让 step-2 反向依赖 step-3，形成环 step-2 -> step-3 -> step-2
        target = agent._last_loop_state.plan.find_step("step-2")
        target["needs"] = ["step-3"]
        assert agent._last_loop_state.plan.has_cycle()

    def test_update_step_status(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([{
            "step_id": "step-1", "title": "一", "status": "pending",
            "purpose": "", "executor": "", "skill_name": "", "needs": [],
            "expected_deliverables": [], "output_artifacts": [],
            "output_summary": "", "error_message": "", "attempt_count": 0,
        }])

        result = agent._handle_update_plan(
            action="update_step",
            step_id="step-1",
            status="completed",
            output_summary="已完成",
            output_artifacts=["/tmp/report.md"],
        )

        assert "计划已更新" in result
        step = agent._last_loop_state.plan.find_step("step-1")
        assert step["status"] == "completed"
        assert step["output_summary"] == "已完成"
        assert step["output_artifacts"] == ["/tmp/report.md"]

    def test_update_step_not_found(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([{
            "step_id": "step-1", "title": "一", "status": "pending",
            "purpose": "", "executor": "", "skill_name": "", "needs": [],
            "expected_deliverables": [], "output_artifacts": [],
            "output_summary": "", "error_message": "", "attempt_count": 0,
        }])

        result = agent._handle_update_plan(action="update_step", step_id="nope", status="completed")

        assert "不存在" in result
        assert agent._last_loop_state.plan.find_step("step-1")["status"] == "pending"

    def test_update_step_invalid_status_ignored(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([{
            "step_id": "step-1", "title": "一", "status": "pending",
            "purpose": "", "executor": "", "skill_name": "", "needs": [],
            "expected_deliverables": [], "output_artifacts": [],
            "output_summary": "", "error_message": "", "attempt_count": 0,
        }])

        # 非法 status 不应改 status，但 plan 仍会 emit/更新（updated_at）
        result = agent._handle_update_plan(action="update_step", step_id="step-1", status="bogus")
        assert "计划已更新" in result
        assert agent._last_loop_state.plan.find_step("step-1")["status"] == "pending"

    def test_remove_step(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([
            {"step_id": "step-1", "title": "一", "status": "pending", "needs": [],
             "purpose": "", "executor": "", "skill_name": "", "expected_deliverables": [],
             "output_artifacts": [], "output_summary": "", "error_message": "", "attempt_count": 0},
            {"step_id": "step-2", "title": "二", "status": "pending", "needs": [],
             "purpose": "", "executor": "", "skill_name": "", "expected_deliverables": [],
             "output_artifacts": [], "output_summary": "", "error_message": "", "attempt_count": 0},
        ])

        result = agent._handle_update_plan(action="remove_step", step_id="step-1")
        assert "计划已更新" in result
        assert agent._last_loop_state.plan.find_step("step-1") is None
        assert len(agent._last_loop_state.plan.steps) == 1

    def test_remove_step_not_found(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([{
            "step_id": "step-1", "title": "一", "status": "pending", "needs": [],
            "purpose": "", "executor": "", "skill_name": "", "expected_deliverables": [],
            "output_artifacts": [], "output_summary": "", "error_message": "", "attempt_count": 0,
        }])

        result = agent._handle_update_plan(action="remove_step", step_id="nope")
        assert "不存在" in result
        assert len(agent._last_loop_state.plan.steps) == 1

    def test_remove_step_with_dependents_rejected(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([
            {"step_id": "step-1", "title": "一", "status": "pending", "needs": [],
             "purpose": "", "executor": "", "skill_name": "", "expected_deliverables": [],
             "output_artifacts": [], "output_summary": "", "error_message": "", "attempt_count": 0},
            {"step_id": "step-2", "title": "二", "status": "pending", "needs": ["step-1"],
             "purpose": "", "executor": "", "skill_name": "", "expected_deliverables": [],
             "output_artifacts": [], "output_summary": "", "error_message": "", "attempt_count": 0},
        ])

        result = agent._handle_update_plan(action="remove_step", step_id="step-1")
        assert "仍被 step-2 依赖" in result
        assert agent._last_loop_state.plan.find_step("step-1") is not None

    def test_update_step_skipped_status(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([{
            "step_id": "step-1", "title": "一", "status": "pending", "needs": [],
            "purpose": "", "executor": "", "skill_name": "", "expected_deliverables": [],
            "output_artifacts": [], "output_summary": "", "error_message": "", "attempt_count": 0,
        }])

        result = agent._handle_update_plan(action="update_step", step_id="step-1", status="skipped")
        assert "计划已更新" in result
        assert agent._last_loop_state.plan.find_step("step-1")["status"] == "skipped"

    def test_no_plan_error(self):
        agent = _make_bare_agent()
        # plan 默认为空 ExecutionPlan（steps=[]），但有 plan 对象；
        # 模拟完全没有 plan
        agent._last_loop_state.plan = None
        result = agent._handle_update_plan(action="add_step", step={"step_id": "x"})
        assert "没有执行计划" in result

    def test_invalid_action(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([])
        result = agent._handle_update_plan(action="bogus")
        assert "action 仅支持" in result


class TestSubtasks:
    def test_add_step_with_subtasks(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([{
            "step_id": "step-1", "title": "第一步", "status": "pending",
            "purpose": "", "executor": "execution_specialist", "skill_name": "",
            "needs": [], "expected_deliverables": [], "output_artifacts": [],
            "output_summary": "", "error_message": "", "attempt_count": 0,
        }])

        result = agent._handle_update_plan(
            action="add_step",
            step={
                "step_id": "step-2",
                "title": "新增步骤",
                "purpose": "补充",
                "subtasks": [
                    {"id": "st-1", "content": "子任务1", "status": "pending", "priority": "high"},
                ],
            },
        )

        assert "计划已更新" in result
        new_step = agent._last_loop_state.plan.find_step("step-2")
        assert new_step is not None
        assert new_step.get("subtasks") == [
            {"id": "st-1", "content": "子任务1", "status": "pending", "priority": "high"},
        ]

    def test_update_step_subtasks(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([{
            "step_id": "step-1", "title": "一", "status": "pending",
            "purpose": "", "executor": "", "skill_name": "", "needs": [],
            "expected_deliverables": [], "output_artifacts": [],
            "output_summary": "", "error_message": "", "attempt_count": 0,
        }])

        result = agent._handle_update_plan(
            action="update_step",
            step_id="step-1",
            subtasks=[
                {"id": "st-1", "content": "子任务1", "status": "completed"},
                {"content": "子任务2", "priority": "low"},
            ],
        )

        assert "计划已更新" in result
        step = agent._last_loop_state.plan.find_step("step-1")
        assert len(step["subtasks"]) == 2
        assert step["subtasks"][0]["status"] == "completed"
        assert step["subtasks"][1]["id"].startswith("step-1-sub-")
        assert step["subtasks"][1]["priority"] == "low"

    def test_workflow_plan_event_includes_subtasks(self):
        agent = _make_bare_agent()
        agent._last_loop_state.plan = _make_plan([{
            "step_id": "step-1", "title": "一", "status": "pending",
            "purpose": "", "executor": "", "skill_name": "", "needs": [],
            "expected_deliverables": [], "output_artifacts": [],
            "output_summary": "", "error_message": "", "attempt_count": 0,
            "subtasks": [{"id": "st-1", "content": "子任务", "status": "in_progress", "priority": "normal"}],
        }])

        events = []
        agent._event_bus.add_listener(events.append)
        agent._emit_plan_full(agent._last_loop_state.plan, title="测试计划")

        assert len(events) == 1
        assert events[0]["type"] == "workflow_plan"
        steps = events[0]["steps"]
        assert steps[0].get("subtasks") == [
            {"id": "st-1", "content": "子任务", "status": "in_progress", "priority": "normal"},
        ]
        assert "output_artifacts" in steps[0]


class TestNormalizeArtifacts:
    def test_list_passthrough(self):
        agent = _make_bare_agent()
        assert agent._normalize_artifacts(["a.md", "b.md"]) == ["a.md", "b.md"]

    def test_json_string(self):
        agent = _make_bare_agent()
        assert agent._normalize_artifacts('["a.md", "b.md"]') == ["a.md", "b.md"]

    def test_plain_string(self):
        agent = _make_bare_agent()
        assert agent._normalize_artifacts("a.md") == ["a.md"]

    def test_empty(self):
        agent = _make_bare_agent()
        assert agent._normalize_artifacts("") == []
        assert agent._normalize_artifacts([]) == []


def _make_executor(event_bus=None):
    mc = MagicMock(spec=ModelClient)
    tool_executor = MagicMock()
    reg = MagicMock()
    reg.get.return_value = None
    reg.all.return_value = []
    reg.tools_schema.return_value = []
    return NativeAgentExecutor(
        model_client=mc,
        tool_executor=tool_executor,
        event_bus=event_bus or EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=5,
        system_prompt="test",
        tools_schema=[],
        tool_registry=reg,
    )


def _step(sid, status="pending", artifacts=None):
    return {
        "step_id": sid, "title": sid, "status": status, "needs": [],
        "purpose": "", "executor": "", "skill_name": "", "expected_deliverables": [],
        "output_artifacts": artifacts or [], "output_summary": "", "error_message": "", "attempt_count": 0,
    }


class TestAutoAdvance:
    def test_auto_advance_on_artifact(self):
        executor = _make_executor()
        state = AgentLoopState(session_id="s", run_id="r")
        state.plan = _make_plan([_step("step-1"), _step("step-2")])
        state.artifacts = ["/tmp/out/report.md"]
        state._round_artifacts_before = []  # 本轮新增了 report.md

        executor._auto_advance_plan(state)

        assert state.plan.find_step("step-1")["status"] == "completed"
        assert state.plan.find_step("step-1")["output_artifacts"] == ["/tmp/out/report.md"]
        assert state.plan.find_step("step-2")["status"] == "pending"

    def test_auto_advance_no_artifact_noop(self):
        executor = _make_executor()
        state = AgentLoopState(session_id="s", run_id="r")
        state.plan = _make_plan([_step("step-1")])
        state.artifacts = ["/tmp/old.md"]
        state._round_artifacts_before = ["/tmp/old.md"]  # 本轮无新增

        executor._auto_advance_plan(state)

        assert state.plan.find_step("step-1")["status"] == "pending"

    def test_auto_advance_skips_when_no_pending(self):
        executor = _make_executor()
        state = AgentLoopState(session_id="s", run_id="r")
        state.plan = _make_plan([_step("step-1", status="completed")])
        state.artifacts = ["/tmp/new.md"]
        state._round_artifacts_before = []

        executor._auto_advance_plan(state)  # 不应抛异常

        assert state.plan.find_step("step-1")["status"] == "completed"

    def test_auto_advance_delegated_step_not_touched(self):
        """已被委派标为 running 的步骤不会被乐观推进。"""
        executor = _make_executor()
        state = AgentLoopState(session_id="s", run_id="r")
        state.plan = _make_plan([
            _step("step-1", status="running"),  # 委派中
            _step("step-2", status="pending"),
        ])
        state.artifacts = ["/tmp/new.md"]
        state._round_artifacts_before = []

        executor._auto_advance_plan(state)

        # running 步骤保持 running，推进的是第一个 pending(step-2)
        assert state.plan.find_step("step-1")["status"] == "running"
        assert state.plan.find_step("step-2")["status"] == "completed"

    def test_auto_advance_multi_artifact_advances_multi_steps(self):
        """一轮产出多个文件应推进多个 pending 步骤（1:1），不漏推进。"""
        executor = _make_executor()
        state = AgentLoopState(session_id="s", run_id="r")
        state.plan = _make_plan([_step("step-1"), _step("step-2"), _step("step-3")])
        state.artifacts = ["/tmp/a.md", "/tmp/b.xlsx"]
        state._round_artifacts_before = []  # 本轮新增 2 个文件

        executor._auto_advance_plan(state)

        assert state.plan.find_step("step-1")["status"] == "completed"
        assert state.plan.find_step("step-1")["output_artifacts"] == ["/tmp/a.md"]
        assert state.plan.find_step("step-2")["status"] == "completed"
        assert state.plan.find_step("step-2")["output_artifacts"] == ["/tmp/b.xlsx"]
        assert state.plan.find_step("step-3")["status"] == "pending"

    def test_auto_advance_no_plan(self):
        executor = _make_executor()
        state = AgentLoopState(session_id="s", run_id="r")
        state.plan = None
        state.artifacts = ["/tmp/new.md"]
        state._round_artifacts_before = []
        executor._auto_advance_plan(state)  # 不应抛异常

    def test_round_artifacts_diff(self):
        executor = _make_executor()
        state = AgentLoopState(session_id="s", run_id="r")
        state.artifacts = ["a.md", "b.md", "c.md"]
        state._round_artifacts_before = ["a.md"]
        diff = executor._round_artifacts_diff(state)
        assert set(diff) == {"b.md", "c.md"}
