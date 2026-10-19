"""执行计划持久化服务（D5）。

create_plan/update_plan 产生的 ExecutionPlan 原本只存在于 AgentLoopState，
每次新 stream 都会重建 AgentLoopState，导致计划无法跨用户回合恢复。
本模块把计划原子落盘为 session_dir 下的 plan.json（机器可读）与
plan.md（人类可读），供新 run 开始时通过 load_open() 恢复未完成的计划。
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional, Union

from floodmind.agent.native.types import ExecutionPlan

logger = logging.getLogger(__name__)

# 步骤视为"已完成"的终态集合（completed/skipped）
_CLOSED_STATUSES = {"completed", "skipped"}


class PlanStore:
    """按会话目录持久化 ExecutionPlan。

    固定写入 session_dir/plan.json 与 session_dir/plan.md；
    写入采用"同目录临时文件 + os.replace"的原子替换，崩溃不留半截文件。
    """

    def __init__(self, session_dir: Union[str, Path]):
        self._session_dir = Path(session_dir)
        self._json_path = self._session_dir / "plan.json"
        self._md_path = self._session_dir / "plan.md"

    @property
    def json_path(self) -> Path:
        return self._json_path

    @property
    def md_path(self) -> Path:
        return self._md_path

    def save(self, plan: ExecutionPlan) -> Path:
        """原子写入 plan.json 与 plan.md，返回 json 文件路径。

        save 时根据步骤状态刷新 terminal_status：
        所有步骤 completed/skipped -> "completed"，否则 "running"。
        """
        plan.refresh_terminal_status()
        self._session_dir.mkdir(parents=True, exist_ok=True)

        payload = plan.model_dump(mode="json")
        json_text = json.dumps(payload, ensure_ascii=False, indent=2)
        md_text = self._render_markdown(plan)

        self._atomic_write(self._json_path, json_text)
        self._atomic_write(self._md_path, md_text)
        return self._json_path

    def load(self) -> Optional[ExecutionPlan]:
        """读取 plan.json；文件不存在或 JSON 损坏时返回 None（不抛异常）。"""
        if not self._json_path.exists():
            return None
        try:
            raw = json.loads(self._json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("计划文件 %s 读取/解析失败，忽略持久化计划: %s", self._json_path, exc)
            return None
        try:
            return ExecutionPlan.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 —— 坏数据不能让 agent 启动崩溃
            logger.warning("计划文件 %s 数据无效，忽略持久化计划: %s", self._json_path, exc)
            return None

    def load_open(self) -> Optional[ExecutionPlan]:
        """仅当计划存在未完成步骤（pending/running）时返回，否则返回 None。"""
        plan = self.load()
        if plan is None:
            return None
        if plan.has_open_steps():
            return plan
        return None

    def _atomic_write(self, path: Path, text: str) -> None:
        """先写同目录临时文件再 os.replace，保证原子覆盖且无 .tmp 残留。"""
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)

    def _render_markdown(self, plan: ExecutionPlan) -> str:
        """渲染人类可读的计划文件。"""
        lines: list[str] = []
        lines.append(f"# 执行计划: {plan.user_message or plan.plan_id}")
        lines.append("")
        lines.append(f"- 计划ID: {plan.plan_id}")
        lines.append(f"- 更新时间: {plan.updated_at}")
        lines.append(f"- 整体状态: {plan.terminal_status}")
        lines.append("")

        # 目标与交付物
        lines.append("## 目标")
        lines.append("")
        lines.append(plan.user_message or "（未填写）")
        lines.append("")
        lines.append("## 交付物")
        lines.append("")
        if plan.goal_deliverables:
            for d in plan.goal_deliverables:
                if isinstance(d, dict):
                    lines.append(f"- {d.get('type', d)}")
                else:
                    lines.append(f"- {d}")
        else:
            lines.append("（未指定）")
        lines.append("")

        # 步骤清单（复选框）
        lines.append("## 步骤")
        lines.append("")
        for step in plan.steps:
            status = str(step.get("status", "pending"))
            checked = "x" if status in _CLOSED_STATUSES else " "
            title = step.get("title", "")
            step_id = step.get("step_id", "")
            lines.append(f"- [{checked}] **{step_id}** {title}（状态: {status}）")
            purpose = str(step.get("purpose", "") or "")
            if purpose:
                lines.append(f"  - 目的: {purpose}")
            expected = step.get("expected_deliverables", []) or []
            if expected:
                rendered = ", ".join(str(e) for e in expected)
                lines.append(f"  - 预期交付物: {rendered}")
            subtasks = step.get("subtasks", []) or []
            if subtasks:
                lines.append("  - 子任务:")
                for st in subtasks:
                    if isinstance(st, dict):
                        st_status = str(st.get("status", "pending"))
                        st_checked = "x" if st_status in _CLOSED_STATUSES else " "
                        lines.append(f"    - [{st_checked}] {st.get('content', st)}（{st_status}）")
                    else:
                        lines.append(f"    - [ ] {st}")
            artifacts = step.get("output_artifacts", []) or []
            if artifacts:
                rendered = ", ".join(str(a) for a in artifacts)
                lines.append(f"  - 产出文件: {rendered}")
            output_summary = str(step.get("output_summary", "") or "")
            if output_summary:
                lines.append(f"  - 产出摘要: {output_summary}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
