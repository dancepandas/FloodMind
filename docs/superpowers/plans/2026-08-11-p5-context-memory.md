# P5 — Context / Memory 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每次 Model Call 能回答"模型实际看到了什么"（Projection Manifest §9.2 + 输入预算 §9.3），把 Compact 改成 Journal-backed 且不拆 Atomic Group（§9.4）、产出结构化 Summary Event（§9.5）、按 §9.6 算法执行，并给注入内容带上 Provenance（§8.3），通过 §25.5 验收。

**Architecture:** 三段式——(1) 纯数据：`ProjectionManifest`/`InputBudget`/`ProjectionSource` 契约（§9.2/§9.3）；(2) `AtomicGroups` 分组器（§9.4，从现有 ContextCompressor 的 `_group_start`/`_aligned_split_points` 提炼）；(3) 服务：Journal-backed `ContextCompressor` 重构（§9.6 算法，摘要来源切到 Canonical Events，产出 §9.5 Summary Event + Manifest 到 journal），外加 `Provenance` 注入（§8.3）。`DualMemory` 的压缩路径改走新管线。

**Tech Stack:** Python 3.x, pydantic v2, `canonical_events`/`journal_authority`（P2）、`ModelCapabilities`（P4）、`ToolTransaction`（P3）。无第三方新增依赖。

## Global Constraints

> 每项任务的隐含要求包含本节；实现者必须逐字遵守。

- **只向前，不向后兼容、不 fallback**：不新增/不保留旧接口适配器、降级分支、双写路径。现有 `ContextCompressor` 的 Digest/Range 结构是迁移基础，但最终摘要来源切到 Canonical Events（§9.6 注）。
- **单一事实源**：Projection 可丢弃和重建；Journal 和 Memory Version 不可被 Projection 修改（§9.1）。Compact 只 append `context.compaction.completed` 事件到 journal，不修改原始事件。
- **Reducer 确定性**：Manifest 的 `projection_sha256` 只依赖输入消息 + 预算参数；Summary 的结构化字段只依赖被覆盖的事件。
- **Atomic Groups（§9.4）**：Compact MUST NOT 拆分 Tool Call+Result、Reasoning+Assistant Block、Parallel Tool Group、Compaction Block、附件+说明、未决 Approval/Tool Transaction。
- **不静默截断当前用户请求（§9.6/§25.5）**：仍超限时减少 Retrieval 或扩大 Offload，绝不得静默截断当前用户请求。
- **模块化**：按模块/契约落实现。
- **回归**：全量 `python -m pytest -q`（当前基线 **1080 passed, 1 skipped**）。每任务全绿后再提交。
- **WIP 不碰**：100+ 未提交用户 WIP 文件不得修改；提交用显式 `git add <paths>`，绝不用 `git add -A`。

---

## 1. 文件结构（本次计划的模块边界）

- Create: `floodmind/agent/runtime/contracts/projection.py` — `ProjectionManifest`/`ProjectionSource`/`InputBudget`（§9.2/§9.3）。
- Create: `floodmind/agent/native/atomic_groups.py` — `AtomicGroup`/`AtomicGroups`（§9.4 分组器）。
- Create: `floodmind/agent/native/projection.py` — `compute_input_budget`（§9.3）+ `build_manifest`（§9.2）。
- Create: `floodmind/agent/runtime/services/provenance.py` — `Provenance`/`attach_provenance`（§8.3）。
- Modify: `floodmind/agent/native/context_compressor.py` — Journal-backed Compact（§9.6），用 AtomicGroups + Canonical Events 摘要 + Summary Event。
- Modify: `floodmind/memory/dual_memory.py` — 压缩路径走新管线（`_get_cached_or_compress_older` 改接）。
- Modify: `floodmind/agent/native/executor.py` — Model Call 前构建 Projection Manifest（可选，接 Manifest 记录）。
- Test: `tests/test_projection_manifest.py`, `tests/test_atomic_groups.py`, `tests/test_input_budget.py`, `tests/test_provenance.py`, `tests/test_journal_compact.py`, `tests/test_p5_acceptance.py`。

---

## 2. 任务

### Task 1: Projection Manifest + Input Budget 契约（§9.2/§9.3）

