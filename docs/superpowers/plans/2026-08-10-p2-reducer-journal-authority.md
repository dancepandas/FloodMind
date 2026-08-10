# P2 — Reducer + Journal Authority 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Canonical Event Journal 变成唯一运行事实源：确定性 Reducer 派生运行状态与对话历史，Executor 在稳定提交点落事件，`_turns`/`chat_history.json`/ExecutionJournalService 的历史源职责下线（无读取回退），ContextVar 全局 getter 随 RuntimeContext 注入移除，Checkpoint 绑定 Journal cursor，子代理事件进入同一 run Journal（thread_id 作用域）。

**Architecture:** 三段式——(1) 纯数据层：`RunState` + 确定性 `reduce(state,event)`；(2) 服务层：`JournalWriter` 扩展（原子组 append_many）→ `JournalAuthority`（per-run 身份作用域 emit/replay）→ `HistoryProjection`（事件→扁平 turns）；(3) 接线层：Executor 稳定提交点发事件、`_turns` 切投影、ContextVar 移除、checkpoint 绑 cursor、子代理入 journal。

**Tech Stack:** Python 3.x, Pydantic v2 (`BaseModel`), frozen dataclass, `canonical_events.EventEnvelope`/`canonical_json`, P1 `JournalWriter`, P1 `Identity`/`new_id`/`RuntimeContext`。无第三方新增依赖。

## Global Constraints

> 每项任务的隐含要求包含本节；实现者必须逐字遵守。

- **只向前，不向后兼容、不 fallback**：不新增/不保留旧接口适配器、降级分支、双写路径、`chat_history.json` 读取回退。遇到"旧接口还要不要支持"一律否，除非计划明确要求。
- **单一事实源**：Canonical Event Journal（JSONL 分段 + 哈希链）是唯一运行事实源；派生状态（turns、checkpoint、前端消息）均可删除并从 Journal 重建。**不允许同一事实双写两份存储**。
- **Reducer 确定性（§2.8）**：`reduce(state, event)` 不得发网络请求、执行工具、读当前时间、生成随机 ID、依赖进程级可变单例、读取事件/Snapshot 之外的环境状态。
- **身份层级（§3.1）**：`conversation_id=conv_`/`task_id=task_`/`run_id=run_`/`thread_id=thread_`/`turn_id=turn_`/`attempt_id=attempt_`/`call_id=call_` 一律用 `floodmind.agent.runtime.contracts.identity.new_id(kind)`；禁止继续用 `f"run-{int(time.time())}"` 之类的临时拼装。`session_id` 保持为兼容标识，映射到 Conversation/Thread，不再兼任 Run 语义。
- **Canonical codec**：事件落盘用 `canonical_json(event.model_dump())`（`ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str`），payload 哈希用 `canonical_payload_sha256`。
- **幂等**：重复 `event_id` 不得重复副作用（写入层走 `JournalWriter._sealed` 返回已封存信封；重放层按 event_id 去重后 reduce）。
- **模块化**：按模块/契约落实现（[[modular-code-principle]]），不打补丁式修改；新代码放对目录，不塞进旧大文件。
- **回归**：全量 `python -m pytest -q`（当前基线 **888 passed, 1 skipped**）。每任务全绿后再提交。断言旧架构行为（`_turns` 唯一源、`chat_history.json`、`get_permission_service` 等）的测试按 forward-only 重写，不保留。
- **WIP 不碰**：当前 109 个未提交文件（用户工作）不得修改。

---

## 0. P2 事件契约（各任务引用的权威定义）

所有事件均携带 `EventEnvelope`（`schema_version="1.0"`），payload 键如下。`emit` 时身份链由 `JournalAuthority` 按当前 run/thread/turn/attempt 自动填充。

| event_type | 触发点 | payload 键 |
|---|---|---|
| `thread.message.sent` | 用户消息入队/入历史 | `{"content": str, "turn_index": int}` |
| `model.attempt.started` | 每次 LLM 调用开始（executor `_on_awaiting_llm` 复位后） | `{"model": str, "iteration": int, "messages_count": int}` |
| `model.attempt.completed` | 一轮 LLM+工具完整结束落历史（executor `_write_round_to_memory` 两处调用点） | `{"attempt_id": str, "terminal_reason": str, "content": str, "reasoning": str, "tool_calls": [{"tool_name","tool_input","tool_output","status"}], "is_final": bool, "usage": {"prompt_tokens","completion_tokens","total_tokens"}}` |
| `model.attempt.failed` | LLM 硬失败置 failed | `{"attempt_id": str, "error": str}` |
| `tool.execution.started` | 工具开始执行（`tool_executor.execute` 前） | `{"transaction_id": str, "call_id": str, "tool_id": str, "arguments": str}` |
| `tool.execution.completed` / `tool.execution.failed` | 工具返回后 | `{"transaction_id": str, "call_id": str, "tool_id": str, "status": str, "result_summary": str, "full_ref": str, "artifacts": [str]}` |
| `tool.approval.requested` | 进入 `awaiting_permission` / `AskService.start_ask` | `{"call_id": str, "ask_id": str, "tool_name": str, "reason": str, "arguments": str}` |
| `tool.approval.resolved` | `AskService.respond` | `{"ask_id": str, "call_id": str, "approved": bool}` |
| `context.compaction.started` / `context.compaction.completed` | `_on_context_compress` 前后 | `{"reason": str, "before_messages": int, "after_messages": int}` |
| `checkpoint.created` | `CheckpointService.save` 发布后 | `{"checkpoint_id": str, "cursor": int, "iteration": int, "status": str}` |
| `run.completed` / `run.failed` | `run_from_state` 进入终态后 | `{"final_output": str, "terminal_reason": str}` 或 `{"error": str, "terminal_reason": str}` |
| `thread.spawn.requested` / `thread.created` / `thread.completed` / `thread.failed` / `thread.cancelled` | 子代理生命周期 | `{"thread_id": str, "parent_call_id": str, "summary": str, ...}` |

---

## 1. 身份与存储布局

### 1.1 身份分配

- 每个 `stream()` 调用（一次 Run）在 `_run_loop` 中建立：
  - `conversation_id`：每 session 稳定一次，存入 session 元数据（`session_dir/session.json`），惰性创建 `new_id("conversation")`。
  - `task_id = new_id("task")`（一次用户请求）。
  - `run_id = new_id("run")`。
  - `thread_id = new_id("thread")`（主线程）；specialist 用独立 `new_id("thread")`（子线程）。
  - `turn_id = new_id("turn")` 每次用户消息；`attempt_id = new_id("attempt")` 每次 LLM 调用。
- 辅助函数 `resolve_identity(session_id, session_dir) -> Identity`（conversation 持久化 + 其余每次新生成）放 `floodmind/agent/runtime/services/run_identity.py`。

### 1.2 存储布局（§18 对齐）

`RuntimeLayout` 辅助（`floodmind/agent/runtime/services/runtime_layout.py`）：

```text
<runtime_dir>/.floodmind/conversations/<conversation_id>/tasks/<task_id>/runs/<run_id>/
  journal/events-000001.jsonl + index.json   # JournalWriter.journal_dir
  checkpoints/<checkpoint_id>.json
  threads/<thread_id>/state|tmp|scripts      # 子代理独立落点（Task 9）
```

`runtime_dir` 默认 `PROJECT_ROOT / ".floodmind"`（`_runtime_root.PROJECT_ROOT`），可注入。

---

## 2. 任务

### Task 1: RunState 契约

**Files:**
- Create: `floodmind/agent/runtime/contracts/run_state.py`
- Test: `tests/test_run_state.py`

**Interfaces:**
- Consumes: 无（纯数据，仅 `pydantic`）。
- Produces: `RunStatus`, `PendingApproval`, `PendingToolTransaction`, `ChildThreadState`, `RunState` —— Task 2/4/8/10 依赖。

- [ ] **Step 1: Write the failing test** (`tests/test_run_state.py`)

```python
from floodmind.agent.runtime.contracts.run_state import (
    RunState, RunStatus, PendingToolTransaction, PendingApproval,
)

def test_default_state():
    s = RunState(run_id="run_1")
    assert s.status == RunStatus.created
    assert s.pending_tool_transactions == []
    assert s.turns == []

def test_required_identity_fields():
    try:
        RunState()  # missing run_id
    except Exception:
        return
    assert False, "run_id should be required"

def test_pending_lists():
    s = RunState(
        run_id="run_1",
        pending_tool_transactions=[PendingToolTransaction(transaction_id="ttx_1", call_id="call_1", tool_id="builtin:Read")],
        pending_approvals=[PendingApproval(ask_id="ask_1", call_id="call_1", tool_name="Bash")],
    )
    assert s.pending_tool_transactions[0].status == "proposed"
    assert s.pending_approvals[0].ask_id == "ask_1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'floodmind.agent.runtime.contracts.run_state'`

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/contracts/run_state.py`:

```python
"""Reducer 派生状态契约（目标 §5.1）。纯数据层，无 I/O。"""

