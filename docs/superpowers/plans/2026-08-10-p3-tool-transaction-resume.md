# P3 — Tool Transaction / Resume 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工具执行升级为完整 Tool Transaction（§6.1 契约 + §6.3 Approval Fingerprint + §6.4 终态机 + §6.5 幂等/indeterminate），并重开真正的 Checkpoint Resume（§16.4：cursor + Reducer Replay + Reconciliation），通过 §25.4/§25.6 验收。

**Architecture:** 三段式——(1) 纯数据层：`ToolTransaction` 契约 + `canonical_arguments`/`arguments_sha256` + `approval_fingerprint` 纯函数；(2) Reducer 层：完整工具终态机（proposed→…→result_committed）落进 `RunState.pending_tool_transactions`（升级为 ToolTransaction），indeterminate 保留 pending 供 reconcile；(3) 服务层：ToolExecutionService 发全生命周期事件 + 幂等键 + timeout→indeterminate，`ReconciliationService` 处理 pending/indeterminate，`ResumeService` 重开公开 resume（fencing + replay + reconcile + resume.started/completed）。

**Tech Stack:** Python 3.x, Pydantic v2 (`BaseModel`), `canonical_events.canonical_json`, P2 `JournalAuthority`/`reducer`/`CheckpointService`/`identity.new_id`。无第三方新增依赖。

## Global Constraints

> 每项任务的隐含要求包含本节；实现者必须逐字遵守。

- **只向前，不向后兼容、不 fallback**：不新增/不保留旧接口适配器、降级分支、双写路径。遇到"旧接口还要不要支持"一律否，除非计划明确要求。
- **单一事实源**：Canonical Event Journal 是唯一运行事实源；Tool Transaction 状态、批准记录、幂等结果均可从 Journal 重建，不允许双写。
- **Reducer 确定性（§2.8）**：`reduce(state, event)` 不得发网络请求、执行工具、读当前时间、生成随机 ID、依赖进程级可变单例、读取事件/Snapshot 之外的环境状态。
- **身份层级（§3.1）**：`conversation_id`/`task_id`/`run_id`/`thread_id`/`turn_id`/`attempt_id`/`call_id`/`transaction_id`/`artifact_id`/`checkpoint_id` 一律用 `floodmind.agent.runtime.contracts.identity.new_id(kind)`；禁止 `f"run-{int(time.time())}"` 之类临时拼装。
- **Canonical codec**：参数规范用 `canonical_json`（`ensure_ascii=False, sort_keys=True, separators=(",", ":")`）；`arguments_sha256` 与 `approval_fingerprint` 均基于该 canonical 形态。
- **幂等**：重复 `event_id` 不得重复副作用；重复 `idempotency_key` 不得重复执行已确认副作用。
- **模块化**：按模块/契约落实现（[[modular-code-principle]]），不打补丁式修改；新代码放对目录。
- **回归**：全量 `python -m pytest -q`（当前基线 **995 passed, 1 skipped**）。每任务全绿后再提交。
- **WIP 不碰**：仓库有 100+ 未提交的用户 WIP 文件（`git status` 可见）不得修改；提交用显式 `git add <paths>`，绝不用 `git add -A`。
- **确定性来源**：`approval_fingerprint`/`arguments_sha256`/`idempotency_key` 必须只依赖传入参数（含显式 `policy_version`），禁止读环境/时间/全局态。

---

## 0. P3 事件契约（各任务引用的权威定义）

事件携带 `EventEnvelope`（`schema_version="1.0"`），payload 键如下。P2 已实现 `tool.execution.started/completed/failed` 与 `tool.approval.requested/resolved`；P3 新增/补全其余 tool 生命周期事件（taxonomy 已在 `canonical_events.py` 注册）。

| event_type | 触发点 | payload 键 |
|---|---|---|
| `tool.call.proposed` | 进入工具阶段、事务建立（`_on_awaiting_tool` 对每个 pending call） | `{"transaction_id": str, "call_id": str, "tool_id": str, "tool_version": str, "canonical_arguments": str, "arguments_sha256": str, "side_effect_class": str, "idempotency_key": str, "preconditions": [str]}` |
| `tool.call.validated` | 参数校验通过 | `{"transaction_id": str, "call_id": str}` |
| `tool.permission.evaluated` | PermissionService 决策后 | `{"transaction_id": str, "call_id": str, "decision": "allow"\|"ask"\|"deny", "approval_fingerprint": str}` |
| `tool.approval.required` | 需人工批准 | `{"transaction_id": str, "call_id": str, "tool_name": str, "reason": str, "arguments": str, "approval_fingerprint": str}` |
| `tool.approval.requested` | `AskService.start_ask`（P2 已有） | `{"call_id", "ask_id", "tool_name", "reason", "arguments"}` |
| `tool.approval.resolved` | `AskService.respond`（P2 已有，approved bool） | `{"ask_id", "call_id", "approved": bool}` |
| `tool.execution.started` | 副作用执行开始（P2 已有） | `{"transaction_id", "call_id", "tool_id", "arguments"}` |
| `tool.execution.completed` | 副作用成功返回（P2 已有） | `{"transaction_id", "call_id", "tool_id", "status": "succeeded", "result_summary", "full_ref", "artifacts", "idempotency_key"}` |
| `tool.execution.failed` | 副作用失败（P2 已有） | `{"transaction_id", "call_id", "tool_id", "status", "error", "idempotency_key"}` |
| `tool.execution.cancelled` | 取消 | `{"transaction_id", "call_id", "tool_id", "reason"}` |
| `tool.execution.indeterminate` | 超时/结果不明 | `{"transaction_id", "call_id", "tool_id", "reason": str, "idempotency_key"}` |
| `tool.result.committed` | 幂等/恢复确认副作用已落定 | `{"transaction_id", "call_id", "tool_id", "result_ref": str, "verdict": "succeeded"\|"failed"}` |

Reducer 终态机（§6.4）：`proposed→validated→permission_evaluated→approval_required→(approved|denied)→running→(succeeded|failed|cancelled|indeterminate)→result_committed`。succeeded/failed/cancelled 为终态（移出 pending）；**indeterminate 保留 pending 供 reconcile**；`tool.result.committed` 将 indeterminate 落定并移出 pending。

---

## 1. 文件结构（本次计划的模块边界）

