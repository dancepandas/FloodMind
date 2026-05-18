"""
双记忆模块

短期记忆 + 长期记忆的对话管理，不依赖 LangChain。
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from agent.runtime.contracts.messages import (
    LLMProtocol,
    Message,
    MessageStore,
    ai_message,
    human_message,
    system_message,
)

logger = logging.getLogger(__name__)

_MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
LONG_TERM_MEMORY_FILE = os.path.join(_MEMORY_DIR, "long_term_memory.md")
CHAT_HISTORY_DIR = os.path.join(_MEMORY_DIR, "chat_history")


class LongTermMemory:
    """长期记忆管理器"""

    def __init__(self, memory_file: Optional[str] = None):
        self.memory_file = memory_file or LONG_TERM_MEMORY_FILE
        self.entries: List[Dict[str, Any]] = []
        self._load()

    def add_entry(self, content: str, category: str = "general", importance: float = 0.5):
        entry = {
            "content": content,
            "category": category,
            "importance": importance,
            "timestamp": datetime.now().isoformat(),
            "access_count": 0,
        }
        self.entries.append(entry)
        self._save()
        logger.info(f"[长期记忆] 添加条目: {content[:50]}... (类别: {category})")

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        if not self.entries:
            return []
        query_terms = set(re.findall(r"[一-龥A-Za-z0-9]{2,}", query.lower()))
        scored = []
        for entry in self.entries:
            content_terms = set(re.findall(r"[一-龥A-Za-z0-9]{2,}", entry["content"].lower()))
            score = len(query_terms & content_terms) / max(len(query_terms), 1)
            score *= entry.get("importance", 0.5)
            scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, entry in scored[:top_k]:
            if score > 0:
                entry["access_count"] = entry.get("access_count", 0) + 1
                results.append(entry)
        self._save()
        return results

    def get_recent(self, n: int = 5) -> List[Dict[str, Any]]:
        return self.entries[-n:]

    def get_by_category(self, category: str) -> List[Dict[str, Any]]:
        return [e for e in self.entries if e.get("category") == category]

    def clear(self):
        self.entries.clear()
        self._save()

    def _load(self):
        if not os.path.exists(self.memory_file):
            self.entries = []
            return
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                self.entries = json.loads(content)
            else:
                self.entries = []
        except Exception as e:
            logger.error(f"[长期记忆] 加载失败: {e}")
            self.entries = []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[长期记忆] 保存失败: {e}")


class ContextCompressor:
    """上下文压缩器"""

    def __init__(self, llm: Optional[Any] = None):
        self.llm = llm

    def compress(self, messages: List[Message], max_tokens: int = 2000) -> str:
        if not messages:
            return ""
        if self.llm is None:
            return self._simple_compress(messages, max_tokens)
        return self._llm_compress(messages, max_tokens)

    def _simple_compress(self, messages: List[Message], max_tokens: int) -> str:
        conv_text = self._messages_to_text(messages)
        estimated_tokens = max(1, int(len(conv_text) / 1.5))
        if estimated_tokens <= max_tokens:
            return conv_text
        ratio = max_tokens / estimated_tokens
        keep_chars = int(len(conv_text) * ratio)
        return conv_text[-keep_chars:]

    def _llm_compress(self, messages: List[Message], max_tokens: int) -> str:
        conv_text = self._messages_to_text(messages)
        prompt = (
            f"请将以下对话压缩为不超过{max_tokens} tokens的摘要，保留关键信息：\n\n"
            f"{conv_text}"
        )
        try:
            response = self.llm.invoke(prompt)
            return response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            logger.error(f"[压缩] LLM压缩失败: {e}")
            return self._simple_compress(messages, max_tokens)

    def _messages_to_text(self, messages: List[Message]) -> str:
        lines = []
        for msg in messages:
            if msg.role == "human":
                lines.append(f"用户: {msg.content}")
            elif msg.role == "ai":
                lines.append(f"助手: {msg.content}")
        return "\n".join(lines)


class DualMemory:
    """双记忆系统：短期记忆 + 长期记忆"""

    COMPRESS_BATCH = 4
    CONTEXT_THRESHOLD = 0.7
    MAX_LONG_TERM_RECALL = 4

    def __init__(
        self,
        session_id: str,
        max_short_term: int = 20,
        max_long_term: int = 100,
        persist_dir: Optional[str] = None,
        llm: Optional[Any] = None,
        context_window: int = 32768,
    ):
        self.session_id = session_id
        self.max_short_term = max_short_term
        self.max_long_term = max_long_term
        self.persist_dir = persist_dir
        self._llm = llm
        self.context_window = context_window

        self._short_term = MessageStore()
        self._long_term_store = MessageStore()
        self.compressed_summary: str = ""
        self.long_term_memory = LongTermMemory()
        self._compressor = ContextCompressor(llm)
        self._lock = threading.Lock()
        self._reasoning_trace: List[Dict[str, Any]] = []

        if persist_dir:
            self._load_from_disk()

        logger.info(
            f"双记忆系统初始化 - 会话: {session_id}, "
            f"短期上限: {max_short_term}, 长期上限: {max_long_term}, "
            f"LLM压缩: {'启用' if llm else '禁用'}"
        )

    def add_user_message(self, content: str) -> None:
        with self._lock:
            self._short_term.add_user_message(content)
            self._extract_long_term_from_message(content)
        logger.debug(f"[记忆] 添加用户消息，短期轮数: {self._current_short_rounds()}")

    def add_ai_message(self, content: str) -> None:
        with self._lock:
            self._short_term.add_ai_message(content)
            self._check_consolidation()
        logger.debug(f"[记忆] 添加AI消息，短期轮数: {self._current_short_rounds()}")

    def add_message(self, message: Message) -> None:
        with self._lock:
            self._short_term.add_message(message)
            self._check_consolidation()

    def add_reasoning(self, reasoning: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        entry = {
            "content": reasoning,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self._reasoning_trace.append(entry)

    def get_reasoning_trace(self) -> List[Dict[str, Any]]:
        return list(self._reasoning_trace)

    def get_short_term_messages(self) -> List[Message]:
        with self._lock:
            return self._short_term.messages

    def get_long_term_messages(self) -> List[Message]:
        with self._lock:
            return self._long_term_store.messages

    def get_all_messages(self) -> List[Message]:
        with self._lock:
            return self._long_term_store.messages + self._short_term.messages

    def get_history_text(self, user_input: Optional[str] = None) -> str:
        parts: List[str] = []
        with self._lock:
            long_term_recall = self._select_long_term_for_context(user_input)
            if long_term_recall:
                parts.append(f"[长期记忆]\n{long_term_recall}")
            if self.compressed_summary.strip():
                parts.append(f"[历史摘要]\n{self.compressed_summary.strip()}")
            if self._short_term.messages:
                recent = self._messages_to_text(self._short_term.messages)
                parts.append(f"[近期对话]\n{recent}")
        return "\n\n".join(parts) if parts else "无历史对话"

    def get_openai_messages(self, system_prompt: Optional[str] = None) -> List[Dict[str, str]]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        with self._lock:
            for msg in self.get_all_messages():
                messages.append({"role": msg.role, "content": msg.content})
        return messages

    def get_chat_history_str(self) -> str:
        with self._lock:
            return self._messages_to_text(self.get_all_messages())

    def get_chat_history(self) -> MessageStore:
        return self._short_term

    def _current_short_rounds(self) -> int:
        return len(self._short_term) // 2

    def _check_consolidation(self) -> None:
        rounds = self._current_short_rounds()
        tokens = self._estimate_total_tokens()
        token_limit = int(self.context_window * self.CONTEXT_THRESHOLD)
        round_overflow = rounds > self.max_short_term
        token_overflow = tokens >= token_limit
        if round_overflow or token_overflow:
            reason = f"轮数={rounds}>{self.max_short_term}" if round_overflow else f"tokens={tokens}>={token_limit}"
            logger.info(f"[记忆] 触发整合 - {reason}")
            self._consolidate()

    def _consolidate(self) -> None:
        short_messages = list(self._short_term.messages)
        keep_recent_rounds = max(4, self.max_short_term // 2)
        compress_count = min(
            self.COMPRESS_BATCH * 2,
            max(0, len(short_messages) - keep_recent_rounds * 2),
        )
        if compress_count < 2:
            logger.warning("[记忆] 消息数不足以整合，跳过")
            return

        to_compress = short_messages[:compress_count]
        remaining = short_messages[compress_count:]

        if self._llm and len(to_compress) >= 4:
            try:
                summary, long_term = self._llm_summarize_and_extract(to_compress)
                self.compressed_summary = (
                    self.compressed_summary + "\n---\n" + summary
                    if self.compressed_summary
                    else summary
                )
                if long_term:
                    self.long_term_memory.add_entry(long_term, category="对话摘要")
            except Exception as e:
                logger.warning(f"[记忆] LLM摘要失败，直接迁移: {e}")
                for msg in to_compress:
                    self._long_term_store.add_message(msg)
        else:
            for msg in to_compress:
                self._long_term_store.add_message(msg)

        self._short_term.clear()
        for msg in remaining:
            self._short_term.add_message(msg)

        if len(self._long_term_store) > self.max_long_term:
            messages = self._long_term_store.messages
            self._long_term_store.clear()
            for msg in messages[-self.max_long_term:]:
                self._long_term_store.add_message(msg)

        if self.persist_dir:
            self._save_to_disk()

        logger.info(
            f"[记忆] 整合完成 - 压缩{compress_count // 2}轮，"
            f"短期保留{len(remaining) // 2}轮，长期{len(self._long_term_store)}条"
        )

    def _llm_summarize_and_extract(self, messages: List[Message]) -> Tuple[str, str]:
        conv_text = self._messages_to_text(messages)
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
            response = self._llm.invoke(prompt)
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

    def _extract_long_term_from_message(self, message: str):
        patterns = [
            r"(默认|通常|习惯|偏好|以后都|总是)[^。！？\n]{0,80}",
            r"(请叫我|我叫)[^。！？\n]{1,20}",
            r"(关注|监控|重点看)[^。！？\n]{1,40}(站|流量|水位)",
        ]
        for pattern in patterns:
            matched = re.search(pattern, message)
            if matched:
                self.long_term_memory.add_entry(
                    f"用户偏好：{matched.group(0).strip()}",
                    category="用户偏好",
                    importance=0.8,
                )
                break

    def _select_long_term_for_context(self, user_input: Optional[str]) -> str:
        entries = self.long_term_memory.entries
        if not entries:
            return ""
        if not user_input:
            selected = entries[-self.MAX_LONG_TERM_RECALL:]
            return "\n".join(f"- {e['content']}" for e in selected)
        results = self.long_term_memory.search(user_input, top_k=self.MAX_LONG_TERM_RECALL)
        if not results:
            selected = entries[-self.MAX_LONG_TERM_RECALL:]
            return "\n".join(f"- {e['content']}" for e in selected)
        return "\n".join(f"- {e['content']}" for e in results)

    def _estimate_tokens(self, text: str) -> int:
        return max(1, int(len(text) / 1.5))

    def _estimate_total_tokens(self) -> int:
        total = self.compressed_summary
        for msg in self._short_term.messages:
            total += str(msg.content)
        return self._estimate_tokens(total)

    def _messages_to_text(self, messages: List[Message]) -> str:
        lines = []
        for msg in messages:
            if msg.role == "human":
                lines.append(f"用户: {msg.content}")
            elif msg.role == "ai":
                lines.append(f"助手: {msg.content}")
        return "\n".join(lines)

    def clear_short_term(self) -> None:
        with self._lock:
            self._short_term.clear()
            self.compressed_summary = ""

    def clear_all(self) -> None:
        with self._lock:
            self._short_term.clear()
            self._long_term_store.clear()
            self.compressed_summary = ""
            self._reasoning_trace.clear()
        if self.persist_dir:
            self._save_to_disk()

    @property
    def short_term_count(self) -> int:
        return len(self._short_term)

    @property
    def long_term_count(self) -> int:
        return len(self._long_term_store)

    def _save_to_disk(self) -> None:
        if not self.persist_dir:
            return
        try:
            os.makedirs(self.persist_dir, exist_ok=True)
            data = {
                "session_id": self.session_id,
                "compressed_summary": self.compressed_summary,
                "short_term": [
                    {"role": m.role, "content": m.content, "additional_kwargs": m.additional_kwargs}
                    for m in self._short_term.messages
                ],
                "long_term": [
                    {"role": m.role, "content": m.content, "additional_kwargs": m.additional_kwargs}
                    for m in self._long_term_store.messages
                ],
                "reasoning_trace": self._reasoning_trace,
            }
            filepath = os.path.join(self.persist_dir, f"memory_{self.session_id}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[记忆] 保存到磁盘失败: {e}")

    def _load_from_disk(self) -> None:
        if not self.persist_dir:
            return
        filepath = os.path.join(self.persist_dir, f"memory_{self.session_id}.json")
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.compressed_summary = data.get("compressed_summary", "")
            for msg_data in data.get("short_term", []):
                self._short_term.add_message(Message(
                    role=msg_data["role"],
                    content=msg_data["content"],
                    additional_kwargs=msg_data.get("additional_kwargs", {}),
                ))
            for msg_data in data.get("long_term", []):
                self._long_term_store.add_message(Message(
                    role=msg_data["role"],
                    content=msg_data["content"],
                    additional_kwargs=msg_data.get("additional_kwargs", {}),
                ))
            self._reasoning_trace = data.get("reasoning_trace", [])
            logger.info(
                f"[记忆] 从磁盘加载: 短期={len(self._short_term)}, "
                f"长期={len(self._long_term_store)}"
            )
        except Exception as e:
            logger.error(f"[记忆] 从磁盘加载失败: {e}")

    def save_chat_history(self, messages: Optional[List[Dict[str, Any]]] = None) -> str:
        """保存聊天历史到文件"""
        os.makedirs(CHAT_HISTORY_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_{self.session_id}_{timestamp}.json"
        filepath = os.path.join(CHAT_HISTORY_DIR, filename)

        if messages is None:
            messages = [
                {"role": msg.role, "content": msg.content}
                for msg in self.get_all_messages()
            ]

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)

        logger.info(f"[记忆] 聊天历史已保存: {filepath}")
        return filepath

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "max_short_term": self.max_short_term,
                "max_long_term": self.max_long_term,
                "short_term_count": len(self._short_term),
                "long_term_count": len(self._long_term_store),
                "has_compressed_summary": bool(self.compressed_summary),
                "short_term": [
                    {"role": m.role, "content": m.content}
                    for m in self._short_term.messages
                ],
                "long_term": [
                    {"role": m.role, "content": m.content}
                    for m in self._long_term_store.messages
                ],
            }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DualMemory':
        memory = cls(
            session_id=data.get("session_id", "unknown"),
            max_short_term=data.get("max_short_term", 20),
            max_long_term=data.get("max_long_term", 100),
        )
        for msg_data in data.get("short_term", []):
            memory._short_term.add_message(Message(
                role=msg_data["role"],
                content=msg_data["content"],
            ))
        for msg_data in data.get("long_term", []):
            memory._long_term_store.add_message(Message(
                role=msg_data["role"],
                content=msg_data["content"],
            ))
        return memory