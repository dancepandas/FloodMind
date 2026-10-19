"""PlanStore 持久化服务测试（D5）。"""

import json

import pytest

from floodmind.agent.native.types import ExecutionPlan
from floodmind.agent.runtime.services.plan_store import PlanStore


def _make_plan(steps=None) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan-test",
        user_message="完成水文分析",
        goal_deliverables=[{"type": "report"}, {"type": "chart"}],
        steps=steps if steps is not None else [
            {
                "step_id": "step-1", "title": "读取数据", "status": "completed",
                "purpose": "读取水位站点数据", "executor": "execution_specialist",
                "skill_name": "", "needs": [], "expected_deliverables": ["原始时序"],
                "output_artifacts": ["/out/data.csv"], "output_summary": "已读取",
                "error_message": "", "attempt_count": 1,
                "subtasks": [{"id": "st-1", "content": "子任务1", "status": "completed"}],
            },
            {
                "step_id": "step-2", "title": "绘制图表", "status": "pending",
                "purpose": "绘制过程线", "executor": "execution_specialist",
                "skill_name": "", "needs": ["step-1"], "expected_deliverables": ["过程线图"],
                "output_artifacts": [], "output_summary": "", "error_message": "",
                "attempt_count": 0, "subtasks": [],
            },
        ],
        created_at="2026-10-19T00:00:00",
        updated_at="2026-10-19T00:00:00",
    )


class TestSaveLoad:
    def test_save_generates_json_and_md(self, tmp_path):
        store = PlanStore(tmp_path / "sessions" / "s1")
        plan = _make_plan()
        json_path = store.save(plan)

        assert json_path == store.json_path
        assert store.json_path.exists()
        assert store.md_path.exists()
        # 自动创建多级目录
        assert store.json_path.parent.is_dir()

    def test_load_roundtrip_equal(self, tmp_path):
        store = PlanStore(tmp_path)
        plan = _make_plan()
        store.save(plan)

        loaded = PlanStore(tmp_path).load()
        assert loaded is not None
        assert loaded.model_dump(mode="json") == plan.model_dump(mode="json")

    def test_load_missing_file_returns_none(self, tmp_path):
        assert PlanStore(tmp_path).load() is None
        assert PlanStore(tmp_path).load_open() is None

    def test_corrupt_json_returns_none(self, tmp_path):
        store = PlanStore(tmp_path)
        store._session_dir.mkdir(parents=True, exist_ok=True)
        store.json_path.write_text("{not valid json", encoding="utf-8")
        assert store.load() is None
        assert store.load_open() is None

    def test_save_twice_atomic_overwrite_no_tmp(self, tmp_path):
        store = PlanStore(tmp_path)
        plan = _make_plan()
        store.save(plan)
        plan.user_message = "修改后的目标"
        store.save(plan)

        files = list(tmp_path.iterdir())
        assert {f.name for f in files} == {"plan.json", "plan.md"}
        loaded = store.load()
        assert loaded is not None
        assert loaded.user_message == "修改后的目标"

    def test_terminal_status_refreshed_on_save(self, tmp_path):
        store = PlanStore(tmp_path)
        plan = _make_plan()
        assert plan.terminal_status == "running"
        store.save(plan)
        assert plan.terminal_status == "running"

        for step in plan.steps:
            step["status"] = "completed"
        store.save(plan)
        assert plan.terminal_status == "completed"
        data = json.loads(store.json_path.read_text(encoding="utf-8"))
        assert data["terminal_status"] == "completed"


class TestMarkdown:
    def test_markdown_contains_checkboxes(self, tmp_path):
        store = PlanStore(tmp_path)
        store.save(_make_plan())
        md = store.md_path.read_text(encoding="utf-8")
        assert "- [x] **step-1**" in md
        assert "- [ ] **step-2**" in md
        assert "（状态: completed）" in md
        assert "（状态: pending）" in md

    def test_markdown_contains_goal_deliverables_and_purpose(self, tmp_path):
        store = PlanStore(tmp_path)
        store.save(_make_plan())
        md = store.md_path.read_text(encoding="utf-8")
        assert "完成水文分析" in md
        assert "- report" in md
        assert "- chart" in md
        assert "目的: 读取水位站点数据" in md
        assert "预期交付物: 原始时序" in md
        assert "更新时间: 2026-10-19T00:00:00" in md


class TestLoadOpen:
    def test_pending_plan_returns_plan(self, tmp_path):
        store = PlanStore(tmp_path)
        store.save(_make_plan())
        loaded = PlanStore(tmp_path).load_open()
        assert loaded is not None
        assert loaded.plan_id == "plan-test"

    def test_running_plan_returns_plan(self, tmp_path):
        plan = _make_plan()
        plan.steps[0]["status"] = "running"
        plan.steps[1]["status"] = "skipped"
        PlanStore(tmp_path).save(plan)
        assert PlanStore(tmp_path).load_open() is not None

    @pytest.mark.parametrize("statuses", [
        ["completed", "completed"],
        ["completed", "skipped"],
        ["skipped", "skipped"],
    ])
    def test_all_closed_returns_none(self, tmp_path, statuses):
        plan = _make_plan()
        for step, status in zip(plan.steps, statuses):
            step["status"] = status
        PlanStore(tmp_path).save(plan)
        assert PlanStore(tmp_path).load_open() is None