**Files:**
- Create: `floodmind/agent/runtime/contracts/projection.py`
- Create: `floodmind/agent/native/projection.py`
- Test: `tests/test_projection_manifest.py`, `tests/test_input_budget.py`

**Interfaces:**
- Consumes: `ModelCapabilities`（P4）。
- Produces:
  - `class ProjectionSource(BaseModel)`：`source_id`, `source_type`（§8.3 枚举 soul|agents|core|episode|retrieval|skill|tool）, `content_sha256`, `original_tokens`, `projected_tokens`, `transform`（identity|compact|offload|summary）, `priority`（int）, `selected`（bool）。
  - `class InputBudget(BaseModel)`：`context_limit`, `reserved_output`, `reserved_tools`, `provider_overhead`, `safety_margin`, `effective_input`（§9.3 公式）。
  - `class ProjectionManifest(BaseModel)`：`projection_id`（`new_id("projection")` 或 `ctx_` 前缀）, `model_call_id`, `projection_version`（str）, `model`, `codec_version`, `capability_snapshot_id`, `budget: InputBudget`, `sources: List[ProjectionSource]`, `tool_registry_version`, `skill_catalog_version`, `total_projected_tokens`, `projection_sha256`。
  - `compute_input_budget(capabilities: ModelCapabilities, *, reserved_output=16384, reserved_tools=8000, provider_overhead=2048, safety_margin=4096) -> InputBudget`：`effective_input = context_limit - reserved_output - reserved_tools - provider_overhead - safety_margin`（§9.3，用 capabilities.context_window 作 context_limit；禁止对所有模型固定减 16K）。
  - `build_manifest(*, model, codec_version, capability_snapshot_id, budget, sources, tool_registry_version="", skill_catalog_version="", model_call_id="") -> ProjectionManifest`：`projection_id="ctx_"+uuid4().hex`, `total_projected_tokens=sum(s.projected_tokens)`, `projection_sha256=SHA256(canonical_json(manifest.model_dump(exclude={"projection_sha256"})))`。

- [ ] **Step 1: 写失败测试**（`tests/test_input_budget.py`）

```python
from floodmind.agent.native.capabilities import ModelCapabilities
from floodmind.agent.native.projection import build_manifest, compute_input_budget
from floodmind.agent.runtime.contracts.projection import ProjectionSource


def test_input_budget_uses_capability_context_window():
    caps = ModelCapabilities(context_window=131072)
    b = compute_input_budget(caps)
    assert b.effective_input == 131072 - 16384 - 8000 - 2048 - 4096


def test_manifest_sha256_deterministic():
    m1 = build_manifest(model="gpt-x", codec_version="1",
                        capability_snapshot_id="cap_1",
                        budget=compute_input_budget(ModelCapabilities(context_window=8192)),
                        sources=[ProjectionSource(source_id="s1", source_type="episode",
                                                 content_sha256="h", original_tokens=10,
                                                 projected_tokens=10, transform="identity",
                                                 priority=1, selected=True)])
    m2 = build_manifest(model="gpt-x", codec_version="1",
                        capability_snapshot_id="cap_1",
                        budget=compute_input_budget(ModelCapabilities(context_window=8192)),
                        sources=[ProjectionSource(source_id="s1", source_type="episode",
                                                 content_sha256="h", original_tokens=10,
                                                 projected_tokens=10, transform="identity",
                                                 priority=1, selected=True)])
    assert m1.projection_sha256 == m2.projection_sha256
    assert m1.total_projected_tokens == 10
    assert m1.projection_id.startswith("ctx_")
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_input_budget.py -v`
Expected: FAIL（`projection` 模块不存在）

- [ ] **Step 3: 实现**

`contracts/projection.py`（§9.2 字段，pydantic）：

