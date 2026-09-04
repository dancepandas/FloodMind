"""确定性 Reducer：reduce(state, event) -> state（目标 §5.1/§2.8）。

纯函数：无 I/O、无随机、无当前时间、无全局单例。给定相同事件序列必须产生相同状态。
每次 reduce 返回新状态，不修改输入。
"""

from typing import Dict, Any

from floodmind.agent.runtime.contracts.run_state import (
    RunState, RunStatus, PendingApproval, ChildThreadState,
)
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.contracts.tool_transaction import (
    SideEffectClass, ToolStatus, ToolTransaction,
)


def initial_run_state(run_id: str, *, conversation_id: str = "", task_id: str = "",
                      thread_id: str = "") -> RunState:
    return RunState(
        run_id=run_id,
        conversation_id=conversation_id,
        task_id=task_id,
        current_thread_id=thread_id,
        status=RunStatus.created,
    )


def _clone(state: RunState) -> RunState:
    return state.model_copy(deep=True)


def _turn_index(turns: list) -> int:
    if not turns:
        return 0
    return max(int(t.get("turn_index", 0)) for t in turns) + 1


def _is_current_thread(state: RunState, thread_id: str) -> bool:
    """事件属于当前线程。current_thread_id 为空（未定义线程）视为当前。"""
    if not state.current_thread_id:
        return True
    return thread_id in ("", state.current_thread_id)


def _reduce_thread_spawn(state: RunState, payload: Dict[str, Any]) -> RunState:
    ns = _clone(state)
    return ns


def _reduce_thread_created(state: RunState, payload: Dict[str, Any]) -> RunState:
    ns = _clone(state)
    ns.child_threads.append(ChildThreadState(
        thread_id=str(payload["thread_id"]),
        parent_call_id=str(payload.get("parent_call_id", "")),
        status="running",
    ))
    return ns


def _reduce_thread_terminal(
    state: RunState,
    payload: Dict[str, Any],
    event_type: str,
) -> RunState:
    ns = _clone(state)
    tid = str(payload.get("thread_id", ""))
    for ct in ns.child_threads:
        if ct.thread_id == tid:
            ct.status = {
                "thread.completed": "completed",
                "thread.failed": "failed",
                "thread.cancelled": "cancelled",
            }.get(event_type, "running")
    return ns


def _reduce_child_thread_accepted(state, payload):
    ns = _clone(state)
    ns.child_threads.append(ChildThreadState(
        thread_id=str(payload["thread_id"]),
        parent_thread_id=str(payload.get("parent_thread_id", "")),
        parent_call_id=str(payload.get("parent_call_id", "")),
        status="accepted",
    ))
    return ns


def _reduce_child_thread_running(state, payload):
    ns = _clone(state)
    tid = str(payload.get("thread_id", ""))
    for ct in ns.child_threads:
        if ct.thread_id == tid:
            ct.status = "running"
    return ns


def _reduce_child_thread_terminal(state, payload, event_type):
    ns = _clone(state)
    tid = str(payload.get("thread_id", ""))
    status = {
        "child_thread.result": "completed",
        "child_thread.failed": "failed",
        "child_thread.cancelled": "cancelled",
    }.get(event_type, "running")
    for ct in ns.child_threads:
        if ct.thread_id == tid:
            ct.status = status
            ct.reason = str(payload.get("reason", ""))
    return ns


