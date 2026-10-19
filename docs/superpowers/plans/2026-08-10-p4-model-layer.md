# P4 — Model Layer 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把模型层重构为目标分层（§7.1）：`ModelTransport`（纯 Wire I/O）→ `ProviderCodec`（Canonical↔Provider）→ `ResponsePipeline`（状态化组装）→ `ModelCapabilities`（数据化能力，消除能力判断散落在 `model.startswith(...)`）；Retry Advice 由 Transport 返回、Orchestrator 决策（§7.7）；§25.2 Golden Fixtures 全绿。

**Architecture:** 四段式——(1) 纯数据：`ModelCapabilities` + 分层覆盖（provider→family→exact→discovery）；(2) `ModelTransport` Protocol + `OpenAIChatTransport`（把现有 `openai.OpenAI` 客户端收进 Transport，返回 RawResponse + Retry Advice）；(3) `ProviderCodec`（现 `ProviderPipeline` 收敛重命名，负责 Canonical Request↔Provider、Provider Raw Event→Canonical Parts、原生 Replay Block 保留）；(4) `ResponsePipeline`（状态化组装，把 `ModelClient.stream_chat` 里的 tool delta 累积/part 事件/终态判断抽出来）。`ModelClient` 保持稳定公共门面，组合 Transport+Codec+ResponsePipeline。

**Tech Stack:** Python 3.x, openai SDK, httpx, pydantic v2, `canonical_events.canonical_json`。无第三方新增依赖。

## Global Constraints

> 每项任务的隐含要求包含本节；实现者必须逐字遵守。

- **只向前，不向后兼容、不 fallback**：不新增/不保留旧接口适配器、降级分支、双写路径。现有 `ProviderPipeline` 收敛为 `ProviderCodec`（重命名即迁移，不保留旧名适配器）。
- **单一事实源**：Canonical Event Journal 仍是唯一运行事实源；模型层只做 wire/组装，不引入第二个历史源。
- **Reducer 确定性**：Capabilities 解析是纯函数（给定 provider/family/model → 确定性能力）；每次请求记录 Capability Snapshot 含来源版本与 observed timestamp（timestamp 在运行时记录，不进 reducer）。
- **数据化能力（§7.6）**：能力判断 MUST 走 `ModelCapabilities` 解析，禁止新增 `model.startswith(...)` 能力判断；已存在的 `startswith`（provider 路由 `match()` 内）属于路由而非能力，可保留但需注释。
- **Retry（§7.7）**：Transport 只返回 Retry Advice；是否重试由 Orchestrator 决定；默认禁止自动重试 refusal/filtered/max_tokens/paused/已开始输出无 replay safety。
- **模块化**：按模块/契约落实现，新代码放对目录。
- **回归**：全量 `python -m pytest -q`（当前基线 **1041 passed, 1 skipped**）。每任务全绿后再提交。
- **WIP 不碰**：100+ 未提交用户 WIP 文件不得修改；提交用显式 `git add <paths>`，绝不用 `git add -A`。

---

## 1. 文件结构（本次计划的模块边界）

- Create: `floodmind/agent/native/capabilities.py` — `ModelCapabilities`/`CapabilitySource`/`CapabilitySnapshot`/`CapabilityRegistry`（§7.6）。
- Create: `floodmind/agent/runtime/contracts/canonical_parts.py` — Canonical Part 事件与类型（§7.5）。
- Create: `floodmind/agent/native/transport.py` — `TransportRetryAdvice`/`ModelTransport` Protocol/`RawResponse`/`OpenAIChatTransport`（§7.2/§7.7）。
- Create: `floodmind/agent/native/providers/codec.py` — `ProviderCodec`（ProviderPipeline 收敛重命名，§7.3）。
- Create: `floodmind/agent/native/response_pipeline.py` — `ResponsePipeline`（状态化组装，§7.4）。
- Modify: `floodmind/agent/native/providers/base.py`（`ProviderPipeline` → `ProviderCodec` 基类）+ 各 provider（dashscope/kimi/deepseek/minimax/openai_compatible）→ Codec 子类。
- Modify: `floodmind/agent/native/model_client.py` — 组合 Transport+Codec+ResponsePipeline，`stream_chat` 内联累积逻辑移除。
- Modify: `floodmind/agent/native/retry.py` — 暴露 Orchestrator 决策函数（§7.7 默认禁重试清单）。
- Modify: `floodmind/agent/native/executor.py` — 重试循环改用 Retry Advice 决策。
- Test: `tests/test_capabilities.py`, `tests/test_canonical_parts.py`, `tests/test_transport.py`, `tests/test_codec.py`, `tests/test_response_pipeline.py`, `tests/test_retry_policy.py`, `tests/test_golden_fixtures.py`。

