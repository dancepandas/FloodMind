"""
简单记忆模块

基于列表的对话历史管理，不依赖 LangChain。
"""

import logging
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from agent.runtime.contracts.messages import (
    LLMProtocol,
    Message,
    MessageStore,
    ai_message,
    human_message,
)

logger = logging.getLogger(__name__)

_MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
LONG_TERM_MEMORY_FILE = os.path.join(_MEMORY_DIR, "memory.md")


class SimpleMemory:
    COMPRESS_BATCH = 4
    CONTEXT_THRESHOLD = 0.7
    MAX_LONG_TERM_RECALL = 4

    def __init__(self, max_history: int = 20, llm: Optional[Any] = None, context_window: int = 32768):
        self.max_history = max_history
        self.llm = llm
        self.context_window = context_window
        self._store = MessageStore()
        self.compressed_summary: str = ""
        self.long_term_memory: str = ""
        self._lock = threading.Lock()
        self._load_long_term_memory()
        logger.info(f"记忆系统初始化 - 最大短期轮数: {max_history}, LLM压缩: {'启用' if llm else '禁用'}, 上下文预算: {context_window} tokens")

    def add_user_message(self, message: str):
        with self._lock:
            self._store.add_user_message(message)
            self._extract_rule_based_long_term_memory(message)
        logger.debug(f"[记忆] 添加用户消息，当前轮数: {self._current_rounds()}")

    def add_ai_message(self, message: str):
        with self._lock:
            self._store.add_ai_message(message)
            self._check_and_compress()
        logger.debug(f"[记忆] 添加AI消息，当前轮数: {self._current_rounds()}")

    def get_messages(self) -> List[Message]:
        with self._lock:
            return self._store.messages

    def get_history_text(self, user_input: Optional[str] = None) -> str:
        parts: List[str] = []
        with self._lock:
            long_term_recall = self._select_long_term_for_context(user_input)
            if long_term_recall:
                parts.append(f"[长期记忆]\n{long_term_recall}")
            if self.compressed_summary.strip():
                parts.append(f"[历史摘要]\n{self.compressed_summary.strip()}")
            if self._store.messages:
                recent = self._messages_to_text(self._store.messages)
                parts.append(f"[近期对话]\n{recent}")
        return "\n\n".join(parts) if parts else "无历史对话"

    def clear(self):
        with self._lock:
            self._store.clear()
            self.compressed_summary = ""
        logger.info("短期记忆已清空（长期记忆保留）")

    def get_chat_history(self) -> MessageStore:
        return self._store

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            messages = self._store.messages
            long_term_entries = self._split_long_term_entries()
            return {
                "max_history": self.max_history,
                "current_rounds": self._current_rounds(),
                "message_count": len(messages),
                "has_compressed_summary": bool(self.compressed_summary),
                "has_long_term_memory": bool(long_term_entries),
                "long_term_entries": len(long_term_entries),
                "estimated_tokens": self._estimate_total_tokens(),
                "messages": [
                    {
                        "type": "human" if msg.role == "human" else "ai",
                        "content": msg.content,
                    }
                    for msg in messages
                ],
            }

    def _current_rounds(self) -> int:
        return len(self._store.messages) // 2

    def _estimate_tokens(self, text: str) -> int:
        return max(1, int(len(text) / 1.5))

    def _estimate_total_tokens(self) -> int:
        total = self.compressed_summary
        for msg in self._store.messages:
            total += str(msg.content)
        return self._estimate_tokens(total)

    def _check_and_compress(self):
        rounds = self._current_rounds()
        tokens = self._estimate_total_tokens()
        token_limit = int(self.context_window * self.CONTEXT_THRESHOLD)
        round_overflow = rounds > self.max_history
        token_overflow = tokens >= token_limit
        if round_overflow or token_overflow:
            reason = f"轮数={rounds}>{self.max_history}" if round_overflow else f"tokens={tokens}>={token_limit}"
            logger.info(f"[记忆] 触发压缩 - {reason}")
            self._compress()

    def _compress(self):
        msgs = list(self._store.messages)
        keep_recent_rounds = max(4, self.max_history // 2)
        compress_count = min(self.COMPRESS_BATCH * 2, max(0, len(msgs) - keep_recent_rounds * 2))
        if compress_count < 2:
            logger.warning("[记忆] 消息数不足以压缩，跳过")
            return
        to_compress = msgs[:compress_count]
        remaining = msgs[compress_count:]
        conv_text = self._messages_to_text(to_compress)
        if self.llm is not None:
            new_summary, long_term = self._llm_summarize_and_extract(conv_text)
            self.compressed_summary = (
                self.compressed_summary + "\n---\n" + new_summary
                if self.compressed_summary
                else new_summary
            )
            if long_term:
                self._append_long_term_memory(long_term)
        else:
            fallback = f"[已截断 {compress_count // 2} 轮对话（未配置LLM压缩）]"
            self.compressed_summary = (
                self.compressed_summary + "\n---\n" + fallback
                if self.compressed_summary
                else fallback
            )
        self._store.clear()
        for msg in remaining:
            self._store.add_message(msg)
        logger.info(f"[记忆] 压缩完成 - 压缩{compress_count // 2}轮，保留{len(remaining) // 2}轮")

    def _llm_summarize_and_extract(self, conv_text: str) -> tuple:
        llm = self.llm
        if llm is None:
            return f"[摘要生成失败，原始对话{conv_text.count('用户:')}轮]", ""
        prompt = (
            "请基于以下对话，输出两部分：\n"
            "1) 对话摘要，保留任务目标、站点、时间范围、关键数据和结论\n"
            "2) 可长期记忆的用户偏好，仅限稳定偏好（如常用站点、默认预测步长、输出格式）\n\n"
            f"对话内容：\n{conv_text}\n\n"
            "输出格式严格如下：\n"
            "【摘要】\n"
            "...\n"
            "【长期记忆】\n"
            "若无则输出 无"
        )
        try:
            response = llm.invoke(prompt)
            text = response.content if hasattr(response, "content") else str(response)
            summary = text
            long_term = ""
            if "【摘要】" in text and "【长期记忆】" in text:
                summary = text.split("【摘要】", 1)[1].split("【长期记忆】", 1)[0].strip()
                long_term = text.split("【长期记忆】", 1)[1].strip()
            if long_term == "无":
                long_term = ""
            logger.info("[记忆] LLM摘要生成成功")
            return summary or "[摘要为空]", long_term
        except Exception as e:
            logger.error(f"[记忆] LLM摘要失败: {e}")
            return f"[摘要生成失败，原始对话{conv_text.count('用户:')}轮]", ""

    def _append_long_term_memory(self, content: str):
        if not content:
            return
        normalized = content.strip()
        if not normalized:
            return
        if normalized in self.long_term_memory:
            return
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = f"\n## {timestamp}\n{normalized}\n"
            with open(LONG_TERM_MEMORY_FILE, "a", encoding="utf-8") as f:
                f.write(entry)
            self.long_term_memory += entry
            logger.info(f"[记忆] 长期记忆已追加: {normalized[:60]}...")
        except Exception as e:
            logger.error(f"[记忆] 保存长期记忆失败: {e}")

    def _load_long_term_memory(self):
        try:
            if os.path.exists(LONG_TERM_MEMORY_FILE):
                with open(LONG_TERM_MEMORY_FILE, "r", encoding="utf-8") as f:
                    self.long_term_memory = f.read()
                logger.info(f"[记忆] 长期记忆加载成功 - {len(self.long_term_memory)} 字符")
            else:
                self.long_term_memory = ""
                with open(LONG_TERM_MEMORY_FILE, "w", encoding="utf-8") as f:
                    f.write("# 长期记忆\n\n")
                logger.info("[记忆] 长期记忆文件已初始化")
        except Exception as e:
            logger.error(f"[记忆] 加载长期记忆失败: {e}")
            self.long_term_memory = ""

    def _messages_to_text(self, messages: List[Message]) -> str:
        lines = []
        for msg in messages:
            if msg.role == "human":
                lines.append(f"用户: {msg.content}")
            elif msg.role == "ai":
                lines.append(f"助手: {msg.content}")
        return "\n".join(lines)

    def _extract_rule_based_long_term_memory(self, message: str):
        patterns = [
            r"(默认|通常|习惯|偏好|以后都|总是)[^。！？\n]{0,80}",
            r"(请叫我|我叫)[^。！？\n]{1,20}",
            r"(关注|监控|重点看)[^。！？\n]{1,40}(站|流量|水位)",
        ]
        for pattern in patterns:
            matched = re.search(pattern, message)
            if matched:
                self._append_long_term_memory(f"用户偏好：{matched.group(0).strip()}")
                break

    def _split_long_term_entries(self) -> List[str]:
        text = self.long_term_memory.strip()
        if not text:
            return []
        raw_entries = re.split(r"\n##\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\n", "\n" + text)
        entries = [entry.strip() for entry in raw_entries if entry.strip() and not entry.strip().startswith("# 长期记忆")]
        return entries

    def _select_long_term_for_context(self, user_input: Optional[str]) -> str:
        entries = self._split_long_term_entries()
        if not entries:
            return ""
        if not user_input:
            selected = entries[-self.MAX_LONG_TERM_RECALL:]
            return "\n".join(f"- {item}" for item in selected)
        query_terms = set(re.findall(r"[一-龥A-Za-z0-9]{2,}", user_input.lower()))
        scored: List[tuple] = []
        for idx, entry in enumerate(entries):
            terms = set(re.findall(r"[一-龥A-Za-z0-9]{2,}", entry.lower()))
            score = len(query_terms & terms)
            scored.append((score, idx, entry))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        selected = [entry for score, _, entry in scored[:self.MAX_LONG_TERM_RECALL] if score > 0]
        if not selected:
            selected = entries[-self.MAX_LONG_TERM_RECALL:]
        return "\n".join(f"- {item}" for item in selected)