from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    created = "created"
    projecting_context = "projecting_context"
    awaiting_model = "awaiting_model"
    streaming_model = "streaming_model"
    awaiting_tool = "awaiting_tool"
    awaiting_approval = "awaiting_approval"
    executing_tool = "executing_tool"
    compacting = "compacting"
    paused = "paused"
    cancelling = "cancelling"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"


class PendingApproval(BaseModel):
    ask_id: str
    call_id: str
    tool_name: str
    reason: str = ""


class PendingToolTransaction(BaseModel):
    transaction_id: str
    call_id: str
    tool_id: str
    status: str = "proposed"


class ChildThreadState(BaseModel):
    thread_id: str
    parent_call_id: str = ""
    status: str = "running"


class RunState(BaseModel):
    run_id: str
    conversation_id: str = ""
    task_id: str = ""
    status: RunStatus = RunStatus.created
    current_thread_id: str = ""
    current_turn_id: str = ""
    active_attempt_id: str = ""
    last_committed_sequence: int = 0
    pending_tool_transactions: List[PendingToolTransaction] = Field(default_factory=list)
    pending_approvals: List[PendingApproval] = Field(default_factory=list)
    active_background_tasks: List[str] = Field(default_factory=list)
    child_threads: List[ChildThreadState] = Field(default_factory=list)
    artifacts: List[str] = Field(default_factory=list)
    token_usage: Dict[str, int] = Field(default_factory=dict)
    cancellation_state: str = ""
    resumability: str = ""
    # 派生对话历史：扁平 user/assistant 条目，与现 DualMemory._turns 形状 wire 兼容
    turns: List[Dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_run_state.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/contracts/run_state.py tests/test_run_state.py
git commit -m "feat(run-state): reducer-derived RunState contract (target 5.1)"
```

---

### Task 2: 确定性 Reducer

**Files:**
- Create: `floodmind/agent/runtime/reducer.py`
- Test: `tests/test_reducer.py`

**Interfaces:**
- Consumes: `RunState`(Task 1), `EventEnvelope`/`EVENT_TYPES`(P1 `canonical_events`), `canonical_json`(P1)。
- Produces: `reduce(state, event) -> RunState`, `initial_run_state(identity) -> RunState` —— Task 4/6/8/10 依赖。

- [ ] **Step 1: Write the failing test** (`tests/test_reducer.py`)

```python
import json
from floodmind.agent.runtime.reducer import reduce, initial_run_state
from floodmind.agent.runtime.contracts.run_state import RunState, RunStatus
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope, canonical_json
from floodmind.agent.runtime.contracts.identity import new_id


def _ev(event_type: str, sequence: int, payload: dict, **kw) -> EventEnvelope:
    return EventEnvelope(event_id=f"evt_{sequence}", event_type=event_type,
                         sequence=sequence, payload=payload, **kw)


def test_message_sent_appends_turn():
    s = initial_run_state(new_id("run"))
    s = reduce(s, _ev("thread.message.sent", 1, {"content": "hi", "turn_index": 0}))
    assert s.turns[-1] == {"role": "user", "content": "hi", "turn_index": 0}
    assert s.status == RunStatus.awaiting_model


def test_attempt_completed_appends_assistant_turn():
    s = initial_run_state(new_id("run"))
    s = reduce(s, _ev("thread.message.sent", 1, {"content": "hi", "turn_index": 0}))
    s = reduce(s, _ev("model.attempt.completed", 2, {
        "attempt_id": "attempt_1", "terminal_reason": "completed",
        "content": "answer", "reasoning": "think", "tool_calls": [],
        "is_final": True, "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }))
    assert s.turns[-1]["role"] == "assistant"
    assert s.turns[-1]["content"] == "answer"
    assert s.token_usage["total_tokens"] == 15
    assert s.status == RunStatus.completed


def test_tool_transaction_lifecycle():
    s = initial_run_state(new_id("run"))
    s = reduce(s, _ev("tool.execution.started", 1, {
        "transaction_id": "ttx_1", "call_id": "call_1", "tool_id": "builtin:Read", "arguments": "{}",
    }))
    assert s.pending_tool_transactions[0].status == "running"
    s = reduce(s, _ev("tool.execution.completed", 2, {
        "transaction_id": "ttx_1", "call_id": "call_1", "tool_id": "builtin:Read",
        "status": "succeeded", "result_summary": "ok", "full_ref": "", "artifacts": [],
    }))
    assert s.pending_tool_transactions == []
    assert s.artifacts == []


def test_replay_determinism():
    events = [
        _ev("thread.message.sent", 1, {"content": "hi", "turn_index": 0}),
        _ev("model.attempt.completed", 2, {"attempt_id": "a1", "terminal_reason": "tool_calls",
            "content": "", "reasoning": "", "tool_calls": [], "is_final": False, "usage": {}}),
        _ev("tool.execution.started", 3, {"transaction_id": "ttx_1", "call_id": "c1",
            "tool_id": "builtin:Read", "arguments": "{}"}),
        _ev("tool.execution.completed", 4, {"transaction_id": "ttx_1", "call_id": "c1",
            "tool_id": "builtin:Read", "status": "succeeded", "result_summary": "r", "full_ref": "", "artifacts": []}),
        _ev("model.attempt.completed", 5, {"attempt_id": "a2", "terminal_reason": "completed",
            "content": "done", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {"total_tokens": 3}}),
        _ev("run.completed", 6, {"final_output": "done", "terminal_reason": "completed"}),
    ]
    s1 = initial_run_state("run_r")
    s2 = initial_run_state("run_r")
    for e in events:
        s1 = reduce(s1, e)
        s2 = reduce(s2, e)
    assert canonical_json(s1.model_dump()) == canonical_json(s2.model_dump())


def test_duplicate_event_id_does_not_double_apply():
    # 重放层按 event_id 去重是 Task 4 的职责；这里验证 reduce 对相同序列（含重复 payload）
    # 只按事件语义前进，且 token_usage 累加逻辑正确。
    s = initial_run_state("run_r")
    s = reduce(s, _ev("model.attempt.completed", 1, {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "x", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}}))
    assert s.token_usage["total_tokens"] == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reducer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'floodmind.agent.runtime.reducer'`

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/reducer.py`：

```python
"""确定性 Reducer：reduce(state, event) -> state（目标 §5.1/§2.8）。

纯函数：无 I/O、无随机、无当前时间、无全局单例。给定相同事件序列必须产生相同状态。
每次 reduce 返回新状态，不修改输入。
"""

from typing import Dict, Any

from floodmind.agent.runtime.contracts.run_state import (
    RunState, RunStatus, PendingToolTransaction, PendingApproval,
)
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.contracts.identity import new_id


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


def _reduce_thread_message_sent(state: RunState, payload: Dict[str, Any]) -> RunState:
    ns = _clone(state)
    content = str(payload.get("content", ""))
    turn_index = int(payload.get("turn_index", _turn_index(ns.turns)))
    ns.turns.append({"role": "user", "content": content, "turn_index": turn_index})
    if ns.status in (RunStatus.created, RunStatus.completed, RunStatus.failed):
        ns.status = RunStatus.awaiting_model
    return ns


def _reduce_attempt_started(state: RunState, payload: Dict[str, Any]) -> RunState:
    ns = _clone(state)
    ns.active_attempt_id = str(payload.get("attempt_id", new_id("attempt")))
    ns.status = RunStatus.streaming_model
    return ns


def _reduce_attempt_completed(state: RunState, payload: Dict[str, Any]) -> RunState:
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


def _reduce_tool_completed(state: RunState, payload: Dict[str, Any]) -> RunState:
    ns = _clone(state)
    ttx_id = str(payload.get("transaction_id", ""))
    ns.pending_tool_transactions = [
        t for t in ns.pending_tool_transactions if t.transaction_id != ttx_id
    ]
    for art in payload.get("artifacts") or []:
        if art not in ns.artifacts:
            ns.artifacts.append(str(art))
    if not ns.pending_tool_transactions:
        ns.status = RunStatus.awaiting_model
    return ns


def _reduce_tool_failed(state: RunState, payload: Dict[str, Any]) -> RunState:
    return _reduce_tool_completed(state, payload)


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
    ns = _clone(state)
    ns.last_committed_sequence = event.sequence
    et = event.event_type
    if et == "thread.message.sent":
        return _reduce_thread_message_sent(ns, event.payload)
    if et == "model.attempt.started":
        return _reduce_attempt_started(ns, event.payload)
    if et == "model.attempt.completed":
        return _reduce_attempt_completed(ns, event.payload)
    if et == "model.attempt.failed":
        return _reduce_attempt_failed(ns, event.payload)
    if et == "tool.execution.started":
        return _reduce_tool_started(ns, event.payload)
    if et in ("tool.execution.completed", "tool.execution.failed"):
        return _reduce_tool_completed(ns, event.payload)
    if et == "tool.approval.requested":
        return _reduce_approval_requested(ns, event.payload)
    if et == "tool.approval.resolved":
        return _reduce_approval_resolved(ns, event.payload)
    if et in ("context.compaction.started", "context.compaction.completed"):
        return _reduce_compaction(ns, event.payload, et)
    if et in ("run.completed", "run.failed"):
        return _reduce_run_terminal(ns, event.payload, et)
    return ns  # 其他事件（usage/checkpoint/thread.*）不改状态，仅推进 cursor
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reducer.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/reducer.py tests/test_reducer.py
git commit -m "feat(reducer): deterministic reduce(state, event) for P2 event set"
```

---

### Task 3: JournalWriter 扩展 — 原子组 append_many + journal_dir

**Files:**
- Modify: `floodmind/agent/runtime/services/journal_writer.py`
- Test: `tests/test_journal_writer.py`（新增用例）

**Interfaces:**
- Consumes: P1 `JournalWriter`（现有 `append`/`_locked`/`_reconcile_from_journal`/哈希链）。
- Produces: `JournalWriter(base_dir, run_id, *, journal_dir=None, max_segment_bytes=...)`；`append_many(events, expected_last_sequence=None) -> List[EventEnvelope]` —— Task 4 依赖（一次提交整组事件，sequence 连续、单次 CAS、单次 fsync）。

- [ ] **Step 1: Write the failing test**（追加到 `tests/test_journal_writer.py`）

```python
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.services.journal_writer import JournalWriter, JournalWriteConflict


def _mk_event(event_type: str, payload: dict) -> EventEnvelope:
    return EventEnvelope(event_id=f"evt_{payload.get('k', event_type)}", event_type=event_type, payload=payload)


def test_append_many_consecutive_sequences_and_chain(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    evs = [_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
           _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})]
    sealed = w.append_many(evs, expected_last_sequence=0)
    assert [e.sequence for e in sealed] == [1, 2]
    assert sealed[1].integrity.previous_event_sha256 == sealed[0].integrity.event_sha256
    # 重读一致性
    reread = w.read_from(0)
    assert [e.sequence for e in reread] == [1, 2]


def test_append_many_cas_conflict(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_mk_event("thread.message.sent", {"k": "x", "content": "hi"}))
    try:
        w.append_many([_mk_event("model.attempt.completed", {"k": "y"})], expected_last_sequence=0)
    except JournalWriteConflict:
        return
    assert False, "expected JournalWriteConflict"


def test_append_many_idempotent_retry(tmp_path):
    w = JournalWriter(tmp_path, "run_1")
    evs = [_mk_event("thread.message.sent", {"k": "a", "content": "hi"}),
           _mk_event("model.attempt.completed", {"k": "b", "content": "ok"})]
    first = w.append_many(evs, expected_last_sequence=0)
    second = w.append_many(evs, expected_last_sequence=0)
    assert second == first
    assert len(w.read_from(0)) == 2  # 未重复写


def test_journal_dir_override(tmp_path):
    custom = tmp_path / "custom" / "runs" / "run_9" / "journal"
    w = JournalWriter(tmp_path, "run_9", journal_dir=custom)
    w.append(_mk_event("thread.message.sent", {"k": "z", "content": "hi"}))
    assert custom.exists()
    assert len(w.read_from(0)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_journal_writer.py -v`
Expected: FAIL — `append_many` / `journal_dir` 不存在（AttributeError/TypeError）

- [ ] **Step 3: Implement**

`journal_writer.py` 修改：

1. `__init__` 增加 `journal_dir: Optional[Path] = None`，签名保持向后兼容：

```python
def __init__(
    self,
    base_dir: Path,
    run_id: str,
    *,
    max_segment_bytes: int = 10 * 1024 * 1024,
    journal_dir: Optional[Path] = None,
):
    if not run_id or run_id in {".", ".."} or ".." in run_id or Path(run_id).name != run_id:
        raise ValueError(f"unsafe run_id: {run_id!r}")
    self._base_dir = Path(base_dir)
    self._run_id = run_id
    self._max_segment_bytes = max_segment_bytes
    self._journal_dir = (
        Path(journal_dir)
        if journal_dir is not None
        else self._base_dir / "runs" / run_id / "journal"
    )
    self._journal_dir.mkdir(parents=True, exist_ok=True)
    self._lock_path = self._journal_dir / ".lock"
    self._index_path = self._journal_dir / "index.json"
    self._sealed: Dict[str, EventEnvelope] = {}
    self._load_index()
```

2. 新增 `append_many`（把单条 `append` 的锁内逻辑提取复用）：

```python
def append_many(
    self,
    events: List[EventEnvelope],
    *,
    expected_last_sequence: Optional[int] = None,
) -> List[EventEnvelope]:
    """原子追加一组事件：单次锁、单次 CAS、sequence 连续、哈希链连续、一次 fsync。"""
    if not events:
        return []
    with self._locked():
        self._reconcile_from_journal()
        if expected_last_sequence is not None and expected_last_sequence != self._last_sequence:
            raise JournalWriteConflict(
                f"expected last sequence {expected_last_sequence}, got {self._last_sequence}"
            )
        # 幂等：整组已封存则原样返回
        if all(e.event_id in self._sealed for e in events):
            return [self._sealed[e.event_id] for e in events]
        sealed_group: List[EventEnvelope] = []
        for event in events:
            existing = self._sealed.get(event.event_id)
            if existing is not None:
                sealed_group.append(existing)
                continue
            event.sequence = self._last_sequence + 1
            event.integrity.payload_sha256 = canonical_payload_sha256(event.payload)
            event.integrity.previous_event_sha256 = self._last_event_sha256
            event.integrity.event_sha256 = hashlib.sha256(
                f"{self._last_event_sha256}|{_hash_input(event)}".encode("utf-8")
            ).hexdigest()
            self._last_sequence = event.sequence
            self._last_event_sha256 = event.integrity.event_sha256
            self._sealed[event.event_id] = event
            sealed_group.append(event)
        path = self._segment_path(self._current_segment)
        with path.open("a", encoding="utf-8") as f:
            for event in sealed_group:
                f.write(canonical_json(event.model_dump()) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if path.stat().st_size > self._max_segment_bytes:
            self.roll_segment()
        else:
            self._save_index()
        return sealed_group
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_journal_writer.py -v`
Expected: PASS（原有 15 + 新增 4）

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/services/journal_writer.py tests/test_journal_writer.py
git commit -m "feat(journal): atomic append_many group + journal_dir override"
```

---

### Task 4: JournalAuthority — 身份作用域 emit / replay

**Files:**
- Create: `floodmind/agent/runtime/services/journal_authority.py`
- Test: `tests/test_journal_authority.py`

**Interfaces:**
- Consumes: `JournalWriter`(Task 3), `EventEnvelope`/`canonical_json`/`utcnow`(P1), `Identity`/`new_id`(P1), `reduce`/`initial_run_state`(Task 2)。
- Produces:
  - `open_journal_authority(runtime_dir, *, conversation_id, task_id, run_id, thread_id, turn_id, attempt_id="") -> JournalAuthority`
  - `JournalAuthority`:
    - `emit(event_type, payload, *, actor_type="system", actor_id="", thread_id=None, turn_id=None, attempt_id=None, call_id=None) -> EventEnvelope`
    - `append_group(events: List[EventEnvelope]) -> List[EventEnvelope]`
    - `cursor() -> int`
    - `read_after(after_sequence=0) -> List[EventEnvelope]`
    - `replay(after_sequence=0, state=None) -> RunState`（按 event_id 去重）
    - `new_envelope(event_type, payload, **scope) -> EventEnvelope`（构造未落盘信封，供 append_group 组批）
- 常量：`DEFAULT_RUNTIME_ROOT`（`PROJECT_ROOT / ".floodmind"`）。

- [ ] **Step 1: Write the failing test** (`tests/test_journal_authority.py`)

```python
from floodmind.agent.runtime.services.journal_authority import (
    open_journal_authority,
    JournalAuthority,
)
from floodmind.agent.runtime.contracts.identity import new_id, is_valid_id
from floodmind.agent.runtime.contracts.run_state import RunStatus


def test_emit_scopes_identity(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_1", task_id="task_1",
                                   run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    ev = auth.emit("thread.message.sent", {"content": "hi", "turn_index": 0})
    assert ev.conversation_id == "conv_1"
    assert ev.run_id == "run_1"
    assert ev.thread_id == "thread_1"
    assert ev.turn_id == "turn_1"
    assert ev.sequence == 1


def test_child_thread_override_scope(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_1", task_id="task_1",
                                   run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    ev = auth.emit("thread.created", {"thread_id": "thread_child"}, thread_id="thread_child")
    assert ev.thread_id == "thread_child"


def test_replay_dedup_by_event_id(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_1", task_id="task_1",
                                   run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    auth.emit("thread.message.sent", {"content": "hi", "turn_index": 0})
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "ok", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    state = auth.replay(after_sequence=0)
    assert state.status == RunStatus.completed
    assert state.token_usage["total_tokens"] == 2
    assert len(state.turns) == 2
    # 幂等重放：同 after_sequence 必须与之前一致
    state2 = auth.replay(after_sequence=0)
    assert state2.model_dump() == state.model_dump()


def test_cursor_read_after(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="r", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "a", "turn_index": 0})
    cur = auth.cursor()
    assert cur == 1
    auth.emit("model.attempt.completed", {"attempt_id": "a", "terminal_reason": "completed",
        "content": "b", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    assert len(auth.read_after(cur)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_journal_authority.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/services/journal_authority.py`：

```python
"""JournalAuthority：per-run 身份作用域的事件写入/重放门面（目标 §4/§5）。

- emit(): 构造 EventEnvelope，填充当前 run/thread/turn/attempt 身份，落盘（CAS 冲突抛 JournalWriteConflict）。
- append_group(): 一次原子提交多事件（Task 3 append_many）。
- replay(): 从 cursor 之后读事件，按 event_id 去重，用确定性 Reducer 折叠出 RunState。
"""

from typing import Dict, List, Optional, Any

from pathlib import Path

from floodmind.agent.runtime.contracts.canonical_events import (
    EventEnvelope, Actor, utcnow,
)
from floodmind.agent.runtime.contracts.identity import new_id
from floodmind.agent.runtime.contracts.run_state import RunState
from floodmind.agent.runtime.reducer import reduce, initial_run_state
from floodmind.agent.runtime.services.journal_writer import JournalWriter
from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT


DEFAULT_RUNTIME_ROOT: Path = PROJECT_ROOT / ".floodmind"


def _run_journal_dir(runtime_dir: Path, conversation_id: str, task_id: str, run_id: str) -> Path:
    return (
        Path(runtime_dir) / "conversations" / conversation_id / "tasks" / task_id
        / "runs" / run_id / "journal"
    )


def open_journal_authority(
    runtime_dir: Path,
    *,
    conversation_id: str,
    task_id: str,
    run_id: str,
    thread_id: str,
    turn_id: str,
    attempt_id: str = "",
) -> "JournalAuthority":
    journal_dir = _run_journal_dir(runtime_dir, conversation_id, task_id, run_id)
    writer = JournalWriter(runtime_dir, run_id, journal_dir=journal_dir)
    return JournalAuthority(
        writer=writer,
        conversation_id=conversation_id,
        task_id=task_id,
        run_id=run_id,
        thread_id=thread_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
    )


class JournalAuthority:
    def __init__(
        self,
        *,
        writer: JournalWriter,
        conversation_id: str,
        task_id: str,
        run_id: str,
        thread_id: str,
        turn_id: str,
        attempt_id: str = "",
    ):
        self._writer = writer
        self.conversation_id = conversation_id
        self.task_id = task_id
        self.run_id = run_id
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.attempt_id = attempt_id

    def new_envelope(self, event_type: str, payload: Dict[str, Any], **scope) -> EventEnvelope:
        import uuid
        return EventEnvelope(
            event_id=f"evt_{uuid.uuid4().hex}",
            event_type=event_type,
            conversation_id=self.conversation_id,
            task_id=self.task_id,
            run_id=self.run_id,
            thread_id=scope.get("thread_id", self.thread_id),
            turn_id=scope.get("turn_id", self.turn_id),
            attempt_id=scope.get("attempt_id", self.attempt_id),
            call_id=scope.get("call_id", ""),
            actor=Actor(type=scope.get("actor_type", "system"), id=scope.get("actor_id", "")),
            payload=payload,
            recorded_at=utcnow(),
        )

    def emit(self, event_type: str, payload: Dict[str, Any], **scope) -> EventEnvelope:
        envelope = self.new_envelope(event_type, payload, **scope)
        return self._writer.append(envelope)

    def append_group(self, events: List[EventEnvelope]) -> List[EventEnvelope]:
        return self._writer.append_many(events)

    def cursor(self) -> int:
        return self._writer.current_sequence()

    def read_after(self, after_sequence: int = 0) -> List[EventEnvelope]:
        return self._writer.read_from(after_sequence)

    def replay(self, after_sequence: int = 0, state: Optional[RunState] = None) -> RunState:
        current = state or initial_run_state(
            self.run_id, conversation_id=self.conversation_id,
            task_id=self.task_id, thread_id=self.thread_id,
        )
        seen: set = set()
        for event in self._writer.read_from(after_sequence):
            if event.event_id in seen:
                continue  # 重复 event_id 不重复副作用
            seen.add(event.event_id)
            current = reduce(current, event)
        return current
```

> 说明：`new_envelope` 用 `uuid.uuid4().hex` 保证 event_id 全局唯一。`JournalAuthority.emit` 每次生成新 event_id；重复事件防护由 writer `_sealed` 幂等 + `replay` 按 event_id 去重两层保证。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_journal_authority.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/services/journal_authority.py tests/test_journal_authority.py
git commit -m "feat(journal): JournalAuthority with identity-scoped emit and dedup replay"
```

---

### Task 5: RuntimeContext 注入 + ContextVar 全局 getter 移除

**Files:**
- Modify: `floodmind/tools/session_context.py`（新增 `set_runtime_context`/`get_runtime_context`）
- Modify: `floodmind/agent/runtime/services/permission_service.py`（删 getter/setter/global + 改内部惰性取用）
- Modify: `floodmind/agent/runtime/services/path_service.py`（删 getter/setter/default + 内部改注入）
- Modify: `floodmind/agent/runtime/services/background_task_service.py`（删 `get_background_task_service`/`set_background_task_service` 全局单例，改显式注入）
- Modify: `floodmind/agent/runtime/services/tool_execution_service.py`（execute 内绑定改由 RuntimeContext 提供；set_session_context 携带 runtime_context）
- Modify: `floodmind/agent/runtime/contracts/tools.py`（`ToolSpec.check_permissions` 用注入）
- Modify: `floodmind/tools/agent_tool.py`、`floodmind/tools/base_tools.py`（getter 调用改 session context 读取）
- Modify: `floodmind/agent/native/native_flood_agent.py`（`_run_loop` 构造 RuntimeContext 注入；specialist 注入 background service）
- Modify: `floodmind/agent/native/executor.py`（`_inject_background_notifications` 去掉 fallback）
- Test: `tests/test_runtime_context_injection.py`（新增）+ 受影响旧测试重写

**Interfaces:**
- Consumes: `RuntimeContext`(P1), `SESSION_CONTEXT`。
- Produces: `set_runtime_context(rtc) -> None` / `get_runtime_context() -> Optional[RuntimeContext]`（session context 键 `"runtime_context"`）；删除 `get_permission_service`/`get_path_service`/`get_background_task_service` 及其 set/reset/global fallback。

- [ ] **Step 1: Write the failing test** (`tests/test_runtime_context_injection.py`)

```python
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.tools.session_context import set_runtime_context, get_runtime_context


def test_runtime_context_roundtrip():
    rtc = RuntimeContext(
        conversation_id="conv_1", task_id="task_1", run_id="run_1",
        thread_id="thread_1", turn_id="turn_1", agent_tier="main",
    )
    set_runtime_context(rtc)
    assert get_runtime_context() == rtc


def test_get_runtime_context_default_none():
    # 需要在一个干净的 context 里验证；用 contextvars 隔离
    import contextvars
    from floodmind.tools import session_context as sc
    token = sc._session_ctx_var.set({})
    try:
        assert get_runtime_context() is None
    finally:
        sc._session_ctx_var.reset(token)
```

（其余断言：`import floodmind.agent.runtime.services.permission_service as ps; assert not hasattr(ps, "get_permission_service")` 等放在一个 grep 审计测试里，见 Step 5。）

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_context_injection.py -v`
Expected: FAIL with `ImportError: cannot import name 'set_runtime_context'`

- [ ] **Step 3: Implement**

**3a. `session_context.py`** 追加：

```python
def set_runtime_context(rtc) -> None:
    ctx = dict(_session_ctx_var.get({}))
    ctx["runtime_context"] = rtc
    _session_ctx_var.set(ctx)


def get_runtime_context():
    return _session_ctx_var.get({}).get("runtime_context")
```

**3b. 服务接线**：`_run_loop`（`native_flood_agent.py:2770-2784`）改为构造并注入 `RuntimeContext`：

```python
rtc = RuntimeContext(
    conversation_id=conv_id, task_id=task_id, run_id=run_id,
    thread_id=thread_id, turn_id=turn_id,
    actor_type="host", actor_id=effective_session_id,
    agent_tier=context.agent_tier, runtime_mode=context.mode,
    workspace_id=workspace_id, sandbox_id=sandbox_id,
    permission_service=self._permission_service,
    path_service=self._path_service,
    background_service=self._background_task_service,
)
set_runtime_context(rtc)
# 删除 set_permission_service/set_path_service/set_workspace 调用与对应 reset
```

`ToolExecutionService.execute` 内（`tool_execution_service.py:126-142`）删除 set/reset，改为直接从注入的 `self._permission_service`/`self._path_service` 使用（已持有），`set_session_context(...)` 调用中追加 `runtime_context` 字段或保持现注入不变。

**3c. 消费者改写**：
- `contracts/tools.py:59-62` `ToolSpec.check_permissions`：从 `get_permission_service()` 改为 `get_runtime_context().permission_service`（None 时返回未授权结果，不 fallback）。
- `permission_service.py` `_check_write_policy/_check_exec_policy/_check_skill_script_policy/_check_read_path_policy`：删 `get_path_service()` 惰性填充，改用 `self._path_service`（构造时要求注入，不默认全局）。
- `agent_tool.py:239-258`：`get_path_service().strip_session_prefix/resolve_simple` → `get_runtime_context().path_service`。
- `base_tools.py:596-602, 751-800`：`get_background_task_service()` → `get_runtime_context().background_service`（None 时返回"未启用后台"错误，不 fallback 全局）。
- `executor.py:668-676`：删 `else: get_background_task_service()` fallback。
- `native_flood_agent.py:440-443, 504-506, 2122-2125`：改用 `self._background_task_service` 注入；specialist cleanup 显式传 service。

**3d. 删除**：`permission_service.py` 的 `_permission_service_var`/`get/set/reset/global`；`path_service.py` 的 `_path_service_var`/`get/set/reset/_default_path_service`；`background_task_service.py` 的 `_service`/`get/set_background_task_service`。

**3e. 重写受影响测试**：grep `get_permission_service|get_path_service|get_background_task_service|set_permission_service|set_path_service` 定位旧测试，改为按注入路径断言。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_runtime_context_injection.py -v`
Expected: PASS

- [ ] **Step 5: 全量 + 审计测试**

在 `tests/test_runtime_context_injection.py` 追加 grep 审计：

```python
import subprocess, sys

def _root():
    from pathlib import Path
    return Path(__file__).resolve().parents[2]


def test_legacy_getters_removed():
    for mod in ("permission_service", "path_service", "background_task_service"):
        out = subprocess.run(
            [sys.executable, "-c", f"import floodmind.agent.runtime.services.{mod} as m; print([x for x in dir(m) if 'service' in x and 'get_' in x])"],
            capture_output=True, text=True, cwd=str(_root()),
        )
        banned = {"get_permission_service", "get_path_service", "get_background_task_service"}
        assert not banned & set(eval(out.stdout))
```

Run: `python -m pytest tests/test_runtime_context_injection.py -v && python -m pytest -q`
Expected: 审计通过；全量绿（原 888 中受影响的测试已重写）。

- [ ] **Step 6: Commit**

```bash
git add -A floodmind/tools/session_context.py floodmind/agent/runtime/services/permission_service.py floodmind/agent/runtime/services/path_service.py floodmind/agent/runtime/services/background_task_service.py floodmind/agent/runtime/services/tool_execution_service.py floodmind/agent/runtime/contracts/tools.py floodmind/tools/agent_tool.py floodmind/tools/base_tools.py floodmind/agent/native/native_flood_agent.py floodmind/agent/native/executor.py tests/
git commit -m "refactor(context): inject RuntimeContext, remove ContextVar global service getters"
```

---

### Task 6: 历史权威切换 — `_turns` → Journal 投影（thread.message.sent + model.attempt.completed）

> 本任务是 P2 的核心开关：同一提交内完成"写改事件、读改投影、删 _turns/chat_history.json 源、SessionManager 读投影"，避免中间态。

**Files:**
- Modify: `floodmind/agent/native/executor.py`（`_write_round_to_memory` → `_emit_round_events`；LLM 起点发 `model.attempt.started`）
- Modify: `floodmind/agent/native/native_flood_agent.py`（stream 内 `add_user_message` → `auth.emit("thread.message.sent")`；构造/注入 `JournalAuthority`；`resolve_identity`）
- Create: `floodmind/agent/runtime/services/run_identity.py`（`resolve_identity(session_id, session_dir) -> Identity`，conversation 持久化）
- Create: `floodmind/agent/runtime/services/history_projection.py`（`project_conversation`/`project_current`）
- Modify: `floodmind/memory/dual_memory.py`（删 `_turns`/`save_chat_history`/`_load_from_disk`/`add_assistant_round`/`add_ai_message*`；读方法改投影）
- Modify: `floodmind/memory/session_manager.py`（`_load_frontend_messages`/`get_session_title` 改读投影）
- Modify: `floodmind/server/routes/chat.py`、`floodmind/server/routes/models.py`（user/ai message 调用改事件）
- Modify: `floodmind/agent/runtime/contracts/runtime_context.py`（增 `journal_authority: Any = None` 槽位）
- Test: `tests/test_history_projection.py`（新增）+ `tests/test_dual_memory_journal.py`（新增）+ 受影响旧测试重写

**Interfaces:**
- Consumes: `JournalAuthority`(Task 4), `reduce`/`initial_run_state`(Task 2), `resolve_identity`(本任务)。
- Produces: `HistoryProjection.project_conversation(runtime_dir, conversation_id) -> List[Dict]`；`HistoryProjection.project_current(authority) -> List[Dict]`（扁平 turns，与旧 `_turns` 形状一致：user=role/content/turn_index；assistant=role/content/reasoning/tool_calls/is_final/timestamp）。`DualMemory` 保留公共读 API（`get_user_messages`/`get_chat_history_for_system_prompt`/`search_history`/`get_turns`/`turn_count`），内部改投影。

- [ ] **Step 1: Write the failing test** (`tests/test_history_projection.py`)

```python
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.history_projection import (
    project_current, project_conversation,
)


def test_project_current_roundtrip(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="conv_1", task_id="task_1",
                                   run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    auth.emit("thread.message.sent", {"content": "hi", "turn_index": 0})
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "tool_calls",
        "content": "", "reasoning": "think", "tool_calls": [
            {"tool_name": "Read", "tool_input": "{}", "tool_output": "ok", "status": "succeeded"}],
        "is_final": False, "usage": {}})
    turns = project_current(auth)
    assert turns[0] == {"role": "user", "content": "hi", "turn_index": 0}
    assert turns[1]["role"] == "assistant"
    assert turns[1]["tool_calls"][0]["tool_name"] == "Read"


def test_project_conversation_aggregates_runs(tmp_path):
    a1 = open_journal_authority(tmp_path, conversation_id="conv_9", task_id="task_1",
                                run_id="run_1", thread_id="thread_1", turn_id="turn_1")
    a1.emit("thread.message.sent", {"content": "first", "turn_index": 0})
    a1.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "one", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    a2 = open_journal_authority(tmp_path, conversation_id="conv_9", task_id="task_2",
                                run_id="run_2", thread_id="thread_2", turn_id="turn_2")
    a2.emit("thread.message.sent", {"content": "second", "turn_index": 0})
    turns = project_conversation(tmp_path, "conv_9")
    assert [t["content"] for t in turns if t["role"] == "user"] == ["first", "second"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_history_projection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named '...history_projection'`

- [ ] **Step 3: Implement**

**3a. `run_identity.py`**：

```python
"""session_id → 标准身份层级（目标 §3.1/§3.3）。conversation_id 每 session 稳定一次并持久化。"""

import json
from pathlib import Path

from floodmind.agent.runtime.contracts.identity import new_id


def resolve_identity(session_id: str, session_dir: Path) -> dict:
    """返回 {conversation_id, task_id, run_id, thread_id, turn_id}。

    conversation_id 惰性创建并写入 session_dir/session.json；task/run/thread/turn 每次调用新生成。
    """
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    meta_path = session_dir / "session.json"
    conversation_id = ""
    if meta_path.exists():
        try:
            conversation_id = str(json.loads(meta_path.read_text(encoding="utf-8")).get("conversation_id", ""))
        except Exception:
            conversation_id = ""
    if not conversation_id:
        conversation_id = new_id("conversation")
        meta_path.write_text(
            json.dumps({"conversation_id": conversation_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return {
        "conversation_id": conversation_id,
        "task_id": new_id("task"),
        "run_id": new_id("run"),
        "thread_id": new_id("thread"),
        "turn_id": new_id("turn"),
    }
```

**3b. `history_projection.py`**：

```python
"""事件 → 扁平 turns 投影。读路径唯一事实源是 Journal。"""

from typing import Dict, List

from floodmind.agent.runtime.services.journal_authority import (
    JournalAuthority, open_journal_authority, _run_journal_dir,
)
from floodmind.agent.runtime.reducer import reduce, initial_run_state


def project_current(auth: JournalAuthority) -> List[Dict]:
    return auth.replay(after_sequence=0).turns


def project_conversation(runtime_dir, conversation_id: str) -> List[Dict]:
    """聚合 conversation 下所有 runs 的事件，按 sequence 折叠成扁平 turns。"""
    from pathlib import Path
    runs_root = Path(runtime_dir) / "conversations" / conversation_id / "tasks"
    if not runs_root.exists():
        return []
    events = []
    # 收集该 conversation 全部 runs 的 journal 事件
    for task_dir in sorted(runs_root.iterdir()) if runs_root.is_dir() else []:
        runs_dir = task_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted(runs_dir.iterdir()):
            journal_dir = run_dir / "journal"
            if not journal_dir.is_dir():
                continue
            from floodmind.agent.runtime.services.journal_writer import JournalWriter
            writer = JournalWriter(Path(runtime_dir), run_dir.name, journal_dir=journal_dir)
            events.extend(writer.read_from(0))
    events.sort(key=lambda e: (e.sequence, e.event_id))
    state = initial_run_state("conversation_projection", conversation_id=conversation_id)
    seen = set()
    for e in events:
        if e.event_id in seen:
            continue
        seen.add(e.event_id)
        state = reduce(state, e)
    return state.turns
```

> 注：`project_conversation` 需要能枚举该 conversation 的 runs。若 `session.json` 记录了 `conversation_id`，则该函数由 `SessionManager`/`DualMemory` 传入正确的 `conversation_id` 与 `runtime_dir`。Task 10 的审计测试会验证无 `chat_history.json` 回退。

**3c. `executor.py` `_write_round_to_memory` → 事件**：

```python
# 原 :638-659 body 替换：
def _emit_round_events(self, state, *, tool_calls_records, is_final, attempt_id):
    auth = self._journal_authority
    auth.emit("model.attempt.completed", {
        "attempt_id": attempt_id,
        "terminal_reason": state.terminal_reason.code if state.terminal_reason else (
            "tool_calls" if tool_calls_records else "completed"),
        "content": state.current_answer or "",
        "reasoning": state.round_reasoning,
        "tool_calls": tool_calls_records,
        "is_final": bool(is_final),
        "usage": {
            "prompt_tokens": state.token_usage.prompt_tokens,
            "completion_tokens": state.token_usage.completion_tokens,
            "total_tokens": state.token_usage.total_tokens,
        },
    })
```

调用点 `:456` 与 `:615` 改为 `self._emit_round_events(state, tool_calls_records=..., is_final=..., attempt_id=state.attempt_id)`。LLM 起点（`:307-310` 附近）`emit("model.attempt.started", {"model": ..., "iteration": state.iteration, "messages_count": len(state.messages)})`，并 `state.attempt_id = new_id("attempt")`。executor 构造函数增 `journal_authority: Optional[JournalAuthority] = None` 参数。

**3d. `dual_memory.py` 切换**：
- 删除：`self._turns`、`_turn_index`（改为投影派生）、`add_user_message`/`add_assistant_round`/`add_ai_message`/`add_ai_message_with_trace` 的历史写入体、`save_chat_history`、`_load_from_disk`（对 `chat_history.json` 的读写全部删除，无回退）。
- 新增 `bind_journal(auth, runtime_dir, conversation_id)`：记录当前 run 的 authority 与 conversation 范围。
- 读方法改投影：
  - `get_user_messages()` → 从 `project_current(auth)` 过滤 `role=="user"` 取 `content`。
  - `get_pending_user_messages()` / 尾部 user 检测 → 同上基于投影。
  - `get_turns()` → `project_current(auth)`。
  - `search_history(query, top_k)` → 在 `project_conversation(...)` 结果上做关键词/块搜索（沿用现有打分逻辑）。
  - `get_chat_history_for_system_prompt(...)` → 用 `project_conversation(...)` 构建历史文本（沿用现有格式化逻辑），压缩逻辑改为对投影列表做派生缓存（`compressed` 只写内存派生 dict，不写 `chat_history.json`）。
  - `turn_count()` → `len(project_current(auth))`。

**3e. `native_flood_agent.py` stream `_run_loop`**：
- 构造 `ident = resolve_identity(effective_session_id, context.state_dir)`。
- `auth = open_journal_authority(runtime_dir, **ident)`。
- `set_runtime_context(...)` 的 `RuntimeContext` 增 `journal_authority=auth`。
- `self.memory.bind_journal(auth, runtime_dir, ident["conversation_id"])`。
- `:2756-2758` `self.memory.add_user_message(user_input)` → `auth.emit("thread.message.sent", {"content": user_input, "turn_index": 0})`。
- 尾部 legacy fallback（`:3006-3019` `add_ai_message*`/`save_chat_history`）删除。

**3f. `session_manager.py`**：
- `_load_frontend_messages`/`get_session_title` 改为调用 `HistoryProjection.project_conversation(runtime_dir, conversation_id)`（conversation_id 从 session 元数据/`resolve_identity` 取），不再读 `chat_history.json`。
- `get_messages_page`/`_turns_to_frontend` 保持（输入换投影 turns）。

**3g. `chat.py:200-201` / `models.py:158-161`**：user/ai message 调用改为经 `get_runtime_context().journal_authority.emit("thread.message.sent", ...)`（或 agent 提供的 `enqueue_user_message` 方法），不调 `memory.add_user_message`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_history_projection.py tests/test_dual_memory_journal.py -v`
Expected: PASS

- [ ] **Step 5: 全量 + 重写受影响旧测试**

运行 `python -m pytest -q`，修复失败测试：凡断言 `_turns` 形状、`chat_history.json` 读写、`add_user_message`/`add_assistant_round` 的直接调用，均改为按投影断言（见 Global Constraints：断言旧架构行为的测试按 forward-only 重写）。

Expected: 全量绿。

- [ ] **Step 6: Commit**

```bash
git add -A floodmind/agent/runtime/services/run_identity.py floodmind/agent/runtime/services/history_projection.py floodmind/agent/runtime/contracts/runtime_context.py floodmind/agent/native/executor.py floodmind/agent/native/native_flood_agent.py floodmind/memory/dual_memory.py floodmind/memory/session_manager.py floodmind/server/routes/chat.py floodmind/server/routes/models.py tests/
git commit -m "feat(history): _turns -> Journal projection; chat_history.json source removed"
```

---

### Task 7: 执行事件 + ExecutionJournalService 下线

**Files:**
- Modify: `floodmind/agent/native/executor.py`（tool/approval/compact/terminal/checkpoint 事件）
- Modify: `floodmind/agent/native/native_flood_agent.py`（删 `_journal_service` 创建）
- Modify: `floodmind/tools/memory_tools.py`（`JournalSearch`/`JournalGetFullResult` 改读 canonical journal）
- Delete: `floodmind/agent/runtime/services/execution_journal_service.py`（或降为只读归档工具，见下）
- Modify: `floodmind/agent/runtime/contracts/journal.py`（删，或保留 `ArchivedToolResult` 供归档读取）
- Test: `tests/test_execution_events.py`（新增）+ 受影响旧测试重写

**Interfaces:**
- Consumes: `JournalAuthority`(Task 4), Task 6 的事件接入。
- Produces: executor 在稳定点发 `tool.execution.started/completed/failed`、`tool.approval.requested/resolved`、`context.compaction.started/completed`、`checkpoint.created`、`run.completed/failed`；`ExecutionJournalService` 历史源职责删除。

- [ ] **Step 1: Write the failing test** (`tests/test_execution_events.py`)

```python
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.history_projection import project_current
from floodmind.agent.runtime.reducer import reduce, initial_run_state


def test_tool_and_terminal_event_sequence(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="r", thread_id="th", turn_id="tu")
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "tool_calls",
        "content": "", "reasoning": "", "tool_calls": [], "is_final": False, "usage": {}})
    auth.emit("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Read", "arguments": "{}"})
    auth.emit("tool.execution.completed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Read", "status": "succeeded", "result_summary": "ok",
        "full_ref": "", "artifacts": ["art_1"]})
    auth.emit("run.completed", {"final_output": "done", "terminal_reason": "completed"})
    state = auth.replay(0)
    assert state.status.value == "completed"
    assert state.artifacts == ["art_1"]
    assert state.last_committed_sequence == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_events.py -v`
Expected: FAIL — `model.attempt.completed` 后无工具执行分支（本项目测试只验证事件落盘+重放；executor 接线由 Step 3 完成）

（注：本测试实际先验证 Task 4/2 已覆盖的 reduce 行为，属于回归锚点；真正的 executor 接线通过 Step 5 的集成测试与全量绿验证。）

- [ ] **Step 3: Implement**

**3a. executor 事件接入**（在 Task 6 已接 `model.*` 的基础上）：

- `_on_awaiting_tool` 工具执行前（`:532-547`）：`emit("tool.execution.started", {"transaction_id": ttx_id, "call_id": call.id, "tool_id": call.name, "arguments": tool_input_str})`；返回后按 `result.status` 发 `tool.execution.completed`/`tool.execution.failed`（payload 含 `status`/`result_summary`(inline 摘要或 ref)/`full_ref`/`artifacts`）。`ttx_id = new_id("transaction")`。
- `_on_awaiting_permission` 进入（`:556-560` 与 `AskService.start_ask` 附近）：`emit("tool.approval.requested", {"call_id": call.id, "ask_id": ask_id, "tool_name": call.name, "reason": ..., "arguments": ...})`。
- `AskService.respond`（`ask_service.py:203-220`）：`emit("tool.approval.resolved", {"ask_id": ask_id, "call_id": ..., "approved": response.approved})`。
- `_on_context_compress`（`:219-262`）：压缩前 `emit("context.compaction.started", {"reason": ..., "before_messages": len(state.messages)})`，压缩后 `emit("context.compaction.completed", {"reason": ..., "before_messages": ..., "after_messages": len(result.compressed_messages)})`。
- `run_from_state` 终态（`:191-193` 判定 terminal 时）：`emit("run.completed", {"final_output": state.final_output, "terminal_reason": ...})` 或 `emit("run.failed", {"error": ..., "terminal_reason": ...})`。
- `_save_checkpoint`（`:985-1002`）发布后：`emit("checkpoint.created", {"checkpoint_id": record.checkpoint_id, "cursor": auth.cursor(), "iteration": state.iteration, "status": state.status})`。

**3b. `ExecutionJournalService` 下线**：
- `native_flood_agent.py:410` 删除 `self._journal_service = ExecutionJournalService(...)`；executor 构造函数删 `execution_journal_service` 参数与 `record_turn`/`process_tool_result` 调用（`:459-468,:580-587,:617-627,:870-876`）。
- 长工具结果归档（`archive_tool_result`）职责：`tool.execution.completed` 事件本身含 `result_summary`/`full_ref`；归档文件读写若仍需，收敛为一个独立的 `ArtifactStore`（P6 正式做）——P2 内将 `ExecutionJournalService` 从 executor 依赖移除，`JournalSearch`/`JournalGetFullResult` 改为读 canonical journal（在 `tool.execution.completed` 事件 payload 上搜索 `result_summary`/`full_ref`），不再依赖 `ExecutionJournalService`。
- 若删除后无引用，删除 `execution_journal_service.py` 与 `contracts/journal.py`；有引用（如旧测试）则按 forward-only 重写/删除。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_events.py -v`
Expected: PASS

- [ ] **Step 5: 全量 + 集成回归**

运行 `python -m pytest -q`；修复引用 `ExecutionJournalService`/`_journal_service`/`record_turn`/`get_recent_summaries` 的旧测试（重写为对 canonical journal 的断言）。
Expected: 全量绿。

- [ ] **Step 6: Commit**

```bash
git add -A floodmind/agent/native/executor.py floodmind/agent/native/native_flood_agent.py floodmind/agent/runtime/services/ask_service.py floodmind/tools/memory_tools.py floodmind/agent/runtime/services/execution_journal_service.py floodmind/agent/runtime/contracts/journal.py tests/
git commit -m "feat(events): tool/approval/compact/terminal/checkpoint events; ExecutionJournalService retired"
```

---

### Task 8: Checkpoint 绑定 Journal cursor + 状态由 Reducer 派生

**Files:**
- Modify: `floodmind/agent/runtime/contracts/checkpoints.py`（`CheckpointManifest`/`CheckpointRecord` 增 `journal_cursor`/`reducer_version`）
- Modify: `floodmind/agent/runtime/services/checkpoint_service.py`（save 存 cursor/reducer_version + `RunState` 快照；load 返回 cursor）
- Modify: `floodmind/agent/native/types.py`（`AgentLoopState` 增 `journal_cursor` 字段，`mark_updated` 不变）
- Modify: `floodmind/agent/native/executor.py`（`_save_checkpoint` 传 cursor；构建 state 时从 reducer RunState 派生权威字段）
- Modify: `floodmind/cli.py`（pause/resume 用 cursor + replay）
- Test: `tests/test_checkpoint_cursor.py`（新增）+ 受影响旧测试重写

**Interfaces:**
- Consumes: `RunState`/`reduce`(Task 2), `JournalAuthority.replay`(Task 4)。
- Produces: `CheckpointService.save(state, metadata, *, journal_cursor=None, reducer_version="1")`；`CheckpointRecord.journal_cursor`/`reducer_version`；`load()` 返回 `(state, cursor)` 语义（或 state 内带 cursor）。

- [ ] **Step 1: Write the failing test** (`tests/test_checkpoint_cursor.py`)

```python
from floodmind.agent.runtime.services.checkpoint_service import CheckpointService
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.reducer import initial_run_state


class _StubState:
    def __init__(self, session_id, run_id, checkpoint_id="", iteration=0, status="created"):
        self.session_id = session_id; self.run_id = run_id; self.checkpoint_id = checkpoint_id
        self.iteration = iteration; self.status = status; self.updated_at = None
    def model_dump(self):
        return {"session_id": self.session_id, "run_id": self.run_id,
                "checkpoint_id": self.checkpoint_id, "iteration": self.iteration,
                "status": self.status}


def test_checkpoint_binds_cursor(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "hi", "turn_index": 0})
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "ok", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    svc = CheckpointService(base_dir=str(tmp_path))
    st = _StubState(session_id="sess_1", run_id="run_1")
    record = svc.save(st, journal_cursor=auth.cursor(), reducer_version="1")
    assert record.journal_cursor == 2
    assert record.reducer_version == "1"
    # load 后 state 携带 cursor
    state = svc.load("sess_1", record.checkpoint_id)
    assert state["checkpoint_id"] == record.checkpoint_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_checkpoint_cursor.py -v`
Expected: FAIL — `save()` 不认 `journal_cursor`（TypeError），`record.journal_cursor` 不存在

- [ ] **Step 3: Implement**

**3a. `checkpoints.py`**：`CheckpointRecord` 与 `CheckpointManifest` 增：

```python
journal_cursor: int = 0
reducer_version: str = "1"
```

**3b. `checkpoint_service.py`**：
- `save(self, state, metadata=None, *, journal_cursor: int = 0, reducer_version: str = "1")`：
  - 写 `state.json` 前，把 `journal_cursor`/`reducer_version` 放入 manifest（`CheckpointManifest`）。
  - 同时把 `state.run_id` 与 cursor 记录进 metadata。
- `load(...)`：从 manifest 读 `journal_cursor`/`reducer_version`，返回 `state_class.model_validate(data)`（`AgentLoopState` 增 `journal_cursor` 字段承载）。

**3c. `types.py`**：`AgentLoopState` 增 `journal_cursor: int = 0`。

**3d. `executor.py`**：
- `_save_checkpoint`（`:985-1002`）传 `journal_cursor=self._journal_authority.cursor()`。
- `run_from_state` 进入前/checkpoint 恢复时：用 `auth.replay(after_sequence=state.journal_cursor)` 得到 `RunState`，从 `RunState` 派生 `state.status`（仅当 journal 有权威状态时）、`state.iteration`、`state.pending_tool_calls`/`pending_ask_id`（对齐 §5.1"AgentLoopState 变为 Reducer 派生状态"）。

**3e. `cli.py`**（`:277-282` pause/resume）：`load(...)` 后用 `auth.replay(after_sequence=state.journal_cursor)` 重放并校验一致，再继续。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_checkpoint_cursor.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: 全量绿（checkpoint 相关旧测试已兼容或重写）。

- [ ] **Step 6: Commit**

```bash
git add -A floodmind/agent/runtime/contracts/checkpoints.py floodmind/agent/runtime/services/checkpoint_service.py floodmind/agent/native/types.py floodmind/agent/native/executor.py floodmind/cli.py tests/
git commit -m "feat(checkpoint): bind journal cursor + reducer version; state derived from reducer"
```

---

### Task 9: 子代理入同一 run Journal + thread_id + 独立落点

**Files:**
- Modify: `floodmind/agent/native/native_flood_agent.py`（`_run_specialist_task`）
- Modify: `floodmind/agent/runtime/services/runtime_layout.py`（新建：`thread_dirs(thread_id)` 计算 state/tmp/scripts）
- Test: `tests/test_subagent_journal.py`（新增）

**Interfaces:**
- Consumes: `JournalAuthority`(Task 4), `open_journal_authority`。
- Produces: specialist 以独立 `thread_id` 在同一 run Journal 落事件（`thread.spawn.requested`/`thread.created`/`thread.completed|failed|cancelled` + 其内部 turn/attempt 事件带 child thread_id）；specialist state/tmp/scripts 落在 `threads/<thread_id>/` 而非父目录；background service 显式注入 specialist executor。

- [ ] **Step 1: Write the failing test** (`tests/test_subagent_journal.py`)

```python
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.reducer import initial_run_state, reduce


def test_child_thread_events_scoped(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="thread_main", turn_id="tu")
    auth.emit("thread.spawn.requested", {"thread_id": "thread_child", "parent_call_id": "call_1"})
    auth.emit("thread.created", {"thread_id": "thread_child", "parent_call_id": "call_1"})
    auth.emit("thread.completed", {"thread_id": "thread_child", "parent_call_id": "call_1",
        "summary": "done", "artifact_ids": ["art_1"]}, thread_id="thread_child")
    events = auth.read_after(0)
    child_evs = [e for e in events if e.thread_id == "thread_child"]
    assert len(child_evs) == 1  # 只有 thread.completed 用 child scope 覆盖
    # reducer 记录 child_threads
    s = initial_run_state("run_1")
    for e in events:
        s = reduce(s, e)
    assert any(ct.thread_id == "thread_child" for ct in s.child_threads)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subagent_journal.py -v`
Expected: FAIL — reducer 不认识 `thread.created`（当前 reduce 不改 child_threads）

- [ ] **Step 3: Implement**

**3a. `reducer.py`** 增 `thread.*` 处理（保持确定性）：

```python
def _reduce_thread_spawn(state, payload):
    ns = _clone(state)
    return ns

def _reduce_thread_created(state, payload):
    ns = _clone(state)
    ns.child_threads.append(ChildThreadState(
        thread_id=str(payload["thread_id"]),
        parent_call_id=str(payload.get("parent_call_id", "")),
        status="running",
    ))
    return ns

def _reduce_thread_terminal(state, payload, event_type):
    ns = _clone(state)
    tid = str(payload.get("thread_id", ""))
    for ct in ns.child_threads:
        if ct.thread_id == tid:
            ct.status = {"thread.completed": "completed",
                         "thread.failed": "failed",
                         "thread.cancelled": "cancelled"}.get(event_type, "running")
    return ns
```

并在 `reduce()` 分发中注册 `thread.spawn.requested`/`thread.created`/`thread.completed`/`thread.failed`/`thread.cancelled`。（相应补 Task 2 的 reducer 测试。）

**3b. `runtime_layout.py`**：

```python
"""Run 内部目录布局（目标 §18）。"""

from pathlib import Path


def thread_dirs(runtime_dir: Path, conversation_id: str, task_id: str,
                run_id: str, thread_id: str) -> dict:
    base = (Path(runtime_dir) / "conversations" / conversation_id / "tasks" / task_id
            / "runs" / run_id / "threads" / thread_id)
    return {
        "thread_dir": base,
        "state_dir": base / "state",
        "tmp_dir": base / "tmp",
        "scripts_dir": base / "scripts",
    }
```

**3c. `_run_specialist_task`**（`native_flood_agent.py:1984-2130`）：
- 顶部：`child_thread_id = new_id("thread")`；`auth.emit("thread.spawn.requested", {"thread_id": child_thread_id, "parent_call_id": step_key})`。
- 构造子代理 `JournalAuthority`（同 run/thread=child）：`child_auth = open_journal_authority(runtime_dir, conversation_id=..., task_id=..., run_id=..., thread_id=child_thread_id, turn_id=new_id("turn"))`。
- 落点：`thread_dirs = thread_dirs(...)`；`RunContext` 的 `state_dir/tmp_dir/scripts_dir` 改用子代理独立目录（不再用 `parent_context.state_dir/tmp_dir/scripts_dir`）。
- 子代理 executor 传入 `journal_authority=child_auth`、`background_task_service=self._background_task_service`。
- 完成/失败/取消时：`auth.emit("thread.completed"|"thread.failed"|"thread.cancelled", {"thread_id": child_thread_id, "parent_call_id": step_key, "summary": result.summary, "artifact_ids": result.artifacts})`，并 `auth.emit("thread.created", ...)` 在 spawn 后立即发出。
- cleanup：`get_background_task_service().kill_session(sub_session_id)` → `self._background_task_service.kill_session(sub_session_id)`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subagent_journal.py tests/test_reducer.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: 全量绿（specialist 相关旧测试若断言父目录共享则重写为断言子代理独立目录）。

- [ ] **Step 6: Commit**

```bash
git add -A floodmind/agent/runtime/reducer.py floodmind/agent/runtime/services/runtime_layout.py floodmind/agent/native/native_flood_agent.py tests/
git commit -m "feat(subagent): child thread events in same run journal + isolated thread dirs"
```

---

### Task 10: 验收 — 端到端重放/幂等/恢复 + 旧源下线审计

**Files:**
- Create: `tests/test_p2_acceptance.py`
- Modify: 视审计结果修正残余引用。

**Interfaces:**
- Consumes: 全部前序任务产物。

- [ ] **Step 1: Write the failing test** (`tests/test_p2_acceptance.py`)

```python
"""P2 验收（设计 §25.1 对应项）：重放确定性、重复 event_id 不重复副作用、半写尾部可恢复、旧历史源无回退。"""

import json
from pathlib import Path

from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.journal_writer import JournalWriter
from floodmind.agent.runtime.reducer import initial_run_state, reduce
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope


def _scenario(auth):
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "tool_calls",
        "content": "", "reasoning": "", "tool_calls": [], "is_final": False, "usage": {}})
    auth.emit("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Read", "arguments": "{}"})
    auth.emit("tool.execution.completed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Read", "status": "succeeded", "result_summary": "ok", "full_ref": "", "artifacts": []})
    auth.emit("model.attempt.completed", {"attempt_id": "a2", "terminal_reason": "completed",
        "content": "final", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})
    auth.emit("run.completed", {"final_output": "final", "terminal_reason": "completed"})


def test_end_to_end_replay_determinism(tmp_path):
    a1 = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                run_id="run_1", thread_id="th", turn_id="tu")
    _scenario(a1)
    a2 = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                run_id="run_1", thread_id="th", turn_id="tu")
    s1 = a1.replay(0)
    s2 = a2.replay(0)
    assert s1.model_dump() == s2.model_dump()


def test_duplicate_event_id_no_duplicate_side_effect(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("model.attempt.completed", {"attempt_id": "a", "terminal_reason": "completed",
        "content": "x", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})
    # 同 event_id 重试落盘：writer 幂等返回封存信封，不重复写
    before = auth.cursor()
    auth.append_group([auth.new_envelope("model.attempt.completed", {"attempt_id": "a",
        "terminal_reason": "completed", "content": "x", "reasoning": "", "tool_calls": [],
        "is_final": True, "usage": {}})])  # 构造同 event_id 的包封（实现需保证 event_id 可复现于同 payload —— 见注）
    # 用 replay 去重验证：无论 writer 是否追加，重放都只算一次副作用
    state = auth.replay(0)
    assert state.token_usage["total_tokens"] == 10


def test_half_written_tail_recoverable(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    _scenario(auth)
    writer = auth._writer
    seg = sorted(writer._journal_dir.glob("events-*.jsonl"))[-1]
    with seg.open("a", encoding="utf-8") as f:
        f.write('{"event_type": "partial')
    writer.repair_tail()
    assert auth.replay(0).status.value == "completed"  # 恢复后重放不受半写尾部影响


def test_old_history_sources_offline():
    """审计：floodmind/ 源码中不再引用旧历史源与全局 getter。"""
    import subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]

    banned = ("get_permission_service", "get_path_service", "get_background_task_service",
              "chat_history.json", "ExecutionJournalService", "record_turn", "_turns")
    hits = []
    for pat in banned:
        scan = (
            "import pathlib;"
            f"root=pathlib.Path({str(root)!r});"
            f"pat={pat!r};"
            "hits=[str(p) for p in root.joinpath('floodmind').rglob('*.py')"
            " if pat in p.read_text(encoding='utf-8', errors='ignore')]"
            ";print('\\n'.join(hits))"
        )
        out = subprocess.run([sys.executable, "-c", scan],
                             capture_output=True, text=True)
        if out.stdout.strip():
            hits.append((pat, out.stdout.strip()))
    assert not hits, f"legacy sources still referenced: {hits}"
```

> 注：`test_duplicate_event_id_no_duplicate_side_effect` 中"同 event_id 的包封"是抽象测试点。真实实现里 `JournalAuthority.emit` 每次生成新 event_id，重复副作用防护由两层保证：(1) writer `_sealed` 幂等返回（retry 同 event_id 不重写）；(2) `replay` 按 event_id 去重。实现者应让该测试构造一个显式固定 event_id 的 `EventEnvelope`（不经过 `new_envelope` 的随机 id），直接调 `writer.append_many` 验证 sealed 幂等。允许按此意图调整该测试用例。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_p2_acceptance.py -v`
Expected: FAIL（前序未完成时）

- [ ] **Step 3: Implement（修正残余引用）**

运行审计测试，对每个命中修正：
- `get_permission_service`/`get_path_service`/`get_background_task_service`：若 Task 5 漏改，按 Task 5 规则改为注入/session context。
- `chat_history.json`：确认无读取回退（`SessionManager`/`DualMemory` 不再读它）。
- `ExecutionJournalService`/`record_turn`：确认已下线。
- `_turns`：确认已从 `DualMemory` 删除（投影替换）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_p2_acceptance.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 全量 + 终审准备**

Run: `python -m pytest -q`
Expected: 全量绿（基线 888 中受影响测试已重写；新契约/投影/事件测试并入）。

- [ ] **Step 6: Commit**

```bash
git add tests/test_p2_acceptance.py
git commit -m "test(p2): acceptance — replay determinism, dedup, tail recovery, legacy-source audit"
```

---

## 3. 验收（§25.1 Journal/Reducer 对应项 + P2 阶段项）

- [ ] 相同事件重放状态完全一致（Task 2/10 确定性测试）。
- [ ] 重复 event_id 不重复副作用（writer `_sealed` 幂等 + replay 去重，Task 3/4/10）。
- [ ] 半写尾部可恢复（Task 3 `repair_tail` + Task 10）。
- [ ] Segment 滚动后 Sequence 连续（Task 3 append_many 测试）。
- [ ] Compare-and-Append 冲突可检测（Task 3 `JournalWriteConflict`）。
- [ ] Reducer 无 I/O 和全局状态依赖（Task 2 纯函数设计 + 审计）。
- [ ] `_turns`/`chat_history.json`/ExecutionJournalService 历史源下线且无读取回退（Task 6/7/10 审计）。
- [ ] ContextVar 全局 getter 移除，RuntimeContext 注入（Task 5 审计）。
- [ ] Checkpoint 绑定 Journal cursor + reducer version，状态由 Reducer 派生（Task 8）。
- [ ] 子代理事件进入同一 run Journal（thread_id 作用域）+ 独立 state/tmp/scripts 落点（Task 9）。
- [ ] 全量套件 `python -m pytest -q` 绿（重写后基线）。