---

## 2. 任务

### Task 1: ModelCapabilities 数据模型 + 分层覆盖（§7.6）

**Files:**
- Create: `floodmind/agent/native/capabilities.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**
- Consumes: 无（纯数据层，仅 stdlib/pydantic）。
- Produces:
  - `class CapabilitySource(str, Enum)`: `"provider_default" | "family" | "exact" | "discovery"`.
  - `class ModelCapabilities(BaseModel)`：§7.6 全字段 `transport_family: str = ""`, `context_window: int = 0`, `max_output_tokens: int = 0`, `supports_tools: bool = False`, `supports_parallel_tools: bool = False`, `supports_reasoning: bool = False`, `reasoning_replay_mode: str = ""`, `supports_vision: bool = False`, `supports_documents: bool = False`, `supports_structured_output: bool = False`, `supports_stream_usage: bool = False`, `supports_server_compaction: bool = False`, `resumable_terminal_reasons: List[str] = []`.
  - `class CapabilitySnapshot(BaseModel)`：`capabilities: ModelCapabilities`, `source: CapabilitySource`, `source_version: str = ""`, `observed_at: str = ""`（运行时记录 ISO 时间，非 reducer）。
  - `class CapabilityRegistry`：`register_provider_defaults(provider, caps)`, `register_family(provider, family, caps)`, `register_exact(model_id, caps)`, `resolve_capabilities(provider, family, model) -> Tuple[ModelCapabilities, CapabilitySource]`（覆盖优先级 exact → family → provider_default；无则 `ModelCapabilities()` + provider_default）。内置默认表覆盖现有 provider（openai/gpt-*, deepseek, kimi/moonshot, minimax, qwen/dashscope）。
  - `def require(model, *flags: str) -> bool`：能力断言辅助（供调用方用，不散落 startswith）。

- [ ] **Step 1: 写失败测试**

```python
import pytest

from floodmind.agent.native.capabilities import (
    CapabilityRegistry, CapabilitySource, ModelCapabilities,
)


def test_layered_resolution_precedence():
    reg = CapabilityRegistry()
    reg.register_provider_defaults("deepseek", ModelCapabilities(supports_reasoning=True))
    reg.register_family("deepseek", "deepseek-reasoner", ModelCapabilities(supports_reasoning=True, context_window=64000))
    reg.register_exact("deepseek-chat", ModelCapabilities(context_window=128000))
    caps, src = reg.resolve_capabilities("deepseek", "deepseek-reasoner", "deepseek-reasoner")
    assert caps.context_window == 64000 and src == CapabilitySource.family
    caps2, src2 = reg.resolve_capabilities("deepseek", "deepseek-reasoner", "deepseek-chat")
    assert caps2.context_window == 128000 and src2 == CapabilitySource.exact
    caps3, src3 = reg.resolve_capabilities("deepseek", "unknown", "deepseek-unknown")
    assert src3 == CapabilitySource.provider_default


def test_capability_snapshot_records_source_and_version():
    reg = CapabilityRegistry()
    reg.register_provider_defaults("kimi", ModelCapabilities(supports_tools=True))
    snap = reg.snapshot("kimi", "moonshot", "kimi-k2", source_version="v1", observed_at="2026-08-11T00:00:00Z")
    assert snap.capabilities.supports_tools is True
    assert snap.source == CapabilitySource.provider_default
    assert snap.source_version == "v1"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_capabilities.py -v`
Expected: FAIL（`capabilities` 模块不存在）

- [ ] **Step 3: 实现**（`floodmind/agent/native/capabilities.py`）

```python
"""ModelCapabilities（目标 §7.6）：数据化能力 + 分层覆盖，禁止 startswith 能力判断散落。"""

from enum import Enum
from typing import Dict, List, Tuple

from pydantic import BaseModel


class CapabilitySource(str, Enum):
    provider_default = "provider_default"
    family = "family"
    exact = "exact"
    discovery = "discovery"


class ModelCapabilities(BaseModel):
    transport_family: str = ""
    context_window: int = 0
    max_output_tokens: int = 0
    supports_tools: bool = False
    supports_parallel_tools: bool = False
    supports_reasoning: bool = False
    reasoning_replay_mode: str = ""
    supports_vision: bool = False
    supports_documents: bool = False
    supports_structured_output: bool = False
    supports_stream_usage: bool = False
    supports_server_compaction: bool = False
    resumable_terminal_reasons: List[str] = []


class CapabilitySnapshot(BaseModel):
    capabilities: ModelCapabilities
    source: CapabilitySource
    source_version: str = ""
    observed_at: str = ""