- Create: `floodmind/agent/runtime/contracts/tool_transaction.py` — `ToolTransaction` 契约 + 终态机（§6.1）。
- Create: `floodmind/agent/runtime/contracts/approval.py` — `ApprovalRecord`（§6.3）。
- Create: `floodmind/agent/runtime/services/approval_fingerprint.py` — 纯函数指纹（§6.3）。
- Create: `floodmind/agent/runtime/services/reconciliation_service.py` — pending/indeterminate reconcile（§16.4 step 5 / §6.5）。
- Create: `floodmind/agent/runtime/services/resume_service.py` — 公开 Resume（§16.4 全流程）。
- Create: `floodmind/agent/runtime/services/idempotency.py` — 幂等键派生 + Journal 幂等查询（§6.5）。
- Modify: `floodmind/agent/runtime/contracts/run_state.py` — `pending_tool_transactions: List[ToolTransaction]`（替换 `PendingToolTransaction`）。
- Modify: `floodmind/agent/runtime/reducer.py` — 完整工具终态机 + thread 门控。
- Modify: `floodmind/agent/runtime/services/tool_execution_service.py` — 全生命周期事件 + 幂等检查 + timeout→indeterminate。
- Modify: `floodmind/agent/native/executor.py` — `_on_awaiting_tool` 发 `tool.call.proposed` 等事件、indeterminate 处理、reconcile 接入。
- Modify: `floodmind/agent/runtime/services/checkpoint_service.py` — 版本校验含 tool registry 版本（§16.4 step 4）。
- Modify: `floodmind/agent/runtime/services/runtime_layout.py` — 增加 `lease_file` 路径（§16.4 fencing）。
- Test: `tests/test_tool_transaction.py`, `tests/test_approval_fingerprint.py`, `tests/test_tool_lifecycle_reducer.py`, `tests/test_idempotency.py`, `tests/test_reconciliation_service.py`, `tests/test_resume_service.py`, `tests/test_p3_acceptance.py`。

---

## 2. 任务

### Task 1: ToolTransaction 契约 + Canonical Arguments（§6.1/§6.2）

**Files:**
- Create: `floodmind/agent/runtime/contracts/tool_transaction.py`
- Test: `tests/test_tool_transaction.py`

**Interfaces:**
- Consumes: `canonical_json`（`floodmind.agent.runtime.contracts.canonical_events`）、`new_id("transaction")`、`RunStatus` 无关（纯数据）。
- Produces:
  - `class SideEffectClass(str, Enum)`: `"read" | "reversible_write" | "irreversible" | "external"`.
  - `class ToolStatus(str, Enum)`: `"proposed" | "validated" | "permission_evaluated" | "approval_required" | "approved" | "denied" | "running" | "succeeded" | "failed" | "cancelled" | "indeterminate" | "result_committed"`.
  - `_LEGAL_TRANSITIONS: Dict[ToolStatus, frozenset[ToolStatus]]`（§6.4 终态机，含 approved→running，denied 终态，indeterminate→result_committed）。
  - `class ToolTransaction(BaseModel)`：字段见 §6.1 JSON（`transaction_id, call_id, tool_id, tool_version="1", canonical_arguments="", arguments_sha256="", workspace_fingerprint="", runtime_fingerprint="", permission_fingerprint="", idempotency_key="", side_effect_class=SideEffectClass.read, status=ToolStatus.proposed, preconditions: List[str]=[], result_ref=""`）。
  - `def transition(self, to: ToolStatus) -> "ToolTransaction"`：非法转移抛 `ValueError`；返回新实例（不改输入，纯函数）。
  - `def canonical_arguments(arguments: dict) -> str`：返回 `canonical_json(arguments)`。
  - `def arguments_sha256(tool_id: str, tool_version: str, canonical_arguments: str) -> str`：`SHA256(tool_id + tool_version + canonical_arguments)`（§6.2，UTF-8）。

- [ ] **Step 1: 写失败测试**（`tests/test_tool_transaction.py`）

```python
import hashlib
import pytest

from floodmind.agent.runtime.contracts.tool_transaction import (
    SideEffectClass, ToolStatus, ToolTransaction,
    canonical_arguments, arguments_sha256,
)
from floodmind.agent.runtime.contracts.canonical_events import canonical_json


def test_canonical_arguments_normalizes_key_order_and_unicode():
    a = {"b": "值", "a": 1, "nested": {"z": True, "x": [2, 1]}}
    s1 = canonical_arguments(a)
    s2 = canonical_arguments({"nested": {"x": [2, 1], "z": True}, "a": 1, "b": "值"})
    assert s1 == s2
    assert '"a":1' in s1 and '"b":"值"' in s1  # sorted keys, ensure_ascii=False


def test_arguments_sha256_is_deterministic_and_defined():
    canon = canonical_arguments({"path": "/tmp/a"})
    h = arguments_sha256("builtin:Read", "1", canon)
    assert h == hashlib.sha256(("builtin:Read" + "1" + canon).encode("utf-8")).hexdigest()
    assert h == arguments_sha256("builtin:Read", "1", canon)  # deterministic
    assert h != arguments_sha256("builtin:Read", "2", canon)  # tool_version changes hash


def test_transition_legal_and_illegal():
    tx = ToolTransaction(transaction_id="ttx_1", call_id="call_1", tool_id="builtin:Write")
    r1 = tx.transition(ToolStatus.validated)
    assert r1.status == ToolStatus.validated and tx.status == ToolStatus.proposed  # immutable
    # 合法链：validated -> permission_evaluated -> approved -> running -> succeeded
    r2 = r1.transition(ToolStatus.permission_evaluated).transition(ToolStatus.approved)
    r3 = r2.transition(ToolStatus.running).transition(ToolStatus.succeeded)
    assert r3.status == ToolStatus.succeeded
    with pytest.raises(ValueError):  # proposed -> running 非法（跳步）
        tx.transition(ToolStatus.running)
    with pytest.raises(ValueError):  # succeeded -> failed 非法（终态）
        r3.transition(ToolStatus.failed)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_tool_transaction.py -v`
Expected: FAIL（`tool_transaction` 模块不存在）

- [ ] **Step 3: 实现**

```python
"""Tool Transaction 契约（目标 §6.1/§6.2/§6.4）。纯数据层，无 I/O。"""

import hashlib
from enum import Enum
from typing import List

from pydantic import BaseModel

from floodmind.agent.runtime.contracts.canonical_events import canonical_json


class SideEffectClass(str, Enum):
    read = "read"
    reversible_write = "reversible_write"
    irreversible = "irreversible"
    external = "external"


class ToolStatus(str, Enum):
    proposed = "proposed"
    validated = "validated"
    permission_evaluated = "permission_evaluated"
    approval_required = "approval_required"
    approved = "approved"
    denied = "denied"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    indeterminate = "indeterminate"
    result_committed = "result_committed"


_LEGAL_TRANSITIONS = {
    ToolStatus.proposed: {ToolStatus.validated, ToolStatus.denied},
    ToolStatus.validated: {ToolStatus.permission_evaluated},
    ToolStatus.permission_evaluated: {ToolStatus.approval_required, ToolStatus.approved, ToolStatus.denied},
    ToolStatus.approval_required: {ToolStatus.approved, ToolStatus.denied},
    ToolStatus.approved: {ToolStatus.running, ToolStatus.cancelled, ToolStatus.failed},
    ToolStatus.running: {ToolStatus.succeeded, ToolStatus.failed, ToolStatus.cancelled, ToolStatus.indeterminate},
    ToolStatus.succeeded: {ToolStatus.result_committed},
    ToolStatus.indeterminate: {ToolStatus.result_committed, ToolStatus.succeeded, ToolStatus.failed},
}
# 终态：无出边
_TERMINAL = {ToolStatus.denied, ToolStatus.failed, ToolStatus.cancelled, ToolStatus.result_committed}


class ToolTransaction(BaseModel):
    transaction_id: str
    call_id: str
    tool_id: str
    tool_version: str = "1"
    canonical_arguments: str = ""
    arguments_sha256: str = ""
    workspace_fingerprint: str = ""
    runtime_fingerprint: str = ""
    permission_fingerprint: str = ""
    idempotency_key: str = ""
    side_effect_class: SideEffectClass = SideEffectClass.read
    status: ToolStatus = ToolStatus.proposed
    preconditions: List[str] = []
    result_ref: str = ""

    def transition(self, to: ToolStatus) -> "ToolTransaction":
        allowed = _LEGAL_TRANSITIONS.get(self.status, frozenset())
        if self.status in _TERMINAL or to not in allowed:
            raise ValueError(f"非法工具状态转移: {self.status.value} -> {to.value}")
        return self.model_copy(update={"status": to})


def canonical_arguments(arguments: dict) -> str:
    return canonical_json(arguments or {})


def arguments_sha256(tool_id: str, tool_version: str, canonical_arguments: str) -> str:
    raw = (tool_id + tool_version + canonical_arguments).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_tool_transaction.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add floodmind/agent/runtime/contracts/tool_transaction.py tests/test_tool_transaction.py
git commit -m "feat(tool): ToolTransaction contract + canonical arguments + state machine"
```