```python
"""Projection Manifest / Input Budget（目标 §9.2/§9.3）。纯数据层。"""

from typing import List

from pydantic import BaseModel


class ProjectionSource(BaseModel):
    source_id: str = ""
    source_type: str = ""          # soul|agents|core|episode|retrieval|skill|tool
    content_sha256: str = ""
    original_tokens: int = 0
    projected_tokens: int = 0
    transform: str = "identity"    # identity|compact|offload|summary
    priority: int = 0
    selected: bool = True


class InputBudget(BaseModel):
    context_limit: int = 0
    reserved_output: int = 0
    reserved_tools: int = 0
    provider_overhead: int = 0
    safety_margin: int = 0
    effective_input: int = 0


class ProjectionManifest(BaseModel):
    projection_id: str = ""
    model_call_id: str = ""
    projection_version: str = "1"
    model: str = ""
    codec_version: str = ""
    capability_snapshot_id: str = ""
    budget: InputBudget = InputBudget()
    sources: List[ProjectionSource] = []
    tool_registry_version: str = ""
    skill_catalog_version: str = ""
    total_projected_tokens: int = 0
    projection_sha256: str = ""
```

`native/projection.py`（§9.3 公式 + §9.2 构建）：

```python
"""输入预算（§9.3）+ Projection Manifest 构建（§9.2）。纯函数，无 I/O/时间/随机（除投影 id）。"""

import hashlib
import uuid

from floodmind.agent.native.capabilities import ModelCapabilities
from floodmind.agent.runtime.contracts.canonical_events import canonical_json
from floodmind.agent.runtime.contracts.projection import (
    InputBudget, ProjectionManifest, ProjectionSource,
)

# §9.3 预算项默认值（不得对所有模型固定减 16K；context_limit 取 capabilities）
_DEFAULT_RESERVED_OUTPUT = 16384
_DEFAULT_RESERVED_TOOLS = 8000
_DEFAULT_PROVIDER_OVERHEAD = 2048
_DEFAULT_SAFETY_MARGIN = 4096


def compute_input_budget(
    capabilities: ModelCapabilities,
    *,
    reserved_output: int = _DEFAULT_RESERVED_OUTPUT,
    reserved_tools: int = _DEFAULT_RESERVED_TOOLS,
    provider_overhead: int = _DEFAULT_PROVIDER_OVERHEAD,
    safety_margin: int = _DEFAULT_SAFETY_MARGIN,
) -> InputBudget:
    context_limit = capabilities.context_window or 0
    effective = max(0, context_limit - reserved_output - reserved_tools
                    - provider_overhead - safety_margin)
    return InputBudget(context_limit=context_limit, reserved_output=reserved_output,
                       reserved_tools=reserved_tools, provider_overhead=provider_overhead,
                       safety_margin=safety_margin, effective_input=effective)


def build_manifest(
    *,
    model: str,
    codec_version: str,
    capability_snapshot_id: str,
    budget: InputBudget,
    sources: list,
    tool_registry_version: str = "",
    skill_catalog_version: str = "",
    model_call_id: str = "",
) -> ProjectionManifest:
    manifest = ProjectionManifest(
        projection_id=f"ctx_{uuid.uuid4().hex}",
        model_call_id=model_call_id,
        model=model,
        codec_version=codec_version,
        capability_snapshot_id=capability_snapshot_id,
        budget=budget,
        sources=[s if isinstance(s, ProjectionSource) else ProjectionSource(**s) for s in sources],
        tool_registry_version=tool_registry_version,
        skill_catalog_version=skill_catalog_version,
        total_projected_tokens=sum(
            (s.projected_tokens if isinstance(s, ProjectionSource) else s.get("projected_tokens", 0))
            for s in sources),
    )
    manifest.projection_sha256 = hashlib.sha256(
        canonical_json(manifest.model_dump(exclude={"projection_sha256"})).encode("utf-8")
    ).hexdigest()
    return manifest
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_input_budget.py tests/test_projection_manifest.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add floodmind/agent/runtime/contracts/projection.py floodmind/agent/native/projection.py tests/test_input_budget.py tests/test_projection_manifest.py
git commit -m "feat(context): ProjectionManifest + InputBudget (9.2/9.3)"
```

---

### Task 2: Atomic Groups（§9.4）

**Files:**
- Create: `floodmind/agent/native/atomic_groups.py`
- Test: `tests/test_atomic_groups.py`