class CapabilityRegistry:
    """provider defaults → family override → exact override → (runtime discovery 预留)。"""

    def __init__(self):
        self._provider_defaults: Dict[str, ModelCapabilities] = {}
        self._family: Dict[Tuple[str, str], ModelCapabilities] = {}
        self._exact: Dict[str, ModelCapabilities] = {}

    def register_provider_defaults(self, provider: str, caps: ModelCapabilities) -> None:
        self._provider_defaults[provider] = caps

    def register_family(self, provider: str, family: str, caps: ModelCapabilities) -> None:
        self._family[(provider, family)] = caps

    def register_exact(self, model_id: str, caps: ModelCapabilities) -> None:
        self._exact[model_id] = caps

    def resolve_capabilities(self, provider: str, family: str, model: str) -> Tuple[ModelCapabilities, CapabilitySource]:
        if model in self._exact:
            return self._exact[model], CapabilitySource.exact
        if (provider, family) in self._family:
            return self._family[(provider, family)], CapabilitySource.family
        if provider in self._provider_defaults:
            return self._provider_defaults[provider], CapabilitySource.provider_default
        return ModelCapabilities(), CapabilitySource.provider_default

    def snapshot(self, provider: str, family: str, model: str, *, source_version: str = "", observed_at: str = "") -> CapabilitySnapshot:
        caps, source = self.resolve_capabilities(provider, family, model)
        return CapabilitySnapshot(capabilities=caps, source=source, source_version=source_version, observed_at=observed_at)


def _default_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.register_provider_defaults("openai", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_parallel_tools=True,
        supports_reasoning=False, supports_vision=True, supports_structured_output=True,
        supports_stream_usage=True))
    reg.register_family("openai", "o", ModelCapabilities(supports_reasoning=True, reasoning_replay_mode="none"))
    reg.register_provider_defaults("deepseek", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_reasoning=True,
        reasoning_replay_mode="think_tags"))
    reg.register_provider_defaults("kimi", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_reasoning=False))
    reg.register_provider_defaults("minimax", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_parallel_tools=True))
    reg.register_provider_defaults("dashscope", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_reasoning=True,
        reasoning_replay_mode="think_tags"))
    return reg


_DEFAULT_REGISTRY: CapabilityRegistry | None = None


def default_registry() -> CapabilityRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = _default_registry()
    return _DEFAULT_REGISTRY
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_capabilities.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add floodmind/agent/native/capabilities.py tests/test_capabilities.py
git commit -m "feat(model): ModelCapabilities data model + layered resolution (7.6)"
```

---

### Task 2: Canonical Part 事件 + ProviderCodec（§7.3/§7.5）

**Files:**
- Create: `floodmind/agent/runtime/contracts/canonical_parts.py`
- Create: `floodmind/agent/native/providers/codec.py`（基类）
- Modify: `floodmind/agent/native/providers/base.py`（`ProviderPipeline` 改名为 `ProviderCodec`，别名消除）
- Modify: `floodmind/agent/native/providers/__init__.py`（`route_pipeline` → `route_codec`）
- Test: `tests/test_canonical_parts.py`, `tests/test_codec.py`

**Interfaces:**
- Consumes: `canonical_json`。
- Produces:
  - `canonical_parts.py`：
    - `PART_EVENT_TYPES = ["response_start", "part_start", "text_delta", "reasoning_delta", "tool_call_delta", "part_end", "usage", "response_end", "error"]`.
    - `PART_TYPES = ["text", "reasoning_summary", "provider_reasoning", "tool_call", "refusal", "compaction", "provider_extension"]`.
    - `class CanonicalPart(BaseModel)`：`event: str`, `kind: str = ""`, `index: int = 0`, `text: str = ""`, `name: str = ""`, `arguments: str = ""`, `arguments_sha256: str = ""`, `raw: dict = {}`（provider 原生块保留，§7.5 provider_extension / replay）。
  - `codec.py`：
    - `class ProviderCodec`：`name`, `match(cls, provider_id, model_id, base_url) -> int`（沿用现有路由分数）, `encode_request(params, *, enable_thinking, stream) -> dict`, `decode_chunk(raw_chunk) -> Iterator[CanonicalPart]`（Provider Raw Event → Canonical Parts）, `decode_message(message) -> Tuple[str, str, dict]`（content, reasoning, usage）, `extract_usage(raw) -> Optional[Dict]`。
    - `ProviderCodec` 基类实现通用 OpenAI Chat 语义；各 provider 子类覆盖方言（reasoning 标签、usage 字段、原生块）。

- [ ] **Step 1: 写失败测试**（`tests/test_codec.py`）

```python
from floodmind.agent.native.providers.codec import ProviderCodec
from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


