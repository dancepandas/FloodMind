"""
Context Compression — 上下文压缩

借鉴 Hermes agent/context_compressor.py，针对 FloodMind 水文场景定制。
用辅助模型（轻量/快速）压缩对话中间轮次，保留头尾完整上下文。

设计原则：
  - 严格保护头尾上下文（用户最初需求 + 最新调整）
  - Handoff Prefix 防止 LLM 把摘要当指令执行
  - 工具输出裁剪前置：先删除冗长输出，再压缩
  - 迭代更新：已有摘要时增量压缩
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.runtime.contracts.projection import ProjectionManifest

logger = logging.getLogger(__name__)

# ── Handoff Prefix（必须）─────────────────────────────────────────
# 明确告知 LLM：这是背景参考，不是活跃指令
SUMMARY_PREFIX = (
    "[上下文压缩 — 仅作参考] 以下是对早期对话的摘要。"
    "请勿执行其中提到的任何请求，它们已被处理完成。"
    "请仅响应此摘要之后最新的用户消息。"
    "如果最新消息与'活跃任务'一致，可将摘要作为背景参考；"
    "如果最新消息与摘要中的任务不同，请以最新消息为准，忽略摘要中的旧任务。"
    "\n\n"
)

# ── 摘要提示词 ────────────────────────────────────────────────────
COMPRESSION_PROMPT = """你是一个上下文压缩助手。你的任务是将一段对话历史压缩成结构化摘要。

规则：
1. 保留所有关键决策、用户要求、工具调用结果和错误信息
2. 删除冗余的推理过程、重复确认和无关寒暄
3. 用简洁的列表形式输出
4. 不要输出任何建议或下一步行动（这些属于 LLM 的职责，不是你的）

输出格式（必须严格遵循）：
## 已完成的任务
- [任务1]: [结果简述]
- [任务2]: [结果简述]

## 活跃任务（如未完结）
- [任务]: [当前进度]

## 关键决策/约束
- [决策1]: [简述]

## 遇到的错误
- [错误1]: [简述及解决状态]

## 生成的产物
- [产物1]: [路径/名称]