**Interfaces:**
- Consumes: 消息形状（role/name/tool_call_id/tool_calls/compaction/attachment 字段，与现 executor/compressor 消息 wire 兼容）。
- Produces:
  - `class AtomicGroup(BaseModel)`：`indices: List[int]`, `kind: str`（tool_pair|reasoning_block|parallel_tool|compaction_block|attachment|pending_txn|message）, `required_together: bool = True`。
  - `class AtomicGroups`：`build(messages: List[dict], *, pending_transaction_ids: List[str] = []) -> List[AtomicGroup]`；`is_split(groups, start, end) -> bool`；`aligned_ranges(messages, *, pending_transaction_ids=[]) -> List[Tuple[int, int]]`（不拆任何 Atomic Group 的可压缩段）。
  - §9.4 规则：
    1. Assistant Tool Calls + 全部对应 Tool Results（按 `tool_call_id` 配对）→ `tool_pair`。
    2. Provider Reasoning/Signature + 所属 Assistant Block → `reasoning_block`。
    3. Parallel Tool Call Group（同一 assistant 消息内多个 tool_call 的 index 全含）→ `parallel_tool`。
    4. Compaction Block（role=="system" 且含 `compaction` 标记/摘要消息）→ `compaction_block`。
    5. 用户附件 + 说明（attachment 字段或紧随的用户消息）→ `attachment`。
    6. 未决 Approval/Tool Transaction（消息引用了 pending transaction）→ `pending_txn`。

- [ ] **Step 1: 写失败测试**（`tests/test_atomic_groups.py`）

```python
from floodmind.agent.native.atomic_groups import AtomicGroups


def _tool_result(tool_call_id):
    return {"role": "tool", "tool_call_id": tool_call_id, "content": "ok"}


def test_tool_pair_never_split():
    msgs = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "Read", "arguments": {}}]},
        _tool_result("c1"),
    ]
    groups = AtomicGroups().build(msgs)
    ranges = AtomicGroups().aligned_ranges(msgs)
    # 任何 range 不得只含 assistant tool_call 而不含其 tool result
    for start, end in ranges:
        sliced = msgs[start:end]
        assert not (any(m.get("tool_calls") for m in sliced) and not any(m.get("role") == "tool" for m in sliced))


def test_parallel_tool_group_kept():
    msgs = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "A", "arguments": {}}, {"id": "c2", "name": "B", "arguments": {}}]},
        _tool_result("c1"), _tool_result("c2"),
    ]
    ranges = AtomicGroups().aligned_ranges(msgs)
    for start, end in ranges:
        sliced = msgs[start:end]
        tc = [m for m in sliced if m.get("tool_calls")]
        if tc:
            assert len(tc[0]["tool_calls"]) == 2  # 并行组整体保留
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_atomic_groups.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**（`floodmind/agent/native/atomic_groups.py`）

```python
"""Atomic Groups（目标 §9.4）：Compact MUST NOT 拆分的消息集合。"""

from typing import Dict, List, Tuple

from pydantic import BaseModel


class AtomicGroup(BaseModel):
    indices: List[int]
    kind: str
    required_together: bool = True


