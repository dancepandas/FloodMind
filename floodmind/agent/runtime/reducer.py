"""确定性 Reducer：reduce(state, event) -> state（目标 §5.1/§2.8）。

纯函数：无 I/O、无随机、无当前时间、无全局单例。给定相同事件序列必须产生相同状态。
每次 reduce 返回新状态，不修改输入。
"""

from typing import Dict, Any

from floodmind.agent.runtime.contracts.run_state import (
    RunState, RunStatus, PendingToolTransaction, PendingApproval, ChildThreadState,
)
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope


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


def _reduce_thread_message_sent(
    state: RunState, payload: Dict[str, Any], thread_id: str,
) -> RunState:
    ns = _clone(state)
    content = str(payload.get("content", ""))
    turn_index = int(payload.get("turn_index", _turn_index(ns.turns)))
    ns.turns.append({
        "role": "user", "content": content, "turn_index": turn_index,
        "thread_id": thread_id,
    })
    if ns.status in (RunStatus.created, RunStatus.completed, RunStatus.failed):
        ns.status = RunStatus.awaiting_model
    return ns


def _reduce_attempt_started(state: RunState, payload: Dict[str, Any]) -> RunState:
    ns = _clone(state)
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


def _reduce_attempt_failed(state: RunState, payload: Dict[str, Any]) -> RunState:
    ns = _clone(state)
    ns.status = RunStatus.failed
    return ns


def _reduce_tool_started(state: RunState, payload: Dict[str, Any]) -> RunState:
    ns = _clone(state)
    ttx = PendingToolTransaction(
        transaction_id=str(payload["transaction_id"]),
        call_id=str(payload["call_id"]),
        tool_id=str(payload["tool_id"]),
        status="running",
    )
    ns.pending_tool_transactions.append(ttx)
    ns.status = RunStatus.executing_tool
    return ns


def _reduce_tool_completed(
    state: RunState,
    payload: Dict[str, Any],
    event_type: str,
    thread_id: str,
) -> RunState:
    ns = _clone(state)
    ttx_id = str(payload.get("transaction_id", ""))
    ns.pending_tool_transactions = [
        t for t in ns.pending_tool_transactions if t.transaction_id != ttx_id
    ]
    result_summary = str(
        payload.get("result_summary")
        or payload.get("reason")
        or payload.get("error")
        or ("tool execution failed" if event_type == "tool.execution.failed" else "")
    )
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
    if not ns.pending_tool_transactions:
        ns.status = RunStatus.awaiting_model
    return ns


def _reduce_approval_requested(state: RunState, payload: Dict[str, Any]) -> RunState:
    ns = _clone(state)
    ns.pending_approvals.append(PendingApproval(
        ask_id=str(payload["ask_id"]),
        call_id=str(payload["call_id"]),
        tool_name=str(payload.get("tool_name", "")),
        reason=str(payload.get("reason", "")),
    ))
    ns.status = RunStatus.awaiting_approval
    return ns


def _reduce_approval_resolved(state: RunState, payload: Dict[str, Any]) -> RunState:
    ns = _clone(state)
    ask_id = str(payload.get("ask_id", ""))
    ns.pending_approvals = [a for a in ns.pending_approvals if a.ask_id != ask_id]
    if not ns.pending_approvals:
        ns.status = RunStatus.awaiting_model
    return ns


def _reduce_compaction(state: RunState, payload: Dict[str, Any], event_type: str) -> RunState:
    ns = _clone(state)
    ns.status = RunStatus.compacting if event_type.endswith("started") else RunStatus.awaiting_model
    return ns


def _reduce_run_terminal(state: RunState, payload: Dict[str, Any], event_type: str) -> RunState:
    ns = _clone(state)
    ns.status = RunStatus.failed if event_type == "run.failed" else RunStatus.completed
    ns.last_committed_sequence = ns.last_committed_sequence
    return ns


def reduce(state: RunState, event: EventEnvelope) -> RunState:
    """确定性折叠。未知事件 fail closed：保持不变但推进 cursor。"""
    if event.event_id in state.processed_event_ids:
        return state  # duplicate: no re-apply, no cursor bump
    ns = _clone(state)
    ns.processed_event_ids = ns.processed_event_ids + [event.event_id]
    ns.last_committed_sequence = event.sequence
    et = event.event_type
    if et == "thread.message.sent":
        return _reduce_thread_message_sent(ns, event.payload, event.thread_id)
    if et == "thread.spawn.requested":
        return _reduce_thread_spawn(ns, event.payload)
    if et == "thread.created":
        return _reduce_thread_created(ns, event.payload)
    if et in ("thread.completed", "thread.failed", "thread.cancelled"):
        return _reduce_thread_terminal(ns, event.payload, et)
    if et == "model.attempt.started":
        return _reduce_attempt_started(ns, event.payload)
    if et == "model.attempt.completed":
        return _reduce_attempt_completed(ns, event.payload, event.thread_id)
    if et == "model.attempt.failed":
        return _reduce_attempt_failed(ns, event.payload)
    if et == "tool.execution.started":
        return _reduce_tool_started(ns, event.payload)
    if et in ("tool.execution.completed", "tool.execution.failed"):
        return _reduce_tool_completed(ns, event.payload, et, event.thread_id)
    if et == "tool.approval.requested":
        return _reduce_approval_requested(ns, event.payload)
    if et == "tool.approval.resolved":
        return _reduce_approval_resolved(ns, event.payload)
    if et in ("context.compaction.started", "context.compaction.completed"):
        return _reduce_compaction(ns, event.payload, et)
    if et in ("run.completed", "run.failed"):
        return _reduce_run_terminal(ns, event.payload, et)
    return ns  # 其他事件（usage/checkpoint/thread.*）不改状态，仅推进 cursor