class _FakeChunk:
    """模拟一个 tool_calls arguments 跨多个 chunk 的 OpenAI 块。"""
    def __init__(self, delta, finish_reason=None, usage=None, choices=True):
        self.choices = [type("C", (), {"delta": delta, "finish_reason": finish_reason})()] if choices else []


def test_codec_decodes_chunk_to_canonical_parts():
    codec = ProviderCodec(name="openai")
    chunk = _FakeChunk(delta=type("D", (), {"content": "hi", "reasoning_content": None, "tool_calls": None})())
    parts = list(codec.decode_chunk(chunk))
    assert any(p.event == "text_delta" and p.text == "hi" for p in parts)


def test_codec_preserves_raw_for_replay():
    codec = ProviderCodec(name="openai")
    chunk = _FakeChunk(delta=type("D", (), {"content": "x", "reasoning_content": None, "tool_calls": None})())
    parts = list(codec.decode_chunk(chunk))
    assert parts[0].raw != {}  # provider 原生块保留（§7.5 replay）
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_codec.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`canonical_parts.py`：

```python
"""Canonical Part 事件与类型（目标 §7.5）。纯数据层。"""

from typing import Dict, List

from pydantic import BaseModel

PART_EVENT_TYPES: List[str] = ["response_start", "part_start", "text_delta", "reasoning_delta",
                               "tool_call_delta", "part_end", "usage", "response_end", "error"]
PART_TYPES: List[str] = ["text", "reasoning_summary", "provider_reasoning", "tool_call",
                         "refusal", "compaction", "provider_extension"]


class CanonicalPart(BaseModel):
    event: str                       # PART_EVENT_TYPES 之一
    kind: str = ""                   # PART_TYPES 之一
    index: int = 0
    text: str = ""
    name: str = ""
    arguments: str = ""
    arguments_sha256: str = ""
    raw: Dict = {}                   # provider 原生块（replay/审计保留）
```

`codec.py`（基类，通用 OpenAI Chat 语义）：

```python
"""ProviderCodec（目标 §7.3）：Canonical Request↔Provider、Provider Raw Event→Canonical Parts。

由原 ProviderPipeline 收敛重命名而来：职责不变，接口按 §7.3 对齐。
"""

import json
from typing import Any, Dict, Iterator, List, Optional, Tuple

from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


class ProviderCodec:
    name: str = "openai"

    def __init__(self, name: str = "openai"):
        self.name = name

    @classmethod
    def match(cls, provider_id: str, model_id: str, base_url: str) -> int:
        return 0

    def encode_request(self, params: Dict[str, Any], *, enable_thinking: bool = False, stream: bool = False) -> Dict[str, Any]:
        """Canonical Request → Provider Request（厂商方言参数由子类 setdefault 注入）。"""
        return dict(params)

    def decode_chunk(self, raw_chunk: Any) -> Iterator[CanonicalPart]:
        """Provider Raw Event → Canonical Parts（保留原生块 raw）。"""
        if raw_chunk is None or not getattr(raw_chunk, "choices", None):
            yield CanonicalPart(event="error", text="no choices")
            return
        choice = raw_chunk.choices[0]
        delta = getattr(choice, "delta", None) or type("D", (), {})()
        if getattr(delta, "content", None):
            yield CanonicalPart(event="text_delta", kind="text", text=str(delta.content),
                                raw=_asdict(raw_chunk))
        reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if reasoning:
            yield CanonicalPart(event="reasoning_delta", kind="provider_reasoning",
                                text=str(reasoning), raw=_asdict(raw_chunk))
        tool_calls = getattr(delta, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                func = getattr(tc, "function", None) or type("F", (), {})()
                yield CanonicalPart(
                    event="tool_call_delta", kind="tool_call", index=int(getattr(tc, "index", 0)),
                    name=getattr(func, "name", "") or "", arguments=getattr(func, "arguments", "") or "",
                    raw=_asdict(raw_chunk))
        usage = getattr(raw_chunk, "usage", None)
        if usage is not None:
            yield CanonicalPart(event="usage", kind="text",
                                text=json.dumps(_usage_dict(usage), ensure_ascii=False),
                                raw=_asdict(raw_chunk))
        finish = getattr(choice, "finish_reason", None)
        if finish is not None:
            yield CanonicalPart(event="response_end", kind="text", text=str(finish),
                                raw=_asdict(raw_chunk))

    def decode_message(self, message: Any) -> Tuple[str, str, dict]:
        content = str(getattr(getattr(message, "content", None), "content", "") or getattr(message, "content", "") or "")
        reasoning = ""
        usage = {}
        return content, reasoning, usage

    def extract_usage(self, raw: Any) -> Optional[Dict[str, int]]:
        usage = getattr(raw, "usage", None)
        return _usage_dict(usage) if usage is not None else None


def _asdict(raw: Any) -> dict:
    try:
        return getattr(raw, "model_dump", lambda: {})() if hasattr(raw, "model_dump") else vars(raw)
    except Exception:
        return {}


def _usage_dict(usage: Any) -> dict:
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
```