class AtomicGroups:
    def build(self, messages: List[Dict], *, pending_transaction_ids: List[str] = []) -> List[AtomicGroup]:
        groups: List[AtomicGroup] = []
        i = 0
        while i < len(messages):
            m = messages[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                # 并行工具组 + 全部 tool results（按 tool_call_id 配对，可能跨多条）
                call_ids = [tc.get("id") for tc in m["tool_calls"] if tc.get("id")]
                j = i + 1
                while j < len(messages) and messages[j].get("role") == "tool" and messages[j].get("tool_call_id") in call_ids:
                    j += 1
                groups.append(AtomicGroup(indices=list(range(i, j)), kind="parallel_tool"))
                i = j
                continue
            if m.get("role") == "assistant" and (m.get("reasoning") or m.get("reasoning_content")):
                # reasoning + 所属 assistant block（同一条消息内已含 → 单索引组）
                groups.append(AtomicGroup(indices=[i], kind="reasoning_block"))
                i += 1
                continue
            if m.get("role") == "system" and (m.get("compaction") or "compaction" in str(m.get("content", ""))[:40]):
                groups.append(AtomicGroup(indices=[i], kind="compaction_block"))
                i += 1
                continue
            if m.get("attachment"):
                groups.append(AtomicGroup(indices=[i], kind="attachment"))
                i += 1
                continue
            if pending_transaction_ids and str(m.get("transaction_id", "")) in pending_transaction_ids:
                groups.append(AtomicGroup(indices=[i], kind="pending_txn"))
                i += 1
                continue
            i += 1
        return groups

    def _member_indices(self, messages, groups) -> Dict[int, str]:
        member: Dict[int, str] = {}
        for g in groups:
            for idx in g.indices:
                member[idx] = g.kind
        return member

    def aligned_ranges(self, messages, *, pending_transaction_ids: List[str] = []) -> List[Tuple[int, int]]:
        """按 §9.4 构造不拆任何 Atomic Group 的可压缩段（连续段，边界落在组间隙）。"""
        groups = self.build(messages, pending_transaction_ids=pending_transaction_ids)
        member = self._member_indices(messages, groups)
        ranges: List[Tuple[int, int]] = []
        start = 0
        for i in range(1, len(messages) + 1):
            if i < len(messages) and (i in member or i - 1 in member):
                continue  # 在组内部，不能断开
            if start < i:
                ranges.append((start, i))
            start = i
        return ranges
```

> 注：`aligned_ranges` 返回的可压缩段边界都不落在 Atomic Group 中间；Task 3 的 Compact 只整体处理这些段（或整段保留/整段压缩）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_atomic_groups.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add floodmind/agent/native/atomic_groups.py tests/test_atomic_groups.py
git commit -m "feat(context): Atomic Groups — tool pairs, parallel groups, reasoning, compaction (9.4)"
```

---

### Task 3: Journal-backed Compact + Summary Event（§9.5/§9.6）

**Files:**
- Modify: `floodmind/agent/native/context_compressor.py`
- Modify: `floodmind/agent/native/executor.py`（`_on_context_compress` 走新管线 + 传 journal_authority）
- Test: `tests/test_journal_compact.py`

**Interfaces:**
- Consumes: `AtomicGroups`（Task 2）、`ProjectionManifest`/`build_manifest`/`compute_input_budget`（Task 1）、`JournalAuthority`（P2）、`ModelCapabilities`（P4）。
- Produces:
  - `class CompactSummary(BaseModel)`：`covered_sequence_start: int = 0`, `covered_sequence_end: int = 0`, `source_event_ids: List[str] = []`, `source_sha256: str = ""`, `summary: str = ""`, `completed_facts: List[str] = []`, `open_commitments: List[str] = []`, `decisions: List[str] = []`, `files_and_symbols: List[str] = []`, `tool_side_effects: List[str] = []`, `artifacts: List[str] = []`, `unresolved_transactions: List[str] = []`, `recipe_version: str = "1"`（§9.5 全字段）。
  - `ContextCompressor.compress(...)` 重构为：`compress_journal(messages, authority, *, capabilities, budget, pending_transaction_ids=[]) -> CompressionResult`（§9.6 算法 1-11）：
    1. 用 `AtomicGroups.aligned_ranges` 定可压缩段；
    2. 固定保留 System/Soul/AGENTS/当前用户请求/未决事务（`_is_strict_continuation`/`_group_start` 沿用）；
    3. 保留最近完整 Group；
    4. 大工具结果 Artifact Offload（`_trim_tool_outputs` 沿用 + `transform="offload"`）；
    5. 从较早低优先级来源压缩（结构化摘要）；
    6. 生成 `CompactSummary`（摘要来源切到 Canonical Events：用 `authority.replay()` 的 turns 计算 `source_sha256` 与结构化字段）；
    7. `authority.emit("context.compaction.completed", CompactSummary(...))`（§9.5 event）；
    8. 再次计数；仍超限时减少 Retrieval/扩大 Offload，绝不静默截断当前用户请求；
    9. 返回 `CompressionResult`（含 `manifest`：`build_manifest(...)`，sources 带 `transform`）。
  - 保留现有 `should_compress`/`_generate_summary` 作为摘要引擎（规则 + LLM），但摘要输入改来自 Canonical Events 投影。

- [ ] **Step 1: 写失败测试**（`tests/test_journal_compact.py`）

```python
from floodmind.agent.native.context_compressor import ContextCompressor
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def test_compact_emits_summary_event_and_keeps_user_message(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "long user message that must never be truncated", "turn_index": 0})
    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "long user message that must never be truncated"},
        {"role": "assistant", "content": "a" * 2000},
        {"role": "assistant", "content": "b" * 2000},
    ]
    cc = ContextCompressor()
    result = cc.compress_journal(messages, auth, capabilities=None,
                                 budget=None, max_context_tokens=1200)
    # 当前用户请求不静默截断
    assert any("long user message that must never be truncated" in m.get("content", "")
               for m in result.compressed_messages if m.get("role") == "user")
    # Summary Event 落 journal
    types = [e.event_type for e in auth.read_after(0)]
    assert "context.compaction.completed" in types
    # 原始 journal 不变（只新增 summary 事件，不修改原事件）
    first = auth.read_after(0)[0]
    assert first.event_type == "thread.message.sent" and first.payload["content"] == "long user message that must never be truncated"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_journal_compact.py -v`
Expected: FAIL（`compress_journal` 不存在）

- [ ] **Step 3: 实现**（在 `context_compressor.py` 增加 `compress_journal`，重构 `compress` 复用分组/摘要引擎；`CompactSummary` + emit；executor `_on_context_compress` 改调）

```python
    def compress_journal(self, messages, authority, *, capabilities=None, budget=None,
                         pending_transaction_ids=None, max_context_tokens=None):
        """§9.6 Journal-backed Compact：Atomic Groups + 结构化 Summary Event + Manifest。"""
        from floodmind.agent.native.atomic_groups import AtomicGroups
        from floodmind.agent.native.projection import build_manifest, compute_input_budget
        from floodmind.agent.runtime.contracts.projection import InputBudget, ProjectionSource

        pending_transaction_ids = pending_transaction_ids or []
        budget = budget or (compute_input_budget(capabilities) if capabilities else InputBudget())
        limit = max_context_tokens or budget.effective_input or 1200

        # 1) 固定保留：System/Soul/AGENTS/当前用户请求/未决事务 + 最近完整 Group
        ranges = AtomicGroups().aligned_ranges(messages, pending_transaction_ids=pending_transaction_ids)
        start, end = self._aligned_split_points(messages)
        # 2) 构造可压缩段（早期低优先级来源）
        compressible = [messages[r0:r1] for r0, r1 in ranges if r1 <= start and (r0, r1) != (0, 0)]
        # 3) 从较早来源压缩（规则/LLM 摘要引擎）
        summary = self._generate_summary([m for seg in compressible for m in seg]) if compressible else ""
        # 4) 大工具结果 offload
        trimmed = self._trim_tool_outputs(messages)
        # 5) 结构化 Summary Event
        from floodmind.agent.runtime.contracts.canonical_events import canonical_json
        import hashlib
        source_text = canonical_json([m for seg in compressible for m in seg])
        summary_event = {
            "covered_sequence_start": start, "covered_sequence_end": end,
            "source_event_ids": [], "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "summary": summary, "completed_facts": [], "open_commitments": [],
            "decisions": [], "files_and_symbols": [], "tool_side_effects": [],
            "artifacts": [], "unresolved_transactions": pending_transaction_ids,
            "recipe_version": "1",
        }
        if authority is not None:
            authority.emit("context.compaction.completed", summary_event)
        # 6) 重建压缩后消息：头部（固定保留） + 摘要块 + 尾部（最近完整 Group）
        compressed = [dict(m) for m in messages[:start]]
        if summary:
            compressed.append({"role": "system", "content": "[compact] " + summary, "compaction": True})
        compressed.extend(dict(m) for m in trimmed[end:])
        # 7) 再次计数；仍超限 → 扩大 offload，不截断当前用户请求
        if self._estimate_tokens(compressed) > limit:
            compressed = self._trim_tool_outputs(compressed)
        # 8) Manifest
        manifest = build_manifest(model="", codec_version="", capability_snapshot_id="",
                                  budget=budget,
                                  sources=[ProjectionSource(source_id="", source_type="episode",
                                                            content_sha256=summary_event["source_sha256"],
                                                            original_tokens=self._estimate_tokens(trimmed),
                                                            projected_tokens=self._estimate_tokens(compressed),
                                                            transform="compact", priority=1, selected=True)])
        return CompressionResult(
            compressed_messages=compressed,
            summary=summary,
            saved_tokens=max(0, self._estimate_tokens(messages) - self._estimate_tokens(compressed)),
            original_messages=len(messages),
            compressed_messages_count=len(compressed),
            manifest=manifest,
        )
```

（`CompressionResult` 增加 `manifest` 字段；executor `_on_context_compress` 改调 `compress_journal(messages, self._journal_authority, capabilities=self._capabilities)`，并把 manifest 记录到事件。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_journal_compact.py tests/test_context_compressor.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: 全量绿（1080 基线）

- [ ] **Step 6: 提交**

```bash
git add floodmind/agent/native/context_compressor.py floodmind/agent/native/executor.py tests/test_journal_compact.py
git commit -m "feat(context): journal-backed compact — atomic groups, summary event, manifest (9.5/9.6)"
```

---

### Task 4: Provenance（§8.3）

**Files:**
- Create: `floodmind/agent/runtime/services/provenance.py`
- Modify: `floodmind/agent/native/projection.py`（`build_manifest` 接 provenance sources）
- Test: `tests/test_provenance.py`

**Interfaces:**
- Consumes: `canonical_json`。
- Produces:
  - `class Provenance(BaseModel)`：`source_type: str`, `source_id: str`, `content_sha256: str = ""`, `version: str = ""`, `transform: str = "identity"`（§8.3）。
  - `def attach_provenance(content: str, *, source_type, source_id, version="") -> Dict`：返回 `{"__provenance": Provenance(...), "content": content}` 或在 ProjectionSource 生成时调用。
  - `def source_sha256(content: str) -> str`：`SHA256(canonical_json({"content": content}))`。
  - `def to_projection_source(prov: Provenance, *, original_tokens, projected_tokens) -> ProjectionSource`。

- [ ] **Step 1: 写失败测试**（`tests/test_provenance.py`）

```python
from floodmind.agent.runtime.services.provenance import (
    Provenance, attach_provenance, source_sha256, to_projection_source,
)


def test_provenance_attached_and_hashed():
    wrapped = attach_provenance("project instruction", source_type="agents", source_id="ag_1", version="v3")
    assert wrapped["__provenance"].source_type == "agents"
    assert wrapped["__provenance"].content_sha256 == source_sha256("project instruction")
    assert source_sha256("a") != source_sha256("b")


def test_provenance_to_projection_source():
    prov = Provenance(source_type="soul", source_id="soul_1", version="v1",
                      content_sha256=source_sha256("x"))
    ps = to_projection_source(prov, original_tokens=10, projected_tokens=10)
    assert ps.source_type == "soul" and ps.source_id == "soul_1"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_provenance.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**（`floodmind/agent/runtime/services/provenance.py`）

```python
"""Provenance（目标 §8.3）：所有注入模型的非用户原文携带来源。纯函数。"""

import hashlib

from pydantic import BaseModel

from floodmind.agent.runtime.contracts.canonical_events import canonical_json
from floodmind.agent.runtime.contracts.projection import ProjectionSource


class Provenance(BaseModel):
    source_type: str = ""   # soul|agents|core|episode|retrieval|skill|tool
    source_id: str = ""
    content_sha256: str = ""
    version: str = ""
    transform: str = "identity"


def source_sha256(content: str) -> str:
    return hashlib.sha256(canonical_json({"content": content}).encode("utf-8")).hexdigest()


def attach_provenance(content: str, *, source_type: str, source_id: str, version: str = "") -> dict:
    return {"__provenance": Provenance(
        source_type=source_type, source_id=source_id,
        content_sha256=source_sha256(content), version=version), "content": content}


def to_projection_source(prov: Provenance, *, original_tokens: int, projected_tokens: int) -> ProjectionSource:
    return ProjectionSource(source_id=prov.source_id, source_type=prov.source_type,
                            content_sha256=prov.content_sha256,
                            original_tokens=original_tokens, projected_tokens=projected_tokens,
                            transform=prov.transform, priority=1, selected=True)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_provenance.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add floodmind/agent/runtime/services/provenance.py tests/test_provenance.py
git commit -m "feat(context): provenance — source type/id/hash/version on injected content (8.3)"
```

---

### Task 5: 验收（§25.5 Compact）

**Files:**
- Create: `tests/test_p5_acceptance.py`
- Modify: 视审计结果修正残余引用。

**Interfaces:**
- Consumes: 全部前序任务产物。

- [ ] **Step 1: 写失败测试**（`tests/test_p5_acceptance.py`）

```python
"""P5 验收（设计 §25.5 Compact 对应项）。"""

import hashlib

from floodmind.agent.native.context_compressor import ContextCompressor
from floodmind.agent.native.atomic_groups import AtomicGroups
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.contracts.canonical_events import canonical_json


def test_compact_does_not_split_tool_call_result_atomic_group(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "Read", "arguments": {"p": 1}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file content " + "x" * 5000},
        {"role": "assistant", "content": "done " * 500},
    ]
    cc = ContextCompressor()
    result = cc.compress_journal(messages, auth, max_context_tokens=800)
    compressed = result.compressed_messages
    # 不拆 Tool Call + Result：要么整组保留，要么整组被摘要替换，绝不只留一半
    tc = [m for m in compressed if m.get("tool_calls")]
    tr = [m for m in compressed if m.get("role") == "tool"]
    assert len(tc) == len(tr)  # 工具组完整


def test_compact_replay_block_not_lost(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "block", "reasoning": "think", "tool_calls": [], "is_final": True, "usage": {}})
    messages = [{"role": "system", "content": "S"}, {"role": "assistant", "content": "block", "reasoning": "think"}]
    before = auth.replay().turns
    cc = ContextCompressor()
    cc.compress_journal(messages, auth, max_context_tokens=400)
    after = auth.replay().turns
    assert before == after  # 原始 journal 不变（compact 只 append summary，不改原事件）
    assert any(e.event_type == "context.compaction.completed" for e in auth.read_after(0))


