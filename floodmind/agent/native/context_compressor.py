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

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from floodmind.agent.native.model_client import ModelClient

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


@dataclass
class CompressionResult:
    """压缩结果"""

    original_messages: List[Dict[str, Any]]
    compressed_messages: List[Dict[str, Any]]
    summary: str
    saved_tokens: int = 0


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

    def should_compress(self, messages: List[Dict[str, Any]], max_context_tokens: int) -> bool:
        """判断是否需要压缩"""
        if len(messages) <= self.head_keep + self.tail_keep + 2:
            return False

        estimated = self._estimate_tokens(messages)
        ratio = estimated / max_context_tokens
        logger.debug("[Compressor] estimated=%d, max=%d, ratio=%.2f", estimated, max_context_tokens, ratio)
        return ratio >= self.trigger_threshold

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

        head = messages[:self.head_keep]
        tail = messages[-self.tail_keep:]
        middle = messages[self.head_keep:-self.tail_keep]

        if not middle:
            return CompressionResult(
                original_messages=messages,
                compressed_messages=messages,
                summary="",
                saved_tokens=0,
            )

        # 1. 裁剪工具输出
        trimmed_middle = self._trim_tool_outputs(middle)

        # 2. 生成或增量更新摘要
        if self._last_summary:
            summary = self._incremental_summary(trimmed_middle, self._last_summary)
        else:
            summary = self._generate_summary(trimmed_middle)

        self._last_summary = summary

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

    def _trim_tool_outputs(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        裁剪工具输出：删除冗长的详细输出，只保留结论。
        对于水文场景，保留关键数值和结论，删除中间过程。
        """
        trimmed = []
        for msg in messages:
            if msg.get("role") == "tool" or msg.get("role") == "function":
                content = msg.get("content", "")
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
            content = msg.get("content", "")[:200]
            if role == "user":
                lines.append(f"用户: {content}")
            elif role == "assistant":
                lines.append(f"助手: {content}")
            elif role in ("tool", "function"):
                lines.append(f"工具结果: {content}")

        return "\n".join(lines[:50])  # 最多 50 行

    @staticmethod
    def _messages_to_text(messages: List[Dict[str, Any]]) -> str:
        """将消息列表转换为文本"""
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                # 多模态 content parts
                content = "\n".join(
                    p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                )
            parts.append(f"[{role}] {content[:1000]}")
        return "\n\n".join(parts)

    @staticmethod
    def _estimate_tokens(messages: List[Dict[str, Any]]) -> int:
        """
        粗略估计 Token 数。
        中文按 1 字 ≈ 1 token，英文按 4 字符 ≈ 1 token。
        """
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total_chars += len(part["text"])

        # 粗略估算：混合文本按 1 字符 ≈ 0.6 token
        return int(total_chars * 0.6) + len(messages) * 4  # +4 为消息格式开销

    def reset(self) -> None:
        """重置状态（会话切换时）"""
        self._last_summary = None