（各 provider 子类：dashscope/kimi/deepseek/minimax 从现有 pipeline 迁移 `match`/方言逻辑到 `ProviderCodec` 子类，`__init__.py` 的 `route_pipeline` 改 `route_codec` 返回对应 Codec 实例。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_codec.py tests/test_canonical_parts.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`（`route_pipeline`→`route_codec` 重命名可能波及调用点，forward-only 更新调用方）

Expected: 全量绿

- [ ] **Step 6: 提交**

```bash
git add floodmind/agent/runtime/contracts/canonical_parts.py floodmind/agent/native/providers/codec.py floodmind/agent/native/providers/base.py floodmind/agent/native/providers/__init__.py floodmind/agent/native/model_client.py tests/test_codec.py tests/test_canonical_parts.py
git commit -m "feat(model): Canonical Part events + ProviderCodec (pipeline convergence, 7.3/7.5)"
```

---

### Task 3: ModelTransport + OpenAIChatTransport + Retry Advice（§7.2/§7.7）

**Files:**
- Create: `floodmind/agent/native/transport.py`
- Modify: `floodmind/agent/native/model_client.py`（`self._client` → transport）
- Test: `tests/test_transport.py`

**Interfaces:**
- Consumes: `ProviderCodec`（Task 2）。
- Produces:
  - `class TransportRetryAdvice(BaseModel)`：`retry_suggested: bool = False`, `retry_after: float = 0.0`, `response_started: bool = False`, `replay_safe: bool = False`, `normalized_error: str = ""`.
  - `class RawResponse`：`def chunks() -> Iterator[Any]`（provider 原始块流）。
  - `class ModelTransport(Protocol)`：`def send(self, request: dict) -> RawResponse`, `def classify_error(self, exc) -> TransportRetryAdvice`.
  - `class OpenAIChatTransport(ModelTransport)`：包装 `openai.OpenAI`；`send` 调 `client.chat.completions.create(**request)`（stream），抛异常时 `classify_error` 用现有 `is_retryable_error` 判定并填充 Retry Advice。

- [ ] **Step 1: 写失败测试**

```python
from floodmind.agent.native.transport import OpenAIChatTransport, TransportRetryAdvice


def test_classify_error_maps_retryable():
    t = OpenAIChatTransport(api_key="k", base_url="http://localhost:1", timeout=1)
    advice = t.classify_error(TimeoutError("timed out"))
    assert advice.retry_suggested is True and advice.response_started is False


def test_classify_error_refusal_not_retryable():
    t = OpenAIChatTransport(api_key="k", base_url="http://localhost:1", timeout=1)
    advice = t.classify_error(RuntimeError("content_filter"))
    assert advice.retry_suggested is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_transport.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**（`floodmind/agent/native/transport.py`）

```python
"""ModelTransport（目标 §7.2/§7.7）：纯 Wire I/O + Retry Advice。"""

from typing import Any, Dict, Iterator, List, Protocol

import httpx
import openai
from pydantic import BaseModel

from floodmind.agent.native.retry import is_retryable_error


class TransportRetryAdvice(BaseModel):
    retry_suggested: bool = False
    retry_after: float = 0.0
    response_started: bool = False
    replay_safe: bool = False
    normalized_error: str = ""


class RawResponse:
    def __init__(self, chunks: Iterator[Any]):
        self._chunks = chunks

    def chunks(self) -> Iterator[Any]:
        return self._chunks


class ModelTransport(Protocol):
    def send(self, request: dict) -> RawResponse: ...
    def classify_error(self, exc: Exception) -> TransportRetryAdvice: ...


class OpenAIChatTransport:
    def __init__(self, api_key: str, base_url: str, timeout: int = 90):
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def send(self, request: dict) -> RawResponse:
        return RawResponse(self._client.chat.completions.create(**request))

    def classify_error(self, exc: Exception) -> TransportRetryAdvice:
        if is_retryable_error(exc):
            return TransportRetryAdvice(retry_suggested=True, response_started=False,
                                        normalized_error=str(exc))
        return TransportRetryAdvice(retry_suggested=False, normalized_error=str(exc))
```

`model_client.py`：`__init__` 用 `OpenAIChatTransport` 替代 `self._client`；`chat`/`stream_chat` 的 `self._client.chat.completions.create(...)` 改为 `self._transport.send(request)`。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_transport.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: 全量绿

- [ ] **Step 6: 提交**