---

### Task 2: Approval Fingerprint（§6.3）

**Files:**
- Create: `floodmind/agent/runtime/contracts/approval.py`（`ApprovalRecord`）
- Create: `floodmind/agent/runtime/services/approval_fingerprint.py`（纯函数）
- Test: `tests/test_approval_fingerprint.py`

**Interfaces:**
- Consumes: `canonical_json`。
- Produces:
  - `compute_approval_fingerprint(*, tool_id: str, tool_version: str, canonical_arguments: str, resolved_targets: list[str], cwd: str, environment_identity: str, workspace_id: str, workspace_generation: str, sandbox_permissions: list[str], agent_tier: str, runtime_mode: str, side_effect_class: str, policy_version: str) -> str`（§6.3 元组按序连接 → SHA256）。
  - `class ApprovalRecord(BaseModel)`：`fingerprint: str, approver: str, decision: str, timestamp: str, expiry: str = "", scope: str = "once", resolved_operation: str = ""`。
  - `fingerprint_matches(record: ApprovalRecord, current: str) -> bool`：`record.fingerprint == current`。

- [ ] **Step 1: 写失败测试**

```python
import hashlib

from floodmind.agent.runtime.services.approval_fingerprint import (
    compute_approval_fingerprint, fingerprint_matches,
)
from floodmind.agent.runtime.contracts.approval import ApprovalRecord
from floodmind.agent.runtime.contracts.tool_transaction import canonical_arguments


def _fp(**kw):
    base = dict(
        tool_id="builtin:Write", tool_version="1", canonical_arguments=canonical_arguments({"path": "/a"}),
        resolved_targets=["/a"], cwd="/work", environment_identity="env-1", workspace_id="ws_1",
        workspace_generation="gen-1", sandbox_permissions=[], agent_tier="main", runtime_mode="execution",
        side_effect_class="reversible_write", policy_version="v1",
    )
    base.update(kw)
    return compute_approval_fingerprint(**base)


def test_fingerprint_deterministic_and_sensitive():
    f1 = _fp()
    assert f1 == _fp()
    assert f1 == hashlib.sha256(
        "|".join([
            "builtin:Write", "1", canonical_arguments({"path": "/a"}), "['/a']", "/work",
            "env-1", "ws_1", "gen-1", "[]", "main", "execution", "reversible_write", "v1",
        ]).encode("utf-8")
    ).hexdigest()


def test_param_path_workspace_policy_change_invalidates():
    f_base = _fp()
    assert _fp(canonical_arguments=canonical_arguments({"path": "/b"})) != f_base  # 参数变
    assert _fp(resolved_targets=["/b"]) != f_base                               # 目标路径变
    assert _fp(workspace_generation="gen-2") != f_base                          # workspace 代变
    assert _fp(policy_version="v2") != f_base                                   # policy 变
    assert _fp(agent_tier="sub") != f_base                                      # tier 变


def test_fingerprint_matches_approval_record():
    rec = ApprovalRecord(fingerprint=_fp(), approver="host", decision="approved")
    assert fingerprint_matches(rec, _fp())
    assert not fingerprint_matches(rec, _fp(policy_version="v2"))
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_approval_fingerprint.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
"""Approval Fingerprint（目标 §6.3）。纯函数，无 I/O/时间/随机。"""

import hashlib
from typing import List

from pydantic import BaseModel


def compute_approval_fingerprint(
    *,
    tool_id: str,
    tool_version: str,
    canonical_arguments: str,
    resolved_targets: List[str],
    cwd: str,
    environment_identity: str,
    workspace_id: str,
    workspace_generation: str,
    sandbox_permissions: List[str],
    agent_tier: str,
    runtime_mode: str,
    side_effect_class: str,
    policy_version: str,
) -> str:
    """§6.3：指纹元组按序定界连接后 SHA256。参数变化即失效。"""
    parts = [
        tool_id, tool_version, canonical_arguments, repr(list(resolved_targets)),
        cwd, environment_identity, workspace_id, workspace_generation,
        repr(list(sandbox_permissions)), agent_tier, runtime_mode,
        side_effect_class, policy_version,
    ]
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class ApprovalRecord(BaseModel):
    fingerprint: str
    approver: str
    decision: str  # approved | denied
    timestamp: str = ""
    expiry: str = ""
    scope: str = "once"  # once | run | rule
    resolved_operation: str = ""


def fingerprint_matches(record: ApprovalRecord, current: str) -> bool:
    return bool(record.fingerprint) and record.fingerprint == current
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_approval_fingerprint.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add floodmind/agent/runtime/contracts/approval.py floodmind/agent/runtime/services/approval_fingerprint.py tests/test_approval_fingerprint.py
git commit -m "feat(tool): approval fingerprint + approval record (6.3)"
```

---

### Task 3: Reducer 完整工具终态机（§6.4 + thread 门控）

**Files:**
- Modify: `floodmind/agent/runtime/contracts/run_state.py`（`pending_tool_transactions: List[ToolTransaction]`）
- Modify: `floodmind/agent/runtime/reducer.py`（全生命周期处理 + `tool.execution.cancelled/indeterminate` + `tool.result.committed`）
- Test: `tests/test_tool_lifecycle_reducer.py`

**Interfaces:**
- Consumes: `ToolTransaction`/`ToolStatus`/`SideEffectClass`（Task 1）、`reduce` 分发现有结构。
- Produces: reducer 处理这些事件：`tool.call.proposed`（append ToolTransaction status=proposed）、`tool.call.validated`、`tool.permission.evaluated`、`tool.approval.required`、`tool.execution.started`（status=running）、`tool.execution.completed`（succeeded + tool turn + **移出 pending**）、`tool.execution.failed`（failed + tool turn + **移出 pending**）、`tool.execution.cancelled`（cancelled + **移出 pending**）、`tool.execution.indeterminate`（status=indeterminate + **保留 pending**）、`tool.result.committed`（verdict 落定 + **移出 pending**）。
- 所有状态转移沿用 `_is_current_thread` 门控（P2）；tool turn 只在 execution.completed/failed 追加（保持投影稳定）。