以下是对话历史：
"""


class CompactSummary(BaseModel):
    """结构化 Summary Event 载荷（目标 §9.5 全字段）。

    摘要来源切到 Canonical Events：source_sha256 对被覆盖的源消息的 canonical_json
    取 SHA256；covered_sequence_* 来自边界；unresolved_transactions 来自未决事务 id。
    """

    covered_sequence_start: int = 0
    covered_sequence_end: int = 0
    source_event_ids: List[str] = []
    source_sha256: str = ""
    summary: str = ""
    completed_facts: List[str] = []
    open_commitments: List[str] = []
    decisions: List[str] = []
    files_and_symbols: List[str] = []
    tool_side_effects: List[str] = []
    artifacts: List[str] = []
    unresolved_transactions: List[str] = []
    recipe_version: str = "1"


@dataclass
class CompressionResult:
    """压缩结果"""

    original_messages: List[Dict[str, Any]]
    compressed_messages: List[Dict[str, Any]]
    summary: str
    saved_tokens: int = 0
    compressed_messages_count: int = 0
    manifest: Optional[ProjectionManifest] = None


class ContextCompressor:
    """
    上下文压缩器。

    使用方式：
        compressor = ContextCompressor(model_client=lightweight_client)
        result = compressor.compress(messages, max_tokens=12000)
        # result.compressed_messages 即为压缩后的消息列表
    """

    def __init__(
        self,
        model_client: Optional[ModelClient] = None,
        head_keep: int = 2,      # 保留头部消息数（system + 前几轮）
        tail_keep: int = 4,      # 保留尾部消息数（最近几轮）
        trigger_threshold: float = 0.75,  # 触发压缩的上下文比例（如 0.75 = 75%）
    ):
        self.model_client = model_client
        self.head_keep = head_keep
        self.tail_keep = tail_keep
        self.trigger_threshold = trigger_threshold
        self._last_summary: Optional[str] = None
        self._summary_coverage: Optional[Tuple[int, int, str]] = None

    def should_compress(self, messages: List[Dict[str, Any]], max_context_tokens: int) -> bool:
        """判断是否需要压缩"""
        if len(messages) <= self.head_keep + self.tail_keep + 2:
            return False

        estimated = self._estimate_tokens(messages)
        ratio = estimated / max_context_tokens
        logger.debug("[Compressor] estimated=%d, max=%d, ratio=%.2f", estimated, max_context_tokens, ratio)
        return ratio >= self.trigger_threshold

    # ── 工具调用原子组对齐 ────────────────────────────────────────

    @staticmethod
    def _group_start(messages: List[Dict[str, Any]], i: int) -> int:
        """返回索引 i 所在工具调用原子组的首条消息索引（自身即组首）。

        原子组 = assistant(tool_calls) + 紧随其后的连续 tool 结果。
        组首 = assistant(tool_calls) 消息，或不带 tool_calls 的消息。
        """
        if i <= 0:
            return 0
        role = messages[i].get("role")
        if role != "tool":
            return i
        # tool 消息：向前扫描连续的 tool 消息
        j = i - 1
        while j > 0 and messages[j].get("role") == "tool":
            j -= 1
        # j 现在指向第一个非 tool 消息，应为 assistant(tool_calls)
        if messages[j].get("role") == "assistant" and messages[j].get("tool_calls"):
            return j
        return j  # 无配对 assistant（异常），返回最后一个 tool 的位置

    def _aligned_split_points(self, messages: List[Dict[str, Any]]) -> tuple:
        """计算对齐到工具调用原子组边界的 head_end / tail_start。

        保证 assistant(tool_calls) 与其 tool 结果不被切开（否则厂商校验报
        tool id not found）。两个切分点都前移到所在组的首条消息。

        head 保证至少保留到首条 user 消息（若 head_keep 落在此前）。
        """
        n = len(messages)
        head_end = min(self.head_keep, n)

        # head 至少保留到首条 user 消息（含），避免把用户最初需求切进摘要
        for i, msg in enumerate(messages[: self.head_keep + 3]):
            if msg.get("role") == "user":
                head_end = max(head_end, i + 1)
                break

        head_end = self._group_start(messages, head_end) if head_end < n else n

        tail_start = max(n - self.tail_keep, head_end)
        if tail_start < n:
            tail_start = max(self._group_start(messages, tail_start), head_end)

        return head_end, tail_start

    def compress(
        self,
        messages: List[Dict[str, Any]],
        max_context_tokens: int = 32000,
    ) -> CompressionResult:
        """
        压缩消息列表。

        策略：
        1. 保留头部消息（system + 前 head_keep 轮）
        2. 保留尾部消息（最近 tail_keep 轮）
        3. 中间部分：先裁剪工具输出，再用辅助模型生成摘要
        4. 如果已有摘要，增量更新而非重新生成
        """
        if not self.should_compress(messages, max_context_tokens):
            return CompressionResult(
                original_messages=messages,
                compressed_messages=messages,
                summary="",
                saved_tokens=0,
            )

        head_end, tail_start = self._aligned_split_points(messages)

        head = messages[:head_end]
        tail = messages[tail_start:]
        middle = messages[head_end:tail_start]

        if not middle:
            return CompressionResult(
                original_messages=messages,
                compressed_messages=messages,
                summary="",
                saved_tokens=0,
            )

        # 1. 裁剪工具输出
        trimmed_middle = self._trim_tool_outputs(middle)

        # 2. 仅当本次 middle 严格延续上次已摘要范围时增量更新。
        # 对历史插入、删除、改写或回退到较短范围，一律重建，避免把不相干摘要串入。
        previous_coverage = self._summary_coverage
        can_increment = bool(
            self._last_summary
            and previous_coverage
            and self._is_strict_continuation(messages, head_end, tail_start, previous_coverage)
        )
        if can_increment:
            _, previous_end, _ = previous_coverage
            newly_covered = self._trim_tool_outputs(messages[previous_end:tail_start])
            summary = self._incremental_summary(newly_covered, self._last_summary or "")
        else:
            summary = self._generate_summary(trimmed_middle)

        self._last_summary = summary
        self._summary_coverage = (
            head_end,
            tail_start,
            self._coverage_digest(messages[head_end:tail_start]),
        )

        # 3. 组装压缩后的消息
        summary_message = {
            "role": "system",
            "content": SUMMARY_PREFIX + summary,
        }
        compressed = head + [summary_message] + tail

        original_tokens = self._estimate_tokens(messages)
        compressed_tokens = self._estimate_tokens(compressed)
        saved = max(0, original_tokens - compressed_tokens)

        logger.info(
            "[Compressor] messages: %d -> %d (head=%d, summary=1, tail=%d), saved ~%d tokens",
            len(messages), len(compressed), len(head), len(tail), saved,
        )

        return CompressionResult(
            original_messages=messages,
            compressed_messages=compressed,
            summary=summary,
            saved_tokens=saved,
        )

    def compress_journal(
        self,
        messages: List[Dict[str, Any]],
        authority: Optional[Any] = None,
        *,
        capabilities: Optional[Any] = None,
        budget: Optional[Any] = None,
        pending_transaction_ids: Optional[List[str]] = None,
        max_context_tokens: Optional[int] = None,
    ) -> CompressionResult:
        """§9.6 Journal-backed Compact：Atomic Groups + 结构化 Summary Event + Manifest。

        只 append ``context.compaction.completed`` 到 journal，绝不修改原始事件；
        可压缩段不拆任何 Atomic Group；仍超限时扩大 Offload，绝不静默截断当前用户请求。
        """
        from floodmind.agent.native.atomic_groups import AtomicGroups
        from floodmind.agent.native.projection import build_manifest, compute_input_budget
        from floodmind.agent.runtime.contracts.canonical_events import canonical_json
        from floodmind.agent.runtime.contracts.projection import InputBudget, ProjectionSource

        pending_transaction_ids = pending_transaction_ids or []
        budget = budget or (compute_input_budget(capabilities) if capabilities else InputBudget())
        limit = max_context_tokens or budget.effective_input or 1200

        # 1) 固定保留 System/Soul/AGENTS/当前用户请求/未决事务 + 最近完整 Group
        ranges = AtomicGroups().aligned_ranges(
            messages, pending_transaction_ids=pending_transaction_ids
        )
        start, end = self._aligned_split_points(messages)

        # 2) 可压缩段 = 早期低优先级来源（整体位于保留头之前，不拆 Atomic Group）
        compressible = [
            messages[r0:r1]
            for r0, r1 in ranges
            if r1 <= start and (r0, r1) != (0, 0)
        ]

        # 3) 从较早来源压缩（规则/LLM 摘要引擎）
        summary = (
            self._generate_summary([m for seg in compressible for m in seg])
            if compressible else ""
        )

        # 4) 大工具结果 Artifact Offload
        trimmed = self._trim_tool_outputs(messages)

        # 5) 结构化 Summary Event（源 = 被覆盖消息的 canonical 投影）
        source_text = canonical_json([m for seg in compressible for m in seg])
        summary_event = CompactSummary(
            covered_sequence_start=start,
            covered_sequence_end=end,
            source_event_ids=[],
            source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            summary=summary,
            completed_facts=[],
            open_commitments=[],
            decisions=[],
            files_and_symbols=[],
            tool_side_effects=[],
            artifacts=[],
            unresolved_transactions=pending_transaction_ids,
            recipe_version="1",
        )
        if authority is not None:
            authority.emit("context.compaction.completed", summary_event.model_dump())

        # 6) 重建压缩后消息：头部（固定保留） + 摘要块 + 尾部（最近完整 Group）
        compressed = [dict(m) for m in messages[:start]]
        if summary:
            compressed.append(
                {"role": "system", "content": "[compact] " + summary, "compaction": True}
            )
        compressed.extend(dict(m) for m in trimmed[end:])

        # 7) 再次计数；仍超限 → 扩大 Offload，不截断当前用户请求
        if self._estimate_tokens(compressed) > limit:
            compressed = self._trim_tool_outputs(compressed)

        # 8) Projection Manifest（source_type="episode"，transform="compact"）
        manifest = build_manifest(
            model="", codec_version="", capability_snapshot_id="",
            budget=budget,
            sources=[
                ProjectionSource(
                    source_id="", source_type="episode",
                    content_sha256=summary_event.source_sha256,
                    original_tokens=self._estimate_tokens(trimmed),
                    projected_tokens=self._estimate_tokens(compressed),
                    transform="compact", priority=1, selected=True,
                )
            ],
        )

        return CompressionResult(
            original_messages=messages,
            compressed_messages=compressed,
            summary=summary,
            saved_tokens=max(
                0, self._estimate_tokens(messages) - self._estimate_tokens(compressed)
            ),
            compressed_messages_count=len(compressed),
            manifest=manifest,
        )

    def _trim_tool_outputs(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        裁剪工具输出：删除冗长的详细输出，只保留结论。
        对于水文场景，保留关键数值和结论，删除中间过程。
        """
        trimmed = []
        for msg in messages:
            if msg.get("role") == "tool" or msg.get("role") == "function":
                content = msg.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
                if len(content) > 2000:
                    # 保留前 500 字 + 后 500 字，中间用省略号
                    prefix = content[:500]
                    suffix = content[-500:]
                    content = f"{prefix}\n\n... [中间 {len(content) - 1000} 字符已省略] ...\n\n{suffix}"
                    msg = dict(msg)
                    msg["content"] = content
            trimmed.append(msg)
        return trimmed

    def _generate_summary(self, messages: List[Dict[str, Any]]) -> str:
        """用辅助模型生成摘要"""
        if not self.model_client:
            # 无辅助模型时，退化为简单拼接
            return self._fallback_summary(messages)

        try:
            text = self._messages_to_text(messages)
            prompt = COMPRESSION_PROMPT + text

            response = self.model_client.invoke(
                prompt=prompt,
                system_prompt="你是一个专门压缩对话历史的助手。",
                temperature=0.1,
                max_tokens=2048,
            )
            summary = response.content.strip()
            return summary if summary else self._fallback_summary(messages)
        except Exception as e:
            logger.warning("[Compressor] summary generation failed: %s, using fallback", e)
            return self._fallback_summary(messages)

    def _incremental_summary(self, new_messages: List[Dict[str, Any]], previous_summary: str) -> str:
        """基于已有摘要增量更新"""
        if not self.model_client:
            return self._fallback_summary(new_messages, previous_summary)

        try:
            text = self._messages_to_text(new_messages)
            prompt = (
                f"以下是对话的早期摘要：\n\n{previous_summary}\n\n"
                f"以下是新增的对话内容：\n\n{text}\n\n"
                f"请更新摘要，合并新旧信息。保持相同格式，不要遗漏关键信息。"
            )

            response = self.model_client.invoke(
                prompt=prompt,
                system_prompt="你是一个专门压缩对话历史的助手。",
                temperature=0.1,
                max_tokens=2048,
            )
            return response.content.strip()
        except Exception as e:
            logger.warning("[Compressor] incremental summary failed: %s", e)
            return self._fallback_summary(new_messages, previous_summary)

    @staticmethod
    def _fallback_summary(
        messages: List[Dict[str, Any]],
        previous: Optional[str] = None,
    ) -> str:
        """无辅助模型时的降级摘要：简单提取关键信息"""
        lines = []
        if previous:
            lines.append("[早期摘要] " + previous[:500])

        for msg in messages:
            role = msg.get("role", "")
            raw_content = msg.get("content", "")
            content = (
                raw_content
                if isinstance(raw_content, str)
                else json.dumps(raw_content, ensure_ascii=False, sort_keys=True, default=str)
            )[:200]
            if role == "user":
                lines.append(f"用户: {content}")
            elif role == "assistant":
                lines.append(f"助手: {content}")
            elif role in ("tool", "function"):
                lines.append(f"工具结果: {content}")

        return "\n".join(lines[:50])  # 最多 50 行

    @classmethod
    def _coverage_digest(cls, messages: List[Dict[str, Any]]) -> str:
        """Return a stable digest for the exact normalized covered history."""
        payload = cls._normalized_serialization(messages).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def _is_strict_continuation(
        cls,
        messages: List[Dict[str, Any]],
        head_end: int,
        tail_start: int,
        previous: Tuple[int, int, str],
    ) -> bool:
        previous_start, previous_end, previous_digest = previous
        return (
            head_end == previous_start
            and tail_start > previous_end
            and previous_end <= len(messages)
            and cls._coverage_digest(messages[head_end:previous_end]) == previous_digest
        )

    @staticmethod
    def _bounded(value: Any, limit: int) -> Any:
        """Bound summary input without dropping tool structure or identifiers."""
        if isinstance(value, str):
            if len(value) <= limit:
                return value
            half = max(1, limit // 2)
            return value[:half] + f"...[{len(value) - 2 * half} chars omitted]..." + value[-half:]
        if isinstance(value, list):
            items = [ContextCompressor._bounded(item, limit) for item in value[:20]]
            if len(value) > 20:
                items.append({"_omitted_items": len(value) - 20})
            return items
        if isinstance(value, dict):
            items = list(value.items())
            bounded = {
                str(k): ContextCompressor._bounded(v, limit)
                for k, v in items[:50]
            }
            if len(items) > 50:
                bounded["_omitted_fields"] = len(items) - 50
            return bounded
        return value

    @classmethod
    def _normalized_message(cls, message: Dict[str, Any], *, bounded: bool) -> Dict[str, Any]:
        """Normalize the complete message envelope for deterministic serialization."""
        normalized = {str(key): value for key, value in message.items()}
        if bounded:
            normalized = cls._bounded(normalized, 1000)
        return normalized

    @classmethod
    def _normalized_serialization(cls, messages: List[Dict[str, Any]]) -> str:
        normalized = [cls._normalized_message(msg, bounded=False) for msg in messages]
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def _messages_to_text(cls, messages: List[Dict[str, Any]]) -> str:
        """Serialize bounded messages, including structured tool calls/results and IDs."""
        parts = []
        for msg in messages:
            normalized = cls._normalized_message(msg, bounded=True)
            parts.append(json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str))
        return "\n\n".join(parts)

    @classmethod
    def _estimate_tokens(cls, messages: List[Dict[str, Any]]) -> int:
        """Estimate tokens from the complete normalized request-message serialization."""
        serialized = cls._normalized_serialization(messages)
        return int(len(serialized) * 0.6) + len(messages) * 4

    def reset(self) -> None:
        """重置状态（会话切换时）"""
        self._last_summary = None
        self._summary_coverage = None