```bash
git add floodmind/agent/native/transport.py floodmind/agent/native/model_client.py tests/test_transport.py
git commit -m "feat(model): ModelTransport + OpenAIChatTransport + Retry Advice (7.2/7.7)"
```

---

### Task 4: ResponsePipeline 状态化组装（§7.4）

**Files:**
- Create: `floodmind/agent/native/response_pipeline.py`
- Modify: `floodmind/agent/native/model_client.py`（`stream_chat` 内联累积逻辑移出）
- Test: `tests/test_response_pipeline.py`

**Interfaces:**
- Consumes: `CanonicalPart`（Task 2）、`ToolCall`/`InvalidToolCall`/`TerminalReason`（native/types）。
- Produces:
  - `class ResponsePipeline`：`accumulate(part: CanonicalPart)`, `finalize() -> List[ToolCall|InvalidToolCall]`, `assistant_snapshot() -> dict`, `cumulative_usage() -> dict`, `terminal_reason() -> TerminalReason`, `is_partial_attempt() -> bool`, `reset()`。
  - 语义：indexed tool delta 累积；malformed JSON → `InvalidToolCall`（保留 raw arguments）；raw 参数保留；assistant 完整 replay snapshot；最终累积 usage；终态判定（refusal/filter/max_tokens 不误判 completed）。

- [ ] **Step 1: 写失败测试**

```python
from floodmind.agent.native.response_pipeline import ResponsePipeline
from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


def test_tool_call_delta_accumulation_across_chunks():
    rp = ResponsePipeline()
    rp.accumulate(CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Read", arguments='{"path"'))
    rp.accumulate(CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="", arguments=': "/a"}'))
    calls, invalids = rp.finalize()
    assert len(calls) == 1 and calls[0].name == "Read" and calls[0].arguments == {"path": "/a"}


def test_malformed_json_is_invalid_tool_call_not_executable():
    rp = ResponsePipeline()
    rp.accumulate(CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Write", arguments="{not-json"))
    calls, invalids = rp.finalize()
    assert calls == [] and len(invalids) == 1 and invalids[0].raw_arguments == "{not-json"


def test_terminal_reason_refusal_not_completed():
    rp = ResponsePipeline()
    rp.accumulate(CanonicalPart(event="response_end", kind="text", text="content_filter"))
    assert rp.terminal_reason().code != "completed"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_response_pipeline.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**（`floodmind/agent/native/response_pipeline.py`）

```python
"""ResponsePipeline（目标 §7.4）：Provider-neutral 状态化组装。"""

import json
from typing import Dict, List, Optional, Tuple

from floodmind.agent.native.types import InvalidToolCall, TerminalReason, ToolCall
from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


class ResponsePipeline:
    def __init__(self):
        self._tool_acc: Dict[int, Dict[str, str]] = {}
        self._completed: List[Dict[str, str]] = []
        self._assistant: Dict[str, object] = {"role": "assistant", "content": ""}
        self._reasoning: List[str] = []
        self._usage: Dict[str, int] = {}
        self._terminal: Optional[str] = None
        self._raw_blocks: List[dict] = []

    def accumulate(self, part: CanonicalPart) -> None:
        if part.raw:
            self._raw_blocks.append(part.raw)
        if part.event == "text_delta":
            self._assistant["content"] = str(self._assistant.get("content", "")) + part.text
        elif part.event == "reasoning_delta":
            self._reasoning.append(part.text)
        elif part.event == "tool_call_delta":
            acc = self._tool_acc.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
            if part.name:
                acc["name"] = part.name
            acc["arguments"] += part.arguments
        elif part.event == "usage":
            try:
                self._usage = json.loads(part.text)
            except (json.JSONDecodeError, TypeError):
                pass
        elif part.event == "response_end":
            self._terminal = part.text

    def _finalize_one(self, acc: Dict[str, str]) -> Tuple[Optional[ToolCall], Optional[InvalidToolCall]]:
        arguments_str = acc.get("arguments", "")
        if not arguments_str:
            return ToolCall(id=acc.get("id") or f"call_{id(acc)}", name=acc.get("name", ""), arguments={}), None
        try:
            parsed = json.loads(arguments_str)
        except (json.JSONDecodeError, TypeError) as exc:
            return None, InvalidToolCall(id=acc.get("id") or "", name=acc.get("name", ""),
                                         raw_arguments=arguments_str, error=f"工具参数不是有效 JSON: {exc}")
        if not isinstance(parsed, dict):
            return None, InvalidToolCall(id=acc.get("id") or "", name=acc.get("name", ""),
                                         raw_arguments=arguments_str, error="工具参数 JSON 必须是对象。")
        return ToolCall(id=acc.get("id") or "", name=acc.get("name", ""), arguments=parsed), None

    def finalize(self) -> Tuple[List[ToolCall], List[InvalidToolCall]]:
        calls, invalids = [], []
        for idx, acc in sorted(self._tool_acc.items()):
            call, invalid = self._finalize_one(acc)
            if call is not None:
                calls.append(call)
            elif invalid is not None:
                invalids.append(invalid)
            self._completed.append(dict(acc))
        self._tool_acc = {}
        return calls, invalids

    def assistant_snapshot(self) -> dict:
        return dict(self._assistant)

    def cumulative_usage(self) -> dict:
        return dict(self._usage)

    def terminal_reason(self) -> TerminalReason:
        return TerminalReason.from_raw(self._terminal)

    def is_partial_attempt(self) -> bool:
        return bool(self._tool_acc) or not self._terminal

    def reset(self) -> None:
        self.__init__()