def test_compact_never_truncates_current_user_message(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    current = "当前用户请求绝不能静默截断 " * 200
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": current}]
    cc = ContextCompressor()
    result = cc.compress_journal(messages, auth, max_context_tokens=50)
    assert any(m.get("role") == "user" and m.get("content") == current for m in result.compressed_messages)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_p5_acceptance.py -v`
Expected: FAIL（前序未完成时）

- [ ] **Step 3: 实现（修正残余引用）**

运行验收测试，对每个失败修正。重点是：`_aligned_split_points` 的保留范围必须覆盖当前用户请求（`_is_strict_continuation` 沿用）；`compress_journal` 的工具组整体处理；摘要事件不改原始 journal。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_p5_acceptance.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 全量 + 终审准备**

Run: `python -m pytest -q`
Expected: 全量绿（1080 基线 + 新增）

- [ ] **Step 6: 提交**

```bash
git add tests/test_p5_acceptance.py
git commit -m "test(context): acceptance — atomic groups, replay block preserved, user message intact (25.5)"
```

---

## 3. 验收（§25.5 Compact 对应项 + §8.3 Provenance）

- [ ] Compact 不拆 Tool Call/Result Atomic Group（Task 2/3/5）。
- [ ] 不丢 Provider Replay Block（Task 3/5：原始 journal 不变）。
- [ ] 当前用户消息不静默截断（Task 3/5）。
- [ ] Summary 可追溯 Event Range/Hash（§9.5 `covered_sequence_start/end` + `source_sha256`，Task 3）。
- [ ] 请求不超 Context（§9.3 预算 + 再次计数，Task 1/3）。
- [ ] 原始 Journal 不变（只 append summary 事件，Task 3/5）。
- [ ] Projection Manifest 每次 Model Call 可回答"模型看到了什么"（Task 1/3）。
- [ ] 注入内容带 Provenance（§8.3，Task 4）。
- [ ] 全量套件 `python -m pytest -q` 绿。

---

## 4. 交付后的 deferred（供最终评审）

- Manifest 记录到 journal 作为事件 payload（`context.projection.committed`）待后续；本计划返回 manifest 供 executor 使用。
- `capability_snapshot_id`/`tool_registry_version`/`skill_catalog_version` 的实时填充待 P4/P6 接线。
- Soul/Core/AGENTS 的版本化文件存储（§8.2）与 Provenance 已对接；实际 Soul/AGENTS 文件加载与 hash 记录待宿主侧。
- Retrieval 投影（RAG/Repo Map）的预算减让待 P6。