- [ ] **Step 1: 写失败测试**（`tests/test_tool_lifecycle_reducer.py`）

```python
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.contracts.run_state import RunStatus
from floodmind.agent.runtime.contracts.tool_transaction import ToolStatus
from floodmind.agent.runtime.reducer import initial_run_state, reduce
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def _evt(auth, event_type, payload, **scope):
    return auth.new_envelope(event_type, payload, **scope)


def test_full_lifecycle_to_succeeded(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    tx = "ttx_1"
    events = [
        _evt(auth, "tool.call.proposed", {"transaction_id": tx, "call_id": "call_1",
            "tool_id": "builtin:Write", "arguments_sha256": "h", "idempotency_key": "ik"}),
        _evt(auth, "tool.call.validated", {"transaction_id": tx, "call_id": "call_1"}),
        _evt(auth, "tool.permission.evaluated", {"transaction_id": tx, "call_id": "call_1",
            "decision": "allow", "approval_fingerprint": ""}),
        _evt(auth, "tool.execution.started", {"transaction_id": tx, "call_id": "call_1",
            "tool_id": "builtin:Write", "arguments": "{}"}),
        _evt(auth, "tool.execution.completed", {"transaction_id": tx, "call_id": "call_1",
            "tool_id": "builtin:Write", "status": "succeeded",
            "result_summary": "ok", "full_ref": "", "artifacts": [], "idempotency_key": "ik"}),
    ]
    s = initial_run_state("run_1", thread_id="th")
    for e in events:
        s = reduce(s, e)
    assert s.pending_tool_transactions == []  # 终态移出
    assert any(t["role"] == "tool" for t in s.turns)
    assert s.status == RunStatus.awaiting_model


def test_indeterminate_stays_pending_then_result_committed_removes(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    tx = "ttx_2"
    events = [
        _evt(auth, "tool.call.proposed", {"transaction_id": tx, "call_id": "call_2",
            "tool_id": "builtin:Bash", "arguments_sha256": "h", "idempotency_key": "ik"}),
        _evt(auth, "tool.execution.started", {"transaction_id": tx, "call_id": "call_2",
            "tool_id": "builtin:Bash", "arguments": "{}"}),
        _evt(auth, "tool.execution.indeterminate", {"transaction_id": tx, "call_id": "call_2",
            "tool_id": "builtin:Bash", "reason": "timeout", "idempotency_key": "ik"}),
    ]
    s = initial_run_state("run_1", thread_id="th")
    for e in events:
        s = reduce(s, e)
    assert len(s.pending_tool_transactions) == 1
    assert s.pending_tool_transactions[0].status == ToolStatus.indeterminate
    # 幂等：replay 相同事件不重复副作用
    s2 = initial_run_state("run_1", thread_id="th")
    for e in events:
        s2 = reduce(s2, e)
    assert s.model_dump() == s2.model_dump()
    # result.committed 落定并移出
    s3 = reduce(s, _evt(auth, "tool.result.committed",
        {"transaction_id": tx, "call_id": "call_2", "tool_id": "builtin:Bash",
         "result_ref": "art_1", "verdict": "succeeded"}))
    assert s3.pending_tool_transactions == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_tool_lifecycle_reducer.py -v`
Expected: FAIL（reducer 不识别 `tool.call.proposed` 等）

- [ ] **Step 3: 实现**

`run_state.py`：把 `PendingToolTransaction` import 换成 Task 1 的 `ToolTransaction`，`pending_tool_transactions: List[ToolTransaction] = Field(default_factory=list)`（保留 `PendingToolTransaction` 类定义可以删除——只向前，不再引用）。

`reducer.py` 增以下处理器（全部沿用 `_is_current_thread` 门控；状态转移用 `ToolTransaction.transition` 的合法路径，非法转移 fail-closed 保持现状）：

```python
def _find_ttx(ns, ttx_id):
    return next((t for t in ns.pending_tool_transactions if t.transaction_id == ttx_id), None)

def _update_ttx(ns, ttx_id, to, **update):
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

def _remove_ttx(ns, ttx_id):
    ns.pending_tool_transactions = [
        t for t in ns.pending_tool_transactions if t.transaction_id != ttx_id
    ]
    return ns
```

在 `reduce()` 分发中注册（payload 缺 `transaction_id` 的事件保持现状推进 cursor）：

```python
    if et == "tool.call.proposed":
        return _reduce_tool_proposed(ns, event.payload, event.thread_id, is_current)
    if et == "tool.call.validated":
        return _reduce_tool_validated(ns, event.payload, is_current)
    if et == "tool.permission.evaluated":
        return _reduce_tool_permission(ns, event.payload, is_current)
    if et == "tool.approval.required":
        return _reduce_tool_approval_required(ns, event.payload, is_current)
    if et == "tool.execution.started":
        return _reduce_tool_started(ns, event.payload, is_current)   # 改：设 status=running（沿用 P2）
    if et == "tool.execution.cancelled":
        return _reduce_tool_cancelled(ns, event.payload, is_current)
    if et == "tool.execution.indeterminate":
        return _reduce_tool_indeterminate(ns, event.payload, is_current)
    if et == "tool.result.committed":
        return _reduce_tool_result_committed(ns, event.payload, is_current)
```

各处理器要点（`is_current=False` 时直接返回 `ns` 不动 pending/turns/status）：
- `_reduce_tool_proposed(ns, payload, thread_id, is_current)`：`if not is_current: return ns`；append `ToolTransaction(transaction_id=..., call_id=..., tool_id=..., arguments_sha256=..., idempotency_key=..., side_effect_class=..., status=proposed)`；`ns.status = awaiting_tool`。
- `_reduce_tool_validated`：`_update_ttx(ns, ttx, ToolStatus.validated)`。
- `_reduce_tool_permission`：按 `payload["decision"]` 转 `approved` 或 `denied`（denied 后终态——但移出 pending 由 denied 事件/后续处理决定；此处 denied 直接 `_remove_ttx`）；`permission_fingerprint=payload.get("approval_fingerprint","")`。
- `_reduce_tool_approval_required`：转 `approval_required`；同时沿用 P2 的 `pending_approvals` 处理（如已由 tool.approval.requested 覆盖则这里只记 transaction 状态）。
- `_reduce_tool_started`：改设 `status=running`（原 P2 是 append PendingToolTransaction + executing_tool；改为对已存在 proposed/approved 事务 `_update_ttx(...running)`，不存在则 append 一个 running ToolTransaction）。
- `_reduce_tool_completed`：`succeeded` → `_remove_ttx` + 追加 tool turn（沿用 P2）+ status=awaiting_model。
- `_reduce_tool_failed`（execution.failed）：`failed` → `_remove_ttx` + tool turn（沿用 P2）+ status=awaiting_model（或失败阈值由 executor 管，reducer 只记状态）。
- `_reduce_tool_cancelled`：`cancelled` → `_remove_ttx`。
- `_reduce_tool_indeterminate`：`indeterminate` → `_update_ttx`（保留 pending）。
- `_reduce_tool_result_committed`：按 `verdict` 落定 → `_remove_ttx`（若存在）+ status=awaiting_model。