```

`model_client.py` `stream_chat`：用 `self._pipeline = ResponsePipeline()` 累积 Canonical Part；循环内不再手工拼 tool_call/finish_reason；终态由 `ResponsePipeline` 给出。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_response_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: 全量绿（ModelClient 行为不变，仍输出 ModelEvent 流）

- [ ] **Step 6: 提交**

```bash
git add floodmind/agent/native/response_pipeline.py floodmind/agent/native/model_client.py tests/test_response_pipeline.py
git commit -m "feat(model): ResponsePipeline stateful assembly (7.4) — tool deltas, terminal, usage"
```

---

### Task 5: Retry Orchestrator 决策 + Golden Fixtures（§7.7/§25.2）

**Files:**
- Modify: `floodmind/agent/native/retry.py`（决策函数）
- Modify: `floodmind/agent/native/executor.py`（重试循环用 Retry Advice）
- Create: `tests/test_golden_fixtures.py`, `tests/test_retry_policy.py`

**Interfaces:**
- Consumes: `TransportRetryAdvice`（Task 3）、`TerminalReason`。
- Produces:
  - `def should_retry(advice: TransportRetryAdvice, terminal_reason: Optional[TerminalReason]) -> bool`：`advice.retry_suggested and not terminal_reason 在 {refusal, content_filter, length(max_tokens), pause}` 且（未开始输出 或 replay_safe）。
  - 默认禁自动重试：refusal / filtered / max_tokens / paused（走 continuation）/ 已开始输出且无 replay safety。

- [ ] **Step 1: 写失败测试**（`tests/test_retry_policy.py`）

```python
from floodmind.agent.native.retry import should_retry
from floodmind.agent.native.transport import TransportRetryAdvice


def test_retry_advice_but_refusal_blocks_retry():
    assert should_retry(TransportRetryAdvice(retry_suggested=True), "refusal") is False


def test_retry_advice_connection_error_retries():
    assert should_retry(TransportRetryAdvice(retry_suggested=True, response_started=False), None) is True


def test_started_no_replay_safety_blocks_retry():
    assert should_retry(TransportRetryAdvice(retry_suggested=True, response_started=True, replay_safe=False), None) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_retry_policy.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**（`floodmind/agent/native/retry.py` 追加）

```python
_BLOCKED_TERMINAL = {"refusal", "content_filter", "length", "pause", "paused"}


def should_retry(advice, terminal_reason=None) -> bool:
    """Orchestrator 决策（§7.7）：Transport 只给 Advice，是否重试由这里定。"""
    if not advice.retry_suggested:
        return False
    if terminal_reason and terminal_reason.lower() in _BLOCKED_TERMINAL:
        return False
    if advice.response_started and not advice.replay_safe:
        return False
    return True
```

`executor.py`：LLM 调用失败路径改用 `should_retry(transport.classify_error(exc), terminal_reason)` 判定（替换直接 `is_retryable_error` 判断）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_retry_policy.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: 全量绿

- [ ] **Step 6: 提交**

```bash
git add floodmind/agent/native/retry.py floodmind/agent/native/executor.py tests/test_retry_policy.py
git commit -m "feat(model): retry orchestration — transport advice, orchestrator decides (7.7)"
```

---

### Task 6: Golden Fixtures 验收（§25.2）

**Files:**
- Create: `tests/test_golden_fixtures.py`
- Modify: 视审计结果修正残余引用。

**Interfaces:**
- Consumes: `ResponsePipeline`（Task 4）、`ProviderCodec`（Task 2）。

- [ ] **Step 1: 写失败测试**（`tests/test_golden_fixtures.py`）

