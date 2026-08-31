"""公开 Checkpoint Resume（目标 §16.4）。fencing lease + replay + reconcile。

流程（§16.4 1-9）：
1. `open_lease` 获取 fencing lease（已被持有时抛 `ResumeBusyError`）。
2. `open_journal_authority` 打开 canonical journal 权威门面。
3. 非空 `checkpoint_id` 时走 P2 `replay_from_checkpoint` 完整校验
   （5-part identity + cursor + reducer/tool registry 版本，fail-closed）。
4. emit `resume.started`。
5. `replay()` 得到权威 RunState；`ReconciliationService.reconcile` 落定
   indeterminate / 僵尸 pending；再 settle reconcile 集合之外的 pre-execution
   僵尸（proposed/validated/permission_evaluated），然后重放。
6. 若 `user_message` 非空：emit `thread.message.sent` 作为新事件，重放。
7. emit `resume.completed`，返回 `ResumeOutcome`。

任何异常路径都释放 lease 再抛出，保证 fencing 不悬挂。
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, PrivateAttr

from floodmind.agent.runtime.contracts.run_state import RunState
from floodmind.agent.runtime.contracts.tool_transaction import ToolStatus
from floodmind.agent.runtime.services.journal_authority import JournalAuthority, open_journal_authority
from floodmind.agent.runtime.services.reconciliation_service import (
    ReconciliationService,
    ReconcileResult,
)
from floodmind.agent.runtime.services.runtime_layout import lease_file
from floodmind.common.filelock import FileLock


class ResumeBusyError(RuntimeError):
    """目标 run 已被其他 owner 持有（lease 未过期），resume 被 fencing。"""


class Lease(BaseModel):
    acquired: bool
    owner: str = ""
    expires_at: str = ""

    _path: Optional[Path] = PrivateAttr(default=None)
    _token: str = PrivateAttr(default="")

    def release(self) -> None:
        """释放 lease。带 owner token 校验：TTL 过期被新 owner 抢占后，
        旧 owner 迟到的 release 不会误删新 owner 的 lease（D06）。"""
        path = self._path
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("token", "")) != self._token:
                return  # lease 已易主
            path.unlink()
        except OSError:
            pass
        except Exception:
            pass


def open_lease(runtime_dir, run_id, owner: str, ttl_seconds: int = 300,
               *, conversation_id: str = "", task_id: str = "") -> Lease:
    """CAS 获取 fencing lease。

    已存在且未过期的 lease → 抛 `ResumeBusyError`；过期/损坏的 lease 覆盖重写。
    `Lease.release()` 删除 lease 文件（token 校验防误删）。

    D06 修复：exists→read→write 三步在跨进程 FileLock 内执行，消除两进程同时
    通过检查的 TOCTOU；lease 写入 tmp+os.replace 原子发布。
    注意：journal 写路径仍未逐条校验 lease epoch（完整 fencing 需写端配合），
    本层保证的是"同一时刻至多一个 owner 通过 open_lease 入场"。
    """
    path = lease_file(Path(runtime_dir), conversation_id, task_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    token = f"{owner}:{uuid.uuid4().hex}"
    with FileLock(Path(str(path) + ".lock"), timeout=10.0):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if int(data.get("expires_at", 0)) > now:
                    raise ResumeBusyError(
                        f"run {run_id} 已被 {data.get('owner')} 持有（lease 未过期）"
                    )
            except ResumeBusyError:
                raise
            except Exception:
                pass  # 损坏/过期 lease 覆盖
        payload = {
            "owner": owner,
            "token": token,
            "pid": str(os.getpid()),
            "acquired_at": now,
            "expires_at": now + ttl_seconds,
        }
        tmp_path = Path(str(path) + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, path)
    lease = Lease(
        acquired=True,
        owner=owner,
        expires_at=datetime.fromtimestamp(
            now + ttl_seconds, timezone.utc
        ).isoformat(),
    )
    lease._path = path
    lease._token = token
    return lease


class ResumeOutcome(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_state: RunState
    journal_cursor: int
    reconciled: ReconcileResult
    lease: Lease

    # Optional seams for the SDK/desktop contract: expose the JournalAuthority the
    # service opened so callers can rebind it onto an executor without going
    # through a fresh stream().  Set by ResumeService().resume(); None when an
    # outcome is constructed elsewhere (tests, previews).
    authority: Optional[JournalAuthority] = None
    identity: Optional[Dict[str, str]] = None


# reconcile 集合之外的 pre-execution 僵尸（§6.4 尚未进入 running 的中间态）。
# 崩溃点在 started 之前的 pending 事务由 ResumeService 额外落定，防止残留。
_PRE_EXECUTION_STATUSES = frozenset({
    ToolStatus.proposed,
    ToolStatus.validated,
    ToolStatus.permission_evaluated,
})


class ResumeService:
    def resume(self, *, runtime_dir, conversation_id, task_id, run_id, thread_id,
               turn_id, checkpoint_id: str = "", user_message: str = "",
               session_id: str = "", checkpoint_service=None,
               expected_reducer_version: str = "1",
               expected_tool_registry_version: str = "") -> ResumeOutcome:
        """§16.4 公开 resume：fencing + 校验 + replay + reconcile + 事件。

        非空 `checkpoint_id` 需要 `session_id` 与 `checkpoint_service`
        （指向 checkpoint base_dir）以执行 P2 `replay_from_checkpoint` 校验。
        """
        lease = open_lease(runtime_dir, run_id, owner="resume",
                           conversation_id=conversation_id, task_id=task_id)
        try:
            authority = open_journal_authority(
                runtime_dir, conversation_id=conversation_id, task_id=task_id,
                run_id=run_id, thread_id=thread_id, turn_id=turn_id)

            if checkpoint_id:
                if checkpoint_service is None:
                    raise ValueError(
                        "resume: checkpoint_id 非空时必须提供 checkpoint_service"
                    )
                if not session_id:
                    raise ValueError(
                        "resume: checkpoint_id 非空时必须提供 session_id"
                    )
                # P2 replay_from_checkpoint：5-part identity + cursor + 版本校验，
                # 校验失败 fail-closed（抛 CheckpointConsistencyError）。
                checkpoint_service.replay_from_checkpoint(
                    authority, session_id, checkpoint_id,
                    reducer_version=expected_reducer_version,
                    expected_tool_registry_version=expected_tool_registry_version,
                )

            authority.emit(
                "resume.started",
                {"checkpoint_id": checkpoint_id, "cursor": authority.cursor()},
            )
            state = authority.replay()
            reconciled = ReconciliationService().reconcile(authority, state)
            state = authority.replay()

            # reconcile 集合 {indeterminate, approval_required, approved, running}
            # 之外的 pre-execution 僵尸：落定 failed 移出 pending。
            for tx in list(state.pending_tool_transactions):
                if tx.status in _PRE_EXECUTION_STATUSES:
                    authority.emit(
                        "tool.execution.indeterminate",
                        {"transaction_id": tx.transaction_id, "call_id": tx.call_id,
                         "tool_id": tx.tool_id, "reason": "reconciled_pending",
                         "idempotency_key": tx.idempotency_key},
                    )
                    authority.emit(
                        "tool.result.committed",
                        {"transaction_id": tx.transaction_id, "call_id": tx.call_id,
                         "tool_id": tx.tool_id, "result_ref": "", "verdict": "failed"},
                    )
                    reconciled.indeterminate_resolved += 2
            state = authority.replay()

            if user_message:
                authority.emit(
                    "thread.message.sent",
                    {"content": user_message, "turn_index": len(state.turns)},
                )
                state = authority.replay()

            authority.emit("resume.completed", {"cursor": authority.cursor()})
            return ResumeOutcome(
                run_state=state,
                journal_cursor=authority.cursor(),
                reconciled=reconciled,
                lease=lease,
                authority=authority,
                identity={
                    "conversation_id": conversation_id,
                    "task_id": task_id,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "runtime_dir": str(Path(runtime_dir)),
                },
            )
        except Exception:
            lease.release()
            raise