> 兼容注意：P2 的 `_reduce_tool_started`/`_reduce_tool_completed` 已存在且被 Task 7/8 测试引用；本任务改造它们（事务对象从 `PendingToolTransaction` → `ToolTransaction`），Task 2/7 的 reducer 测试若断言旧 `PendingToolTransaction` 字段需同步更新（forward-only：重写断言，不保留旧行为）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_tool_lifecycle_reducer.py tests/test_reducer.py tests/test_subagent_journal.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: 全量绿（995 基线；如有旧断言按 forward-only 重写）

- [ ] **Step 6: 提交**

```bash
git add floodmind/agent/runtime/contracts/run_state.py floodmind/agent/runtime/reducer.py tests/test_tool_lifecycle_reducer.py tests/test_reducer.py
git commit -m "feat(reducer): full tool transaction state machine + indeterminate pending"
```

---

### Task 4: ToolExecutionService 全生命周期 + 幂等 + timeout→indeterminate（§6.5/§25.4）

**Files:**
- Create: `floodmind/agent/runtime/services/idempotency.py`
- Modify: `floodmind/agent/runtime/services/tool_execution_service.py`
- Modify: `floodmind/agent/native/executor.py`（`_on_awaiting_tool` 发 proposed 事件 + 幂等短路 + indeterminate 处理）
- Test: `tests/test_idempotency.py`、`tests/test_execution_events.py` 增补

**Interfaces:**
- Consumes: `ToolTransaction`/`canonical_arguments`/`arguments_sha256`（Task 1）、`JournalAuthority`、`PermissionDecisionHook`。
- Produces:
  - `derive_idempotency_key(*, tool_id: str, canonical_arguments: str, side_effect_class: str) -> str`：`SHA256(tool_id + canonical_arguments + side_effect_class)`；`read` 类返回 `""`（读可重试，不幂等去重）。
  - `find_committed_result(authority, idempotency_key) -> Optional[dict]`：在 authority journal 中从后往前找同 `idempotency_key` 的 `tool.execution.completed`（succeeded）→ 返回 `{result_summary, full_ref, artifacts}`；找不到返回 `None`。纯读，不写。
  - `ToolExecutionService.execute` 在副作用执行前：若 `side_effect_class != "read"` 且 `idempotency_key` 非空，先 `find_committed_result`，命中则直接返回已提交结果（不重执行）。
  - 超时（`TOOL_TIMEOUT_SECONDS`）：线程池 future 超时 → 返回 `ToolResult(status="error", content="...超时，标记 indeterminate")` 且执行器发 `tool.execution.indeterminate`（不是 failed）。

- [ ] **Step 1: 写失败测试**（`tests/test_idempotency.py`）

```python
from floodmind.agent.runtime.services.idempotency import derive_idempotency_key, find_committed_result
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def test_derive_key_deterministic_and_read_is_empty():
    assert derive_idempotency_key(tool_id="builtin:Write", canonical_arguments='{"path":"/a"}',
                                  side_effect_class="reversible_write") == \
           derive_idempotency_key(tool_id="builtin:Write", canonical_arguments='{"path":"/a"}',
                                  side_effect_class="reversible_write")
    assert derive_idempotency_key(tool_id="builtin:Read", canonical_arguments='{"path":"/a"}',
                                  side_effect_class="read") == ""


def test_find_committed_result_reuses_succeeded(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    ik = derive_idempotency_key(tool_id="builtin:Write", canonical_arguments="{}",
                                side_effect_class="reversible_write")
    auth.emit("tool.execution.completed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Write", "status": "succeeded", "result_summary": "done",
        "full_ref": "ref://1", "artifacts": ["art_1"], "idempotency_key": ik})
    hit = find_committed_result(auth, ik)
    assert hit is not None and hit["result_summary"] == "done" and hit["artifacts"] == ["art_1"]
    assert find_committed_result(auth, "no-such-key") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_idempotency.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`floodmind/agent/runtime/services/idempotency.py`：

```python
"""幂等键派生与 Journal 幂等查询（目标 §6.5）。"""

import hashlib
from typing import Dict, Optional

from floodmind.agent.runtime.services.journal_authority import JournalAuthority


def derive_idempotency_key(*, tool_id: str, canonical_arguments: str, side_effect_class: str) -> str:
    if side_effect_class == "read":
        return ""  # 纯读可按资源版本重试，不做幂等去重
    return hashlib.sha256(
        (tool_id + canonical_arguments + side_effect_class).encode("utf-8")
    ).hexdigest()


def find_committed_result(authority: JournalAuthority, idempotency_key: str) -> Optional[Dict]:
    """从后往前找同幂等键的已提交 succeeded 结果；命中即复用，不重执行。"""
    if not idempotency_key:
        return None
    for event in reversed(authority.read_after(0)):
        if event.event_type != "tool.execution.completed":
            continue
        if event.payload.get("idempotency_key") != idempotency_key:
            continue
        return {
            "result_summary": event.payload.get("result_summary", ""),
            "full_ref": event.payload.get("full_ref", ""),
            "artifacts": list(event.payload.get("artifacts", []) or []),
        }
    return None
```

`tool_execution_service.py`：在 `execute()` 副作用分支前插幂等短路（伪码）：

```python
        # §6.5 幂等：非 read 且有幂等键，先查已提交结果
        canon = canonical_arguments(call.arguments)
        ik = derive_idempotency_key(tool_id=call.name, canonical_arguments=canon,
                                    side_effect_class=_side_effect_class(call.name, spec))
        if ik and journal_authority is not None:
            committed = find_committed_result(journal_authority, ik)
            if committed is not None:
                return ToolResult(tool_call_id=call.id, name=call.name,
                                  content=committed["result_summary"],
                                  status="completed", artifacts=committed["artifacts"],
                                  metadata={"idempotent_replay": True, "full_ref": committed["full_ref"]})