```python
"""P4 验收（§25.2 Provider 对应项）。用 Fake chunks 驱动 ResponsePipeline，验证不误判。"""

from floodmind.agent.native.response_pipeline import ResponsePipeline
from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


def _feed(rp, events):
    for e in events:
        rp.accumulate(e)


def test_tool_json_across_arbitrary_chunk_boundaries():
    rp = ResponsePipeline()
    _feed(rp, [
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Read", arguments='{"path":'),
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="", arguments='"/a/'),
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="", arguments='b"}'),
    ])
    calls, invalids = rp.finalize()
    assert len(calls) == 1 and calls[0].arguments == {"path": "/a/b"} and invalids == []


def test_reasoning_and_content_and_tool_in_same_chunk():
    rp = ResponsePipeline()
    _feed(rp, [
        CanonicalPart(event="reasoning_delta", kind="provider_reasoning", text="think"),
        CanonicalPart(event="text_delta", kind="text", text="answer"),
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Bash", arguments="{}"),
        CanonicalPart(event="response_end", kind="text", text="tool_calls"),
    ])
    calls, _ = rp.finalize()
    assert len(calls) == 1 and rp.assistant_snapshot()["content"] == "answer"


def test_parallel_tool_calls_indexed():
    rp = ResponsePipeline()
    _feed(rp, [
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=1, name="Write", arguments='{"x":1}'),
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Read", arguments='{"y":2}'),
    ])
    calls, _ = rp.finalize()
    assert [c.name for c in calls] == ["Read", "Write"]  # 按 index 排序


def test_usage_only_final_chunk_not_misjudged():
    rp = ResponsePipeline()
    _feed(rp, [CanonicalPart(event="usage", kind="text", text='{"total_tokens": 10}')])
    assert rp.terminal_reason().code != "completed"
    assert rp.cumulative_usage()["total_tokens"] == 10


def test_refusal_filter_max_tokens_not_completed():
    for reason in ("refusal", "content_filter", "length"):
        rp = ResponsePipeline()
        rp.accumulate(CanonicalPart(event="response_end", kind="text", text=reason))
        assert rp.terminal_reason().code != "completed"


def test_malformed_arguments_never_executed():
    rp = ResponsePipeline()
    _feed(rp, [CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Write", arguments="{oops")])
    calls, invalids = rp.finalize()
    assert calls == [] and len(invalids) == 1  # 只产生 InvalidToolCall，绝不执行
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_golden_fixtures.py -v`
Expected: FAIL（ResponsePipeline 未完成时）

- [ ] **Step 3: 实现（修正残余引用）**

运行验收测试，对每个失败修正。重点是 `TerminalReason.from_raw` 对 refusal/content_filter/length 的映射不返回 `completed`（若现有映射有误则修正 native/types.py 的 `TerminalReason.from_raw`）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_golden_fixtures.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 全量 + 终审准备**

Run: `python -m pytest -q`
Expected: 全量绿（1041 基线 + 新增）

- [ ] **Step 6: 提交**

```bash
git add tests/test_golden_fixtures.py
git commit -m "test(model): golden fixtures — chunk boundaries, parallel calls, usage, refusal, malformed args (25.2)"
```

---

## 3. 验收（§7.1-§7.7 + §25.2 对应项）

- [ ] ModelTransport 纯 Wire I/O；OpenAI SDK 收进 `OpenAIChatTransport`（Task 3）。
- [ ] ProviderPipeline 收敛为 ProviderCodec，旧名无残留适配器（Task 2）。
- [ ] ResponsePipeline 状态化组装：tool delta 跨 chunk、malformed→InvalidToolCall、raw 保留、assistant replay snapshot、累积 usage、终态（Task 4）。
- [ ] ModelCapabilities 数据化，能力判断不散落 `model.startswith`；分层覆盖 provider→family→exact→discovery（Task 1）。
- [ ] Retry Advice 由 Transport 返回、Orchestrator 决策；refusal/filtered/max_tokens/paused/started-no-replay-safety 默认禁自动重试（Task 5）。
- [ ] §25.2 Golden Fixtures 全绿：Tool JSON 跨 chunk、reasoning+content+tool 同 chunk、并行 tool calls、usage-only final chunk、refusal/filter/max_tokens 不误判 completed、malformed args 永不执行（Task 6）。
- [ ] 全量套件 `python -m pytest -q` 绿。

---

## 4. 交付后的 deferred（供最终评审）

- runtime discovery（真实探测式能力）在本计划以 registry 预留；`CapabilitySource.discovery` 落地待后续。
- Capability Snapshot 的 observed timestamp 记录已支持；持久化到 Journal 作为事件 payload 待后续（P5 Projection Manifest 可接）。
- 原生 Replay Block 的 `provider_extension` 回放链（§7.8 双平面）完整落地待后续；本计划保留 raw 块。
- `ModelClient.invoke/chat` 非流式路径继续走 OpenAI SDK（经 transport），stream_chat 是主路径。
