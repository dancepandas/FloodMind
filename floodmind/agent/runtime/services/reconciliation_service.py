"""Pending/Indeterminate 对账（目标 §16.4 step5 / §6.5）。恢复安全边界前调用。

规则（§6.5 "先 reconcile，禁止直接重试"）：
- 每个 status == indeterminate 的 pending tool → 发 `tool.result.committed`（verdict=failed）落定。
- 每个 status in {approval_required, approved, running} 的僵尸 pending tool → 先发
  `tool.execution.indeterminate`（reason=reconciled_pending）再发 `tool.result.committed`（verdict=failed）。
- 悬空 `pending_approvals`（无 resolved）→ 发 `tool.approval.resolved`（approved=False）deny 落定。
- `child_threads` 中 status == running → 发 `thread.cancelled`。
- background/artifact 临时清理本任务留接口（`background_killed`/`artifacts_cleaned` 保持 0），P6 实装；
  若 `background_task_service` 已注入则对 `active_background_tasks` 调 `kill_task`（§12 kill 验证链，不依赖 ContextVar）。

本服务不直接改 reducer 状态，只通过 JournalAuthority 发事件；确定性由 reducer 折叠保证。
"""

import logging
from typing import Any, Optional

from pydantic import BaseModel

from floodmind.agent.runtime.contracts.run_state import RunState
from floodmind.agent.runtime.contracts.tool_transaction import ToolStatus
from floodmind.agent.runtime.services.journal_authority import JournalAuthority

logger = logging.getLogger(__name__)


class ReconcileResult(BaseModel):
    indeterminate_resolved: int = 0
    approvals_closed: int = 0
    background_killed: int = 0
    child_threads_closed: int = 0
    artifacts_cleaned: int = 0
    safe: bool = True


class ReconciliationService:
    def __init__(self, background_task_service: Optional[Any] = None):
        self._background_task_service = background_task_service

    def retry_allowed(self, run_state: RunState, transaction_id: str) -> bool:
        """该事务是否允许直接重试。

        §6.5：indeterminate 必须先 reconcile，禁止直接重试；僵尸 running / approved /
        approval_required（reconcile 会先记 indeterminate 再落定，副作用可能已发生）
        同样禁止直接重试——必须先 reconcile 落定再开新事务（§25.6 不重复已确认副作用）。
        其余状态（proposed/validated/permission_evaluated 副作用未开始）允许。
        """
        for tx in run_state.pending_tool_transactions:
            if tx.transaction_id == transaction_id and tx.status in (
                ToolStatus.indeterminate,
                ToolStatus.approval_required,
                ToolStatus.approved,
                ToolStatus.running,
            ):
                return False  # 未 reconcile 禁止直接重试
        return True

    def reconcile(self, authority: JournalAuthority, run_state: RunState) -> ReconcileResult:
        result = ReconcileResult()
        # 1) indeterminate / 僵尸 pending → 先记 indeterminate（若非已 indeterminate），再 result.committed 落定
        for tx in list(run_state.pending_tool_transactions):
            status = tx.status
            if status in (ToolStatus.indeterminate, ToolStatus.approval_required,
                          ToolStatus.approved, ToolStatus.running):
                if status != ToolStatus.indeterminate:
                    authority.emit(
                        "tool.execution.indeterminate",
                        {"transaction_id": tx.transaction_id, "call_id": tx.call_id,
                         "tool_id": tx.tool_id, "reason": "reconciled_pending",
                         "idempotency_key": tx.idempotency_key},
                    )
                    result.indeterminate_resolved += 1
                authority.emit(
                    "tool.result.committed",
                    {"transaction_id": tx.transaction_id, "call_id": tx.call_id,
                     "tool_id": tx.tool_id, "result_ref": "",
                     "verdict": "failed"},
                )
                result.indeterminate_resolved += 1
        # 2) 悬空 approval → deny 落定
        for ask in list(run_state.pending_approvals):
            authority.emit("tool.approval.resolved",
                           {"ask_id": ask.ask_id, "call_id": ask.call_id, "approved": False})
            result.approvals_closed += 1
        # 3) 僵尸 child thread → cancelled（沿用 P2 thread 事件）
        for ct in list(run_state.child_threads):
            if ct.status == "running":
                authority.emit("thread.cancelled",
                               {"thread_id": ct.thread_id, "parent_call_id": ct.parent_call_id,
                                "summary": "reconciled"})
                result.child_threads_closed += 1
        # 4) 后台任务清理（P6）：active_background_tasks 为 task_id 列表，
        #    经 kill_task 走完整 kill 验证链（§12）。
        if self._background_task_service is not None:
            for task_id in list(run_state.active_background_tasks):
                try:
                    if self._background_task_service.kill_task(task_id):
                        result.background_killed += 1
                except Exception as e:
                    logger.warning("BackgroundTask reconcile kill failed task=%s: %s", task_id, e)
        return result
