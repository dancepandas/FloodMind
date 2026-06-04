"""
Native Agent Runtime - Planner

执行计划创建、步骤状态管理、重规划事件。
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from floodmind.agent.native.types import AgentLoopState, ExecutionPlan, PlanStep
from floodmind.agent.native.event_bus import EventBus

logger = logging.getLogger(__name__)


class Planner:
    def __init__(self, event_bus: Optional[EventBus] = None):
        self._event_bus = event_bus

    def create_plan(
        self,
        state: AgentLoopState,
        user_goal: str,
        deliverables: str,
        steps: List[Dict[str, Any]],
    ) -> str:
        normalized_steps = []
        for i, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                raw_step = {"title": str(raw_step)[:60]}
            step_id = raw_step.get("step_id") or f"step-{i + 1}"
            expected = raw_step.get("expected_deliverables", [])
            if isinstance(expected, str):
                try:
                    expected = json.loads(expected)
                except Exception:
                    expected = [{"type": expected}]
            if not isinstance(expected, list):
                expected = [expected] if expected else []
            normalized_steps.append({
                "step_id": step_id,
                "title": str(raw_step.get("title", "") or f"步骤 {i + 1}"),
                "executor": str(raw_step.get("executor", "") or "execution_specialist"),
                "skill_name": str(raw_step.get("skill_name", "") or ""),
                "purpose": str(raw_step.get("purpose", "") or ""),
                "status": "pending",
                "expected_deliverables": expected,
                "output_artifacts": [],
                "output_summary": "",
                "error_message": "",
                "attempt_count": 0,
            })

        deliverable_types = [d.strip() for d in deliverables.split(",") if d.strip()] if deliverables else []
        goal_deliverables = [{"type": dt} for dt in deliverable_types]

        plan = ExecutionPlan(
            plan_id=f"plan-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            user_message=user_goal,
            goal_deliverables=goal_deliverables,
            steps=normalized_steps,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )

        state.plan = plan
        state.user_message = user_goal
        summary = f"执行计划已创建: {len(normalized_steps)} 个步骤, 交付物: {deliverables or '无特定类型'}"
        logger.info("[Planner] %s", summary)

        if self._event_bus:
            self._event_bus.emit_workflow_plan(
                title=user_goal,
                steps=[
                    {
                        "key": s["step_id"],
                        "label": s["title"],
                        "title": s["title"],
                        "status": s["status"],
                        "detail": s["purpose"],
                        "expected_deliverables": s["expected_deliverables"],
                    }
                    for s in normalized_steps
                ],
            )

        return summary

    def update_step_status(
        self,
        state: AgentLoopState,
        step_id: str,
        status: str,
        output_summary: str = "",
        error_message: str = "",
    ) -> None:
        if state.plan is None:
            return
        target_step = None
        for step in state.plan.steps:
            if step.get("step_id") == step_id:
                target_step = step
                step["status"] = status
                if output_summary:
                    step["output_summary"] = output_summary
                if error_message:
                    step["error_message"] = error_message
                break

        if self._event_bus and target_step:
            self._event_bus.emit_workflow_step(
                step_key=step_id,
                status=status,
                title=target_step.get("title", ""),
                detail=target_step.get("output_summary", "") or output_summary,
            )

    def get_next_pending_step(self, state: AgentLoopState) -> Optional[Dict[str, Any]]:
        if state.plan is None:
            return None
        for step in state.plan.steps:
            if step.get("status") == "pending":
                return step
        return None