def _reduce_thread_message_sent(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    content = str(payload.get("content", ""))
    turn_index = int(payload.get("turn_index", _turn_index(ns.turns)))
    ns.turns.append({
        "role": "user", "content": content, "turn_index": turn_index,
        "thread_id": thread_id,
    })
    if ns.status in (RunStatus.created, RunStatus.completed, RunStatus.failed):
        ns.status = RunStatus.awaiting_model
    return ns


def _reduce_attempt_started(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ns.active_attempt_id = str(payload.get("attempt_id") or "")
    ns.status = RunStatus.streaming_model
    return ns


def _reduce_attempt_completed(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    usage = payload.get("usage") or {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        ns.token_usage[key] = int(ns.token_usage.get(key, 0)) + int(usage.get(key, 0))
    if not _is_current_thread(ns, thread_id):
        return ns
    tool_calls = payload.get("tool_calls") or []
    is_final = bool(payload.get("is_final"))
    ns.turns.append({
        "role": "assistant",
        "turn_index": max((int(t.get("turn_index", 0)) for t in ns.turns), default=0),
        "content": str(payload.get("content", "")),
        "reasoning": str(payload.get("reasoning", "")),
        "tool_calls": list(tool_calls),
        "is_final": is_final,
        "timestamp": "",
        "thread_id": thread_id,
    })
    terminal = str(payload.get("terminal_reason", ""))
    if terminal == "tool_calls" and tool_calls:
        ns.status = RunStatus.awaiting_tool
    elif is_final or terminal == "completed":
        ns.status = RunStatus.completed
    return ns


def _reduce_attempt_failed(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ns.status = RunStatus.failed
    return ns


def _find_ttx(ns: RunState, ttx_id: str):
    return next((t for t in ns.pending_tool_transactions if t.transaction_id == ttx_id), None)


def _update_ttx(ns: RunState, ttx_id: str, to: ToolStatus, **update) -> RunState:
    """把 pending 中对应事务 transition 到 to；非法转移忽略（fail-closed）。"""
    tx = _find_ttx(ns, ttx_id)
    if tx is None:
        return ns
    try:
        moved = tx.transition(to)
    except ValueError:
        return ns
    fields = {"status": moved.status}
    fields.update(update)
    ns.pending_tool_transactions = [
        (m.model_copy(update=fields) if m.transaction_id == ttx_id else m)
        for m in ns.pending_tool_transactions
    ]
    return ns


def _remove_ttx(ns: RunState, ttx_id: str) -> RunState:
    ns.pending_tool_transactions = [
        t for t in ns.pending_tool_transactions if t.transaction_id != ttx_id
    ]
    return ns


# §6.4 终态机的线性执行链。denied 为旁路终态（不在链中），由 permission 处理器
# 单独 remove；succeeded/failed/cancelled/indeterminate 是 running 之后的并列终态，
# 链式前进只允许沿正向分支走（indeterminate -> running 属回退，被 _advance_to 拒绝）。
_CHAIN = [
    ToolStatus.proposed, ToolStatus.validated, ToolStatus.permission_evaluated,
    ToolStatus.approval_required, ToolStatus.approved, ToolStatus.running,
    ToolStatus.succeeded, ToolStatus.failed, ToolStatus.cancelled,
    ToolStatus.indeterminate, ToolStatus.result_committed,
]


def _advance_to(ns: RunState, ttx_id: str, target: ToolStatus, **update) -> RunState:
    """沿 §6.4 合法链前进到 target；不可达/回退/非法边 fail-closed（返回 ns 不变）。"""
    tx = _find_ttx(ns, ttx_id)
    if tx is None:
        return ns
    if target not in _CHAIN or tx.status not in _CHAIN:
        return ns
    cur = _CHAIN.index(tx.status)
    tgt = _CHAIN.index(target)
    if tgt < cur:
        return ns  # 不能回退（含 indeterminate -> running 被拒）
    walk = tx
    for s in _CHAIN[cur + 1:tgt + 1]:
        try:
            walk = walk.transition(s)
        except ValueError:
            return ns  # fail-closed
    fields = {"status": walk.status}
    fields.update(update)
    ns.pending_tool_transactions = [
        (m.model_copy(update=fields) if m.transaction_id == ttx_id else m)
        for m in ns.pending_tool_transactions
    ]
    return ns


def _reduce_tool_proposed(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ttx_id = str(payload.get("transaction_id", "") or "")
    if not ttx_id:
        return ns  # 无 transaction_id：不建立垃圾 pending 项，仅推进 cursor
    raw_se = payload.get("side_effect_class")
    try:
        side_effect = SideEffectClass(raw_se) if raw_se else SideEffectClass.read
    except ValueError:
        side_effect = SideEffectClass.read  # 非法串 fail-closed 到 read，不抛异常
    ns.pending_tool_transactions.append(ToolTransaction(
        transaction_id=ttx_id,
        call_id=str(payload.get("call_id", "")),
        tool_id=str(payload.get("tool_id", "")),
        tool_version=str(payload.get("tool_version", "1")),
        canonical_arguments=str(payload.get("canonical_arguments", "")),
        arguments_sha256=str(payload.get("arguments_sha256", "")),
        side_effect_class=side_effect,
        idempotency_key=str(payload.get("idempotency_key", "")),
        preconditions=list(payload.get("preconditions") or []),
        status=ToolStatus.proposed,
    ))
    ns.status = RunStatus.awaiting_tool
    return ns


def _reduce_tool_validated(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    return _advance_to(ns, str(payload.get("transaction_id", "")), ToolStatus.validated)


def _reduce_tool_permission(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ttx_id = str(payload.get("transaction_id", ""))
    decision = str(payload.get("decision", ""))
    fingerprint = str(payload.get("approval_fingerprint", ""))
    if decision == "deny":
        _advance_to(ns, ttx_id, ToolStatus.denied)  # denied 为旁路终态（不在 _CHAIN），仅作记档尝试
        return _remove_ttx(ns, ttx_id)  # denied 终态，移出 pending
    if decision == "allow":
        return _advance_to(ns, ttx_id, ToolStatus.approved, permission_fingerprint=fingerprint)
    return ns  # ask 由 tool.approval.required 事件推进，这里保持现状


def _reduce_tool_approval_required(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ttx_id = str(payload.get("transaction_id", ""))
    fingerprint = str(payload.get("approval_fingerprint", ""))
    ns = _advance_to(ns, ttx_id, ToolStatus.approval_required,
                     permission_fingerprint=fingerprint)
    # 只有事务确实推进到 approval_required 才置 awaiting_approval：
    # 过期/回退事件（_advance_to 失败）不得把运行状态错误翻成等待授权。
    tx = _find_ttx(ns, ttx_id)
    if tx is not None and tx.status == ToolStatus.approval_required:
        ns.status = RunStatus.awaiting_approval
    return ns


def _reduce_tool_started(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ttx_id = str(payload.get("transaction_id", ""))
    if _find_ttx(ns, ttx_id) is not None:
        # 沿 §6.4 链前进到 running（P2 风格时序下 proposed -> started 会补走
        # validated/permission_evaluated/approval_required/approved）。
        # 过期的 started（如 indeterminate 之后）因回退被 fail-closed，不翻回 running。
        ns = _advance_to(ns, ttx_id, ToolStatus.running)
        if _find_ttx(ns, ttx_id).status == ToolStatus.running:
            ns.status = RunStatus.executing_tool
    else:
        ns.pending_tool_transactions.append(ToolTransaction(
            transaction_id=ttx_id,
            call_id=str(payload.get("call_id", "")),
            tool_id=str(payload.get("tool_id", "")),
            canonical_arguments=str(payload.get("arguments", "")),
            status=ToolStatus.running,
        ))
        ns.status = RunStatus.executing_tool
    return ns


def _reduce_tool_cancelled(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    return _remove_ttx(ns, str(payload.get("transaction_id", "")))


def _reduce_tool_indeterminate(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    return _update_ttx(ns, str(payload.get("transaction_id", "")), ToolStatus.indeterminate)


def _reduce_tool_result_committed(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ttx_id = str(payload.get("transaction_id", ""))
    if str(payload.get("verdict", "")) == "succeeded":
        ns = _update_ttx(ns, ttx_id, ToolStatus.result_committed)
    ns = _remove_ttx(ns, ttx_id)
    if not ns.pending_tool_transactions:
        ns.status = RunStatus.awaiting_model
    return ns


def _reduce_tool_completed(
    state: RunState,
    payload: Dict[str, Any],
    event_type: str,
    thread_id: str,
) -> RunState:
    ns = _clone(state)
    is_current = _is_current_thread(ns, thread_id)
    ttx_id = str(payload.get("transaction_id", ""))
    if is_current:
        ns = _remove_ttx(ns, ttx_id)
    result_summary = str(
        payload.get("result_summary")
        or payload.get("reason")
        or payload.get("error")
        or ("tool execution failed" if event_type == "tool.execution.failed" else "")
    )
    if is_current:
        ns.turns.append({
            "role": "tool",
            "tool_call_id": str(payload.get("call_id", "")),
            "tool_id": str(payload.get("tool_id", "")),
            "content": result_summary,
            "turn_index": _turn_index(ns.turns),
            "thread_id": thread_id,
        })
    for art in payload.get("artifacts") or []:
        if art not in ns.artifacts:
            ns.artifacts.append(str(art))
    if is_current and not ns.pending_tool_transactions:
        ns.status = RunStatus.awaiting_model
    return ns


def _reduce_approval_requested(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ns.pending_approvals.append(PendingApproval(
        ask_id=str(payload["ask_id"]),
        call_id=str(payload["call_id"]),
        tool_name=str(payload.get("tool_name", "")),
        reason=str(payload.get("reason", "")),
    ))
    ns.status = RunStatus.awaiting_approval
    return ns


def _reduce_approval_resolved(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ask_id = str(payload.get("ask_id", ""))
    ns.pending_approvals = [a for a in ns.pending_approvals if a.ask_id != ask_id]
    if not ns.pending_approvals:
        ns.status = RunStatus.awaiting_model
    return ns


def _reduce_compaction(
    state: RunState, payload: Dict[str, Any], event_type: str, thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ns.status = RunStatus.compacting if event_type.endswith("started") else RunStatus.awaiting_model
    return ns


def _reduce_run_terminal(
    state: RunState, payload: Dict[str, Any], event_type: str, thread_id: str,
) -> RunState:
    ns = _clone(state)
    if not _is_current_thread(ns, thread_id):
        return ns
    ns.status = RunStatus.failed if event_type == "run.failed" else RunStatus.completed
    ns.last_committed_sequence = ns.last_committed_sequence
    return ns


def _reduce_artifact_declared(
    state: RunState, payload: Dict[str, Any],
) -> RunState:
    """artifact.declared 仅为提交前元数据；不构成 durable artifact fact。"""
    return state


def _reduce_artifact_committed(
    state: RunState, payload: Dict[str, Any],
) -> RunState:
    """artifact.committed：正式落库，计入 artifacts（去重）。"""
    artifact_id = payload.get("artifact_id", "")
    if not artifact_id:
        return state
    if artifact_id in state.artifacts:
        return state
    ns = state.model_copy(deep=True)
    ns.artifacts = state.artifacts + [artifact_id]
    return ns


def _reduce_background_started(
    state: RunState, payload: Dict[str, Any],
) -> RunState:
    """background.started：run 级 active_background_tasks 记账（task_id，去重）。"""
    task_id = payload.get("task_id", "")
    if not task_id:
        return state
    if task_id in state.active_background_tasks:
        return state
    ns = state.model_copy(deep=True)
    ns.active_background_tasks = state.active_background_tasks + [task_id]
    return ns


def _reduce_background_terminal(
    state: RunState, payload: Dict[str, Any],
) -> RunState:
    """background.killed / kill.failed / completed：从 active_background_tasks 移除。"""
    task_id = payload.get("task_id", "")
    if not task_id or task_id not in state.active_background_tasks:
        return state
    ns = state.model_copy(deep=True)
    ns.active_background_tasks = [t for t in state.active_background_tasks if t != task_id]
    return ns


def reduce(state: RunState, event: EventEnvelope) -> RunState:
    """确定性折叠。未知事件 fail closed：保持不变但推进 cursor。"""
    if event.event_id in state.processed_event_ids:
        return state  # duplicate: no re-apply, no cursor bump
    ns = _clone(state)
    ns.processed_event_ids = ns.processed_event_ids + [event.event_id]
    ns.last_committed_sequence = event.sequence
    et = event.event_type
    if et == "agent.handoff.requested":
        return ns
    if et == "agent.handoff.completed":
        ns.active_agent = str(event.payload.get("target_agent", ""))
        return ns
    if et == "thread.message.sent":
        return _reduce_thread_message_sent(ns, event.payload, event.thread_id)
    if et == "thread.spawn.requested":
        return _reduce_thread_spawn(ns, event.payload)
    if et == "thread.created":
        return _reduce_thread_created(ns, event.payload)
    if et in ("thread.completed", "thread.failed", "thread.cancelled"):
        return _reduce_thread_terminal(ns, event.payload, et)
    if et == "child_thread.accepted":
        return _reduce_child_thread_accepted(ns, event.payload)
    if et == "child_thread.running":
        return _reduce_child_thread_running(ns, event.payload)
    if et in ("child_thread.result", "child_thread.failed", "child_thread.cancelled"):
        return _reduce_child_thread_terminal(ns, event.payload, et)
    if et == "model.attempt.started":
        return _reduce_attempt_started(ns, event.payload, event.thread_id)
    if et == "model.attempt.completed":
        return _reduce_attempt_completed(ns, event.payload, event.thread_id)
    if et == "model.attempt.failed":
        return _reduce_attempt_failed(ns, event.payload, event.thread_id)
    if et == "tool.call.proposed":
        return _reduce_tool_proposed(ns, event.payload, event.thread_id)
    if et == "tool.call.validated":
        return _reduce_tool_validated(ns, event.payload, event.thread_id)
    if et == "tool.permission.evaluated":
        return _reduce_tool_permission(ns, event.payload, event.thread_id)
    if et == "tool.approval.required":
        return _reduce_tool_approval_required(ns, event.payload, event.thread_id)
    if et == "tool.execution.started":
        return _reduce_tool_started(ns, event.payload, event.thread_id)
    if et == "tool.execution.cancelled":
        return _reduce_tool_cancelled(ns, event.payload, event.thread_id)
    if et == "tool.execution.indeterminate":
        return _reduce_tool_indeterminate(ns, event.payload, event.thread_id)
    if et == "tool.result.committed":
        return _reduce_tool_result_committed(ns, event.payload, event.thread_id)
    if et in ("tool.execution.completed", "tool.execution.failed"):
        return _reduce_tool_completed(ns, event.payload, et, event.thread_id)
    if et == "tool.approval.requested":
        return _reduce_approval_requested(ns, event.payload, event.thread_id)
    if et == "tool.approval.resolved":
        return _reduce_approval_resolved(ns, event.payload, event.thread_id)
    if et in ("context.compaction.started", "context.compaction.completed"):
        return _reduce_compaction(ns, event.payload, et, event.thread_id)
    if et in ("run.completed", "run.failed"):
        return _reduce_run_terminal(ns, event.payload, et, event.thread_id)
    if et in ("artifact.declared", "artifact.committed"):
        if et == "artifact.declared":
            return _reduce_artifact_declared(ns, event.payload)
        return _reduce_artifact_committed(ns, event.payload)
    if et == "background.started":
        return _reduce_background_started(ns, event.payload)
    if et in ("background.killed", "background.kill.failed", "background.completed"):
        return _reduce_background_terminal(ns, event.payload)
    return ns  # 其他事件（usage/checkpoint/thread.*）不改状态，仅推进 cursor
