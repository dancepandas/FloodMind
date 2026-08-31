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


class CompactionOverBudgetError(RuntimeError):
    """Fail-closed：压缩后投影仍超输入预算，且无更多工具输出可裁剪。

    不得静默截断当前用户请求（§9.6 step 10）；检索缩减属 P6，当前先以抛错终止。
    """


# §9.6 摘要来源切到 Canonical Events：产生对话轮次的 journal 事件类型。
# 用于把被压缩中间区映射到 journal 事件（source_event_ids / covered_sequence_* /
# source_sha256 可被 replay 复现）。
_TURN_EVENT_TYPES = frozenset({
    "thread.message.sent",
    "model.attempt.completed",
    "tool.execution.completed",
    "tool.execution.failed",
})

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

    摘要来源切到 Canonical Events（§9.6 注）：source_sha256 对被覆盖内容的 canonical
    投影取 SHA256（有 authority 时来自 journal 事件 payload，可被 replay 复现）；
    covered_sequence_* 为 journal SEQUENCE 范围；source_event_ids 引用实际 journal 事件。
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

    @staticmethod
    def _is_content_message(msg: Dict[str, Any]) -> bool:
        """Wire 消息是否对应 canonical journal turn（system/compaction 块不对应）。

        用于把被压缩中间区映射到 journal 事件（§9.6 摘要来源切到 Canonical Events）：
        system prompt 是宿主注入、[compact] 摘要块是压缩产物，均非原始 journal 事件。
        """
        if not isinstance(msg, dict):
            return False
        if msg.get("role") == "system":
            return False
        if msg.get("compaction"):
            return False
        content = str(msg.get("content", ""))
        if content.startswith("[compact]"):
            return False
        return True

    @classmethod
    def _is_compaction_block(cls, msg: Dict[str, Any]) -> bool:
        """是否为既有压缩块（system + compaction 标记或 [compact] 前缀，对应 Atomic Group compaction_block）。"""
        if not isinstance(msg, dict):
            return False
        if msg.get("role") != "system":
            return False
        if msg.get("compaction"):
            return True
        return str(msg.get("content", "")).startswith("[compact]")

    @classmethod
    def _retain_compaction_blocks(
        cls,
        messages: List[Dict[str, Any]],
        compressed: List[Dict[str, Any]],
        *,
        head_len: int,
        has_new_summary: bool,
    ) -> List[Dict[str, Any]]:
        """F1：把输入中所有既存 compaction 块保留到输出（按内容去重）。

        插到新摘要块之后（即头部 + 新摘要 + 旧摘要块 + 尾部），保证二次压缩不会
        静默丢失首轮压缩历史。已存在于头部/尾部的块按内容去重，不重复插入。
        """
        insert_at = head_len + (1 if has_new_summary else 0)
        existing_contents = {c.get("content") for c in compressed}
        for m in messages:
            if cls._is_compaction_block(m) and m.get("content") not in existing_contents:
                copy = dict(m)
                compressed.insert(insert_at, copy)
                existing_contents.add(copy.get("content"))
                insert_at += 1
        return compressed

    def _journal_covered_events(
        self,
        messages: List[Dict[str, Any]],
        start: int,
        end: int,
        authority: Optional[Any],
    ) -> List[Any]:
        """Best-effort 把被压缩中间区 messages[start:end] 映射到 journal turn 事件。

        计数对齐：head/tail 中可对应 journal turn 的消息数决定偏移，取中间的 turn 事件
        （thread.message.sent / model.attempt.completed / tool.execution.completed|failed）。
        返回空列表表示无法从 journal 复现（authority 缺失或映射为空）。
        """
        if authority is None:
            return []
        events = authority.read_after(0)
        turn_events = [e for e in events if e.event_type in _TURN_EVENT_TYPES]
        if not turn_events:
            return []
        head_backed = sum(1 for m in messages[:start] if self._is_content_message(m))
        tail_backed = sum(1 for m in messages[end:] if self._is_content_message(m))
        covered = turn_events[head_backed:max(head_backed, len(turn_events) - tail_backed)]
        return covered

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
        摘要来源切到 Canonical Events：source_event_ids / covered_sequence_* / source_sha256
        在有 authority 时来自 journal 事件，可被 replay 复现。
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

        # 2) 可压缩段 = 与 [start, end) 窗口有交集的完整段（不拆 Atomic Group）。
        #    head/tail 边界经 _group_start 对齐到 assistant(tool_calls) 组首，但
        #    AtomicGroups 的段边界可能因 compaction/attachment 等单消息组连锁向前
        #    延伸越过头边界；若按整段包含（r0 >= start and r1 <= end）过滤，落在
        #    assistant(tool_calls) 上的组会被整段排除——组内消息不进 head、不进
        #    摘要源、不进 tail，静默丢失。因此改为对窗口求交集：与窗口有任何重叠
        #    的段都整段纳入摘要源（AtomicGroups 语义：同一组不可拆，按整组取）。
        compressible = [
            messages[r0:r1]
            for r0, r1 in ranges
            if r1 > start and r0 < end
        ]

        # 3) 从较早低优先级来源压缩（规则/LLM 摘要引擎）
        summary = (
            self._generate_summary([m for seg in compressible for m in seg])
            if compressible else ""
        )

        # 4) 大工具结果 Artifact Offload
        trimmed = self._trim_tool_outputs(messages)

        # 5) 结构化 Summary Event（源 = Canonical Events，可被 replay 复现）
        covered = self._journal_covered_events(messages, start, end, authority)
        if covered:
            source_event_ids = [e.event_id for e in covered]
            covered_sequence_start = covered[0].sequence
            covered_sequence_end = covered[-1].sequence
            source_text = canonical_json([e.payload for e in covered])
            source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        else:
            # F5：无 journal-backed 覆盖时字段留空，绝不退化为 wire-message 哈希。
            source_event_ids = []
            covered_sequence_start = 0
            covered_sequence_end = 0
            source_sha256 = ""

        summary_event = CompactSummary(
            covered_sequence_start=covered_sequence_start,
            covered_sequence_end=covered_sequence_end,
            source_event_ids=source_event_ids,
            source_sha256=source_sha256,
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

        # 6) 重建压缩后消息：头部（固定保留） + 摘要块 + 尾部（最近完整 Group）。
        #    F1：输入中既有 compaction 块代表此前压缩历史，必须始终保留在输出中，
        #    否则二次压缩会静默丢失首轮压缩的摘要。
        compressed = [dict(m) for m in messages[:start]]
        if summary:
            compressed.append(
                {"role": "system", "content": "[compact] " + summary, "compaction": True}
            )
        compressed.extend(dict(m) for m in trimmed[end:])
        compressed = self._retain_compaction_blocks(
            messages, compressed, head_len=start, has_new_summary=bool(summary)
        )

        # 7) 再次计数：有界扩大 Offload——至多 3 轮渐进收紧工具输出阈值（2000→1000→500），
        #    每轮保留前后缀随阈值等比缩小，保证 3 轮都确实压缩内容而非空转。
        #    仍超限 → fail-closed（绝不静默截断当前用户请求，也绝不返回超预算投影）。
        for trim_limit in (2000, 1000, 500):
            if self._estimate_tokens(compressed) <= limit:
                break
            next_compressed = self._trim_tool_outputs(compressed, max_len=trim_limit)
            if next_compressed == compressed:
                continue  # 本轮阈值下已无可裁剪的工具输出，进入下一轮收紧
            compressed = next_compressed
        if self._estimate_tokens(compressed) > limit:
            raise CompactionOverBudgetError(
                f"compacted projection still over input budget: "
                f"~{self._estimate_tokens(compressed)} tokens > limit {limit}; "
                "refusing to silently truncate the current user request"
            )

        # 8) Summary Event 在最终校验后落 journal（F6）；无 journal-backed 覆盖不落（F5）
        if authority is not None and covered:
            authority.emit("context.compaction.completed", summary_event.model_dump())

        # 9) Projection Manifest（source_type="episode"，transform="compact"）
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

    def _trim_tool_outputs(
        self,
        messages: List[Dict[str, Any]],
        max_len: int = 2000,
    ) -> List[Dict[str, Any]]:
        """
        裁剪工具输出：删除冗长的详细输出，只保留结论。
        对于水文场景，保留关键数值和结论，删除中间过程。

        max_len 为触发裁剪的长度阈值；保留的前后缀随阈值等比缩小
        （各约 max_len/2 减去省略标记余量），保证渐进收紧（2000→1000→500）
        时每轮都确实缩短，不会出现"裁剪后长度不再变化"的空转轮。
        """
        trimmed = []
        for msg in messages:
            if msg.get("role") == "tool" or msg.get("role") == "function":
                content = msg.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
                if len(content) > max_len:
                    # 前后缀各取约阈值一半，预留省略标记长度，确保结果严格短于原内容
                    half = max(1, (max_len - 64) // 2)
                    prefix = content[:half]
                    suffix = content[-half:]
                    content = f"{prefix}\n\n... [中间 {len(content) - 2 * half} 字符已省略] ...\n\n{suffix}"
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