```

（`_side_effect_class` 由 `ToolSpec.is_readonly/is_destructive` 推断：readonly→read；destructive→irreversible；否则 reversible_write。）

`executor.py` `_on_awaiting_tool`：在 `transaction_id = new_id("transaction")` 后，发 `tool.call.proposed`（含 canonical_arguments/arguments_sha256/side_effect_class/idempotency_key）；若 `find_committed_result` 命中（executor 侧或 service 侧），跳过重执行并直接 `_emit_tool_execution_result` 复用结果。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_idempotency.py tests/test_execution_events.py tests/test_tool_execution_service.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: 全量绿

- [ ] **Step 6: 提交**

```bash
git add floodmind/agent/runtime/services/idempotency.py floodmind/agent/runtime/services/tool_execution_service.py floodmind/agent/native/executor.py tests/test_idempotency.py tests/test_execution_events.py
git commit -m "feat(tool): idempotency key + journal dedup + timeout indeterminate"
```

---

### Task 5: ReconciliationService（§16.4 reconcile / §6.5 indeterminate 先 reconcile）

**Files:**
- Create: `floodmind/agent/runtime/services/reconciliation_service.py`
- Test: `tests/test_reconciliation_service.py`

**Interfaces:**
- Consumes: `JournalAuthority`、`RunState`（`pending_tool_transactions`、`pending_approvals`、`child_threads`、`active_background_tasks`）。
- Produces:
  - `class ReconcileResult(BaseModel)`：`indeterminate_resolved: int, approvals_closed: int, background_killed: int, child_threads_closed: int, artifacts_cleaned: int, safe: bool`。
  - `class ReconciliationService`: `reconcile(authority, run_state) -> ReconcileResult`。规则：
    - 每个 `status == indeterminate` 的 pending tool：按 §6.5 "先 reconcile 禁止直接重试"——发 `tool.result.committed`（verdict=failed，reason=reconciled）或调用各工具的 reconcile 查询（本任务统一发 failed 落定，记录 reason）。
    - 每个 `status in {approval_required, approved, running}` 且 journal 无后续终态的 pending tool：发 `tool.execution.indeterminate`（reason=reconciled_pending）→ 再按上条落定，防止僵尸运行。
    - `pending_approvals` 悬空（无 resolved）：发 `tool.approval.resolved(approved=False)`（deny 落定）。
    - `active_background_tasks`：调 `background_task_service` 查询/清理（无 service 则跳过）。
    - `child_threads` 中 `status == "running"`：发 `thread.cancelled`。
  - `def retry_allowed(run_state, transaction_id) -> bool`：`False` if 该事务 status == indeterminate 且未 reconcile（禁止直接重试）。

- [ ] **Step 1: 写失败测试**

```python
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.reducer import initial_run_state, reduce
from floodmind.agent.runtime.services.reconciliation_service import ReconciliationService
from floodmind.agent.runtime.contracts.tool_transaction import ToolTransaction, ToolStatus


