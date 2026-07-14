"""Tests for ExecutionPlan — topological_sort, cycle detection, get_batches."""

import pytest

from floodmind.agent.native.types import ExecutionPlan


def _make_plan(steps, goal_deliverables=None):
    return ExecutionPlan(
        plan_id="test-plan",
        user_message="test task",
        goal_deliverables=goal_deliverables or [],
        steps=steps,
    )


class TestExecutionPlan:
    def test_topological_sort_no_deps(self):
        plan = _make_plan([
            {"step_id": "a", "title": "任务A", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": [], "expected_deliverables": []},
            {"step_id": "b", "title": "任务B", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": [], "expected_deliverables": []},
        ])
        order = plan.topological_sort()
        assert set(order) == {"a", "b"}

    def test_topological_sort_with_deps(self):
        plan = _make_plan([
            {"step_id": "a", "title": "下载", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": [], "expected_deliverables": []},
            {"step_id": "b", "title": "清洗", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["a"], "expected_deliverables": []},
            {"step_id": "c", "title": "建模", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["a"], "expected_deliverables": []},
            {"step_id": "d", "title": "报告", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["b", "c"], "expected_deliverables": []},
        ])
        order = plan.topological_sort()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")
        assert len(order) == 4

    def test_cycle_detection(self):
        plan = _make_plan([
            {"step_id": "a", "title": "A", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["b"], "expected_deliverables": []},
            {"step_id": "b", "title": "B", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["a"], "expected_deliverables": []},
        ])
        assert plan.has_cycle()

    def test_no_cycle_ok(self):
        plan = _make_plan([
            {"step_id": "a", "title": "A", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": [], "expected_deliverables": []},
            {"step_id": "b", "title": "B", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["a"], "expected_deliverables": []},
        ])
        assert not plan.has_cycle()

    def test_get_batches_linear(self):
        plan = _make_plan([
            {"step_id": "a", "title": "A", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": [], "expected_deliverables": []},
            {"step_id": "b", "title": "B", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["a"], "expected_deliverables": []},
        ])
        batches = plan.get_batches()
        assert len(batches) == 2
        assert batches[0] == ["a"]
        assert batches[1] == ["b"]

    def test_get_batches_diamond(self):
        plan = _make_plan([
            {"step_id": "a", "title": "A", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": [], "expected_deliverables": []},
            {"step_id": "b", "title": "B", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["a"], "expected_deliverables": []},
            {"step_id": "c", "title": "C", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["a"], "expected_deliverables": []},
            {"step_id": "d", "title": "D", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["b", "c"], "expected_deliverables": []},
        ])
        batches = plan.get_batches()
        assert len(batches) == 3
        assert set(batches[0]) == {"a"}
        assert set(batches[1]) == {"b", "c"}
        assert set(batches[2]) == {"d"}

    def test_find_step(self):
        plan = _make_plan([
            {"step_id": "s1", "title": "Step 1", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": [], "expected_deliverables": []},
        ])
        found = plan.find_step("s1")
        assert found is not None
        assert found["title"] == "Step 1"
        assert plan.find_step("nonexistent") is None

    def test_next_pending_step(self):
        plan = _make_plan([
            {"step_id": "s1", "title": "Done", "executor": "execution_specialist", "purpose": "", "status": "completed", "needs": [], "expected_deliverables": []},
            {"step_id": "s2", "title": "Pending", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": [], "expected_deliverables": []},
        ])
        next_step = plan.next_pending_step()
        assert next_step["step_id"] == "s2"

    def test_get_batches_cycle_raises(self):
        plan = _make_plan([
            {"step_id": "a", "title": "A", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["b"], "expected_deliverables": []},
            {"step_id": "b", "title": "B", "executor": "execution_specialist", "purpose": "", "status": "pending", "needs": ["a"], "expected_deliverables": []},
        ])
        with pytest.raises(ValueError, match="依赖环"):
            plan.get_batches()

    def test_get_batches_empty_plan(self):
        plan = _make_plan([])
        assert plan.get_batches() == []