def test_indeterminate_is_reconciled_before_retry(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("tool.call.proposed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "idempotency_key": "ik"})
    auth.emit("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "arguments": "{}"})
    auth.emit("tool.execution.indeterminate", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "reason": "timeout", "idempotency_key": "ik"})
    state = auth.replay()
    svc = ReconciliationService()
    assert svc.retry_allowed(state, "ttx_1") is False          # 未 reconcile 禁止重试
    res = svc.reconcile(auth, state)
    assert res.indeterminate_resolved == 1
    after = auth.replay()
    assert after.pending_tool_transactions == []               # 已落定移出
    assert svc.retry_allowed(after, "ttx_1") is True           # 落定后允许新事务
    tail = [e for e in auth.read_after(0) if e.event_type == "tool.result.committed"]
    assert len(tail) == 1 and tail[0].payload["verdict"] == "failed"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_reconciliation_service.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
"""Pending/Indeterminate 对账（目标 §16.4 step5 / §6.5）。恢复安全边界前调用。"""

from pydantic import BaseModel

from floodmind.agent.runtime.contracts.run_state import RunState
from floodmind.agent.runtime.contracts.tool_transaction import ToolStatus
from floodmind.agent.runtime.services.journal_authority import JournalAuthority


class ReconcileResult(BaseModel):
    indeterminate_resolved: int = 0
    approvals_closed: int = 0
    background_killed: int = 0
    child_threads_closed: int = 0
    artifacts_cleaned: int = 0
    safe: bool = True


class ReconciliationService:
    def retry_allowed(self, run_state: RunState, transaction_id: str) -> bool:
        for tx in run_state.pending_tool_transactions:
            if tx.transaction_id == transaction_id and tx.status == ToolStatus.indeterminate:
                return False  # indeterminate 必须先 reconcile，禁止直接重试
        return True

    def reconcile(self, authority: JournalAuthority, run_state: RunState) -> ReconcileResult:
        result = ReconcileResult()
        # 1) indeterminate / 僵尸 pending → 先记 indeterminate，再 result.committed 落定
        for tx in list(run_state.pending_tool_transactions):
            status = tx.status
            if status in (ToolStatus.indeterminate, ToolStatus.approval_required,
                          ToolStatus.approved, ToolStatus.running):
                if status != ToolStatus.indeterminate:
                    authority.emit("tool.execution.indeterminate",
                                   {"transaction_id": tx.transaction_id, "call_id": tx.call_id,
                                    "tool_id": tx.tool_id, "reason": "reconciled_pending",
                                    "idempotency_key": tx.idempotency_key})
                    result.indeterminate_resolved += 1
                authority.emit("tool.result.committed",
                               {"transaction_id": tx.transaction_id, "call_id": tx.call_id,
                                "tool_id": tx.tool_id, "result_ref": "",
                                "verdict": "failed"})
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
        return result
```

> 注：background/artifact temp 清理在本任务留接口（`background_killed`/`artifacts_cleaned` 保持 0），P6 实装；若 `background_task_service` 已注入则对 `active_background_tasks` 调 `kill_session`（按现有注入方式，避免 ContextVar）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_reconciliation_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add floodmind/agent/runtime/services/reconciliation_service.py tests/test_reconciliation_service.py
git commit -m "feat(resume): pending reconciliation — indeterminate reconcile first, deny dangling approvals"
```

---

### Task 6: 公开 Resume（§16.4 全流程 + fencing）

**Files:**
- Modify: `floodmind/agent/runtime/services/runtime_layout.py`（`lease_file`）
- Modify: `floodmind/agent/runtime/services/checkpoint_service.py`（reducer_version + tool registry 版本校验）
- Create: `floodmind/agent/runtime/services/resume_service.py`
- Modify: `floodmind/cli.py`（resume 路径改走 ResumeService；P2 内部 seam 关闭）
- Test: `tests/test_resume_service.py`

**Interfaces:**
- Consumes: `open_journal_authority`、`CheckpointService.replay_from_checkpoint`、`ReconciliationService`、`new_id`。
- Produces:
  - `open_lease(runtime_dir, run_id, owner: str, ttl_seconds: int = 300) -> Lease`：acquire/fenced；CAS 语义（lease 文件存在且未过期 → 拒绝；否则写入 `{owner, pid, acquired_at, expires_at}`）。`Lease.release()`。
  - `class ResumeService`: `resume(*, runtime_dir, conversation_id, task_id, run_id, thread_id, turn_id, checkpoint_id, user_message: str = "") -> ResumeOutcome`（`ResumeOutcome`: `run_state, journal_cursor, reconciled: bool, lease: Lease`）。流程（§16.4 1-9）：
    1. `lease = open_lease(...)`（fencing，失败抛 `ResumeBusyError`）。
    2. `authority = open_journal_authority(runtime_dir, conversation_id=..., task_id=..., run_id=..., thread_id=..., turn_id=...)`。
    3. 加载 checkpoint：`authority.replay()` 验证 cursor ≤ tail；校验 reducer_version（沿用 P2 `replay_from_checkpoint`，扩展 tool registry 版本字段）。
    4. `authority.emit("resume.started", {"checkpoint_id": ..., "cursor": ...})`。
    5. `state = authority.replay()`；`reconciled = ReconciliationService().reconcile(authority, state)`；`state = authority.replay()`（reconcile 后重放）。
    6. 若 `user_message`：`authority.emit("thread.message.sent", {"content": user_message, "turn_index": ...})`；`state = authority.replay()`。
    7. `authority.emit("resume.completed", {"cursor": authority.cursor()})`。
    8. 返回 `ResumeOutcome(run_state=state, journal_cursor=authority.cursor(), reconciled=..., lease=lease)`。
  - `CheckpointManifest` 增加 `tool_registry_version: str = ""`；`replay_from_checkpoint` 校验 `manifest.tool_registry_version == expected`（`expected` 由调用方传入）。

- [ ] **Step 1: 写失败测试**（`tests/test_resume_service.py`）

```python
import pytest

from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.resume_service import (
    ResumeService, open_lease, ResumeBusyError,
)
from floodmind.agent.runtime.reducer import initial_run_state, reduce


def test_lease_is_fenced_until_release(tmp_path):
    lease = open_lease(tmp_path, "run_1", owner="p1", ttl_seconds=300)
    assert lease.acquired
    with pytest.raises(ResumeBusyError):
        open_lease(tmp_path, "run_1", owner="p2", ttl_seconds=300)   # 已被 p1 持有
    lease.release()
    lease2 = open_lease(tmp_path, "run_1", owner="p2", ttl_seconds=300)
    assert lease2.acquired


def test_resume_reconciles_and_replays(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    auth.emit("tool.call.proposed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "idempotency_key": "ik"})
    auth.emit("tool.execution.indeterminate", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Bash", "reason": "timeout", "idempotency_key": "ik"})
    outcome = ResumeService().resume(
        runtime_dir=tmp_path, conversation_id="c", task_id="t", run_id="run_1",
        thread_id="th", turn_id="tu", checkpoint_id="",
    )
    assert outcome.reconciled.indeterminate_resolved >= 1
    assert outcome.run_state.pending_tool_transactions == []   # reconcile 后无僵尸
    types = [e.event_type for e in auth.read_after(0)]
    assert "resume.started" in types and "resume.completed" in types
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_resume_service.py -v`
Expected: FAIL（`resume_service` 不存在）

- [ ] **Step 3: 实现**

`runtime_layout.py` 增加：

```python
def lease_file(runtime_dir, conversation_id, task_id, run_id) -> Path:
    base = (Path(runtime_dir) / "conversations" / conversation_id / "tasks" / task_id
            / "runs" / run_id)
    return base / "lease.json"
```

`resume_service.py`：

```python
"""公开 Checkpoint Resume（目标 §16.4）。fencing lease + replay + reconcile。"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from floodmind.agent.runtime.contracts.run_state import RunState
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.reconciliation_service import (
    ReconciliationService, ReconcileResult,
)
from floodmind.agent.runtime.services.runtime_layout import lease_file


class ResumeBusyError(RuntimeError):
    pass


class Lease(BaseModel):
    acquired: bool
    owner: str = ""
    expires_at: str = ""

    def release(self) -> None:
        if self._path is not None and self._path.exists():
            try:
                self._path.unlink()
            except OSError:
                pass

    # 内部：持有路径
    _path: Optional[Path] = None


def open_lease(runtime_dir, run_id, owner: str, ttl_seconds: int = 300,
               *, conversation_id: str = "", task_id: str = "") -> Lease:
    path = lease_file(Path(runtime_dir), conversation_id, task_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("expires_at", 0)) > now:
                raise ResumeBusyError(f"run {run_id} 已被 {data.get('owner')} 持有（lease 未过期）")
        except ResumeBusyError:
            raise
        except Exception:
            pass  # 损坏/过期 lease 覆盖
    payload = {"owner": owner, "pid": str(__import__("os").getpid()),
               "acquired_at": now, "expires_at": now + ttl_seconds}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    lease = Lease(acquired=True, owner=owner,
                  expires_at=datetime.fromtimestamp(now + ttl_seconds, timezone.utc).isoformat())
    lease._path = path
    return lease


class ResumeOutcome(BaseModel):
    run_state: RunState
    journal_cursor: int
    reconciled: ReconcileResult
    lease: Lease


class ResumeService:
    def resume(self, *, runtime_dir, conversation_id, task_id, run_id, thread_id, turn_id,
               checkpoint_id: str = "", user_message: str = "") -> ResumeOutcome:
        lease = open_lease(runtime_dir, run_id, owner="resume",
                           conversation_id=conversation_id, task_id=task_id)
        try:
            authority = open_journal_authority(
                runtime_dir, conversation_id=conversation_id, task_id=task_id,
                run_id=run_id, thread_id=thread_id, turn_id=turn_id)
            authority.emit("resume.started",
                           {"checkpoint_id": checkpoint_id, "cursor": authority.cursor()})
            state = authority.replay()
            reconciled = ReconciliationService().reconcile(authority, state)
            state = authority.replay()
            if user_message:
                authority.emit("thread.message.sent",
                               {"content": user_message, "turn_index": len(state.turns)})
                state = authority.replay()
            authority.emit("resume.completed", {"cursor": authority.cursor()})
            return ResumeOutcome(run_state=state, journal_cursor=authority.cursor(),
                                 reconciled=reconciled, lease=lease)
        except Exception:
            lease.release()
            raise
```

> 说明：`checkpoint_id` 为空时跳过 manifest 校验（纯 journal 恢复）；非空时走 P2 `replay_from_checkpoint` 的完整校验路径。`Reduce`/`replay` 的 thread 门控沿用 P2——reconcile 后 `state` 即当前线程的权威状态。`reduce` 调用在实现里应统一通过 `authority.replay()`，不要在 service 里手工 reduce（避免与 P2 的 thread-filter 不一致）。

`checkpoint_service.py`：`CheckpointManifest` 加 `tool_registry_version: str = ""`；`save(..., *, tool_registry_version="")`；`replay_from_checkpoint(..., expected_tool_registry_version="")` 校验 `manifest.tool_registry_version == expected`（expected 空则跳过）。

`cli.py`：resume 路径改调 `ResumeService().resume(...)`（关闭 P2 内部 seam：不再手工构造 AgentLoopState + JournalAuthority；返回的 `run_state` 直接交给 `run_from_state`）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_resume_service.py tests/test_checkpoint_cursor.py tests/test_cli_commands.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: 全量绿

- [ ] **Step 6: 提交**

```bash
git add floodmind/agent/runtime/services/resume_service.py floodmind/agent/runtime/services/runtime_layout.py floodmind/agent/runtime/services/checkpoint_service.py floodmind/cli.py tests/test_resume_service.py
git commit -m "feat(resume): public ResumeService — lease fencing, replay, reconcile, resume events"
```

---

### Task 7: 验收（§25.4 + §25.6 fault-injection）

**Files:**
- Create: `tests/test_p3_acceptance.py`
- Modify: 视审计结果修正残余引用。

**Interfaces:**
- Consumes: 全部前序任务产物。

- [ ] **Step 1: 写失败测试**（`tests/test_p3_acceptance.py`）

```python
"""P3 验收（设计 §25.4 Tool/Permission + §25.6 Resume Fault Injection 对应项）。"""

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from floodmind.agent.runtime.services.approval_fingerprint import compute_approval_fingerprint
from floodmind.agent.runtime.services.idempotency import (
    derive_idempotency_key, find_committed_result,
)
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.reconciliation_service import ReconciliationService
from floodmind.agent.runtime.contracts.tool_transaction import ToolStatus, canonical_arguments


def test_approval_fingerprint_param_change_invalidates():
    fp_a = compute_approval_fingerprint(
        tool_id="builtin:Write", tool_version="1",
        canonical_arguments=canonical_arguments({"path": "/safe/a"}),
        resolved_targets=["/safe/a"], cwd="/safe", environment_identity="e",
        workspace_id="w", workspace_generation="g", sandbox_permissions=[],
        agent_tier="main", runtime_mode="execution", side_effect_class="reversible_write",
        policy_version="v1")
    fp_b = compute_approval_fingerprint(
        tool_id="builtin:Write", tool_version="1",
        canonical_arguments=canonical_arguments({"path": "/safe/b"}),
        resolved_targets=["/safe/b"], cwd="/safe", environment_identity="e",
        workspace_id="w", workspace_generation="g", sandbox_permissions=[],
        agent_tier="main", runtime_mode="execution", side_effect_class="reversible_write",
        policy_version="v1")
    assert fp_a != fp_b  # 参数变化 → 旧批准失效


def test_idempotency_key_prevents_duplicate_write(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    ik = derive_idempotency_key(tool_id="builtin:Write",
                                canonical_arguments=canonical_arguments({"path": "/f"}),
                                side_effect_class="reversible_write")
    assert ik != ""
    auth.emit("tool.execution.completed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Write", "status": "succeeded", "result_summary": "wrote",
        "full_ref": "", "artifacts": [], "idempotency_key": ik})
    hit = find_committed_result(auth, ik)
    assert hit is not None  # 重放同键不再重写


def test_timeout_marks_indeterminate_then_reconcile(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    tx = "ttx_t"
    auth.emit("tool.call.proposed", {"transaction_id": tx, "call_id": "c_t",
        "tool_id": "builtin:Bash", "idempotency_key": "ik_t"})
    auth.emit("tool.execution.started", {"transaction_id": tx, "call_id": "c_t",
        "tool_id": "builtin:Bash", "arguments": "{}"})
    auth.emit("tool.execution.indeterminate", {"transaction_id": tx, "call_id": "c_t",
        "tool_id": "builtin:Bash", "reason": "timeout", "idempotency_key": "ik_t"})
    state = auth.replay()
    assert any(t.status == ToolStatus.indeterminate for t in state.pending_tool_transactions)
    svc = ReconciliationService()
    assert svc.retry_allowed(state, tx) is False          # 未 reconcile 禁止重试
    svc.reconcile(auth, state)
    assert auth.replay().pending_tool_transactions == []  # 已落定


def test_resume_fault_injection_no_duplicate_side_effect(tmp_path):
    """§25.6：Tool Started 后、Side Effect 完成但 Result 未提交前 crash → 恢复不重复副作用。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    ik = derive_idempotency_key(tool_id="builtin:Write",
                                canonical_arguments=canonical_arguments({"path": "/f"}),
                                side_effect_class="reversible_write")
    # crash 点在 started 之后、completed 之前
    auth.emit("tool.call.proposed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Write", "idempotency_key": ik})
    auth.emit("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Write", "arguments": canonical_arguments({"path": "/f"})})
    # 恢复：reconcile 先落定（不能直接重试），幂等键保证不重复写
    state = auth.replay()
    svc = ReconciliationService()
    assert svc.retry_allowed(state, "ttx_1") is False
    svc.reconcile(auth, state)
    committed = find_committed_result(auth, ik)
    # 副作用从未 confirmed：reconcile 落定为 failed，幂等查询返回 None（不假装已写）
    assert committed is None
    assert all(e.payload.get("verdict") == "failed"
               for e in auth.read_after(0) if e.event_type == "tool.result.committed")
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_p3_acceptance.py -v`
Expected: FAIL（前序未完成时）

- [ ] **Step 3: 实现（修正残余引用）**

运行验收测试，对每个失败修正。重点是 Task 3/4/5 的一致性：
- 若 `tool.execution.indeterminate` 尚未被 reducer 识别 → 回 Task 3 补处理。
- 若 `retry_allowed` 逻辑与 reconcile 落定不一致 → 回 Task 5 修正。
- 若幂等查询未命中已提交结果 → 回 Task 4 检查 `find_committed_result` 的 payload 键（`idempotency_key`）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_p3_acceptance.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 全量 + 终审准备**

Run: `python -m pytest -q`
Expected: 全量绿（995 基线 + 新增）

- [ ] **Step 6: 提交**

```bash
git add tests/test_p3_acceptance.py
git commit -m "test(p3): acceptance — fingerprint, idempotency, indeterminate reconcile, resume fault injection"
```

---

## 3. 验收（§25.4 Tool/Permission 对应项 + §25.6 Resume Fault Injection）

- [ ] Approval Fingerprint 参数变化后失效（Task 2/7）。
- [ ] Symlink/Traversal 被拒（P1/P2 path_service 既有；Task 4 不回归）。
- [ ] Workspace 外操作必须显式授权（PermissionService 既有；不回归）。
- [ ] Timeout-running 标记 Indeterminate（Task 4/7）。
- [ ] Idempotency Key 防重复写（Task 4/7）。
- [ ] Approved Call 走统一持久链路（P2 approval 事件 + Task 3/4 生命周期，不双写）。
- [ ] 不同 Agent 的 Permission/Path 不串扰（P2 RuntimeContext 注入；不回归）。
- [ ] §25.6：crash 恢复后不重复已确认副作用（幂等键，Task 4/7）。
- [ ] Indeterminate Transaction 先 Reconcile 再允许新事务（Task 5/7）。
- [ ] State 与 Journal 一致（reconcile 后 replay，Task 5/6）。
- [ ] 用户追加指令进入下一安全边界（ResumeService user_message → 新 thread.message.sent，Task 6）。
- [ ] 全量套件 `python -m pytest -q` 绿（重写后基线）。

---

## 4. 交付后的 deferred（供最终评审）

- Reconcile 的 background PID / artifact temp 实际清理延迟到 P6（Task 5 留接口）。
- `tool.result.committed` 的 Outbox worker（§6.4 长期建议）不在本计划；幂等键已提供防重复执行。
- CLI resume 内部 seam（P2 deferred）由 Task 6 关闭（改走 ResumeService）。
- 工具副作用对账的"查询式"reconcile（调用工具 reconcile 函数而非一律 failed）列为后续增强。
