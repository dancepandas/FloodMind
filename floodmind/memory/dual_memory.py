"""
双记忆模块

短期记忆 + 长期记忆的对话管理，不依赖 LangChain。
对话历史统一存储到 persist_dir/chat_history.json，每轮包含：
turn_index, user_input, reasoning, tool_calls, final_answer, timestamp
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from floodmind.agent.runtime.contracts.messages import (
    LLMProtocol,
    Message,
    MessageStore,
    ai_message,
    human_message,
    system_message,
)

logger = logging.getLogger(__name__)

_MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
LONG_TERM_MEMORY_FILE = os.path.join(_MEMORY_DIR, "long_term_memory.json")
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
    """双记忆系统：短期记忆 + 长期记忆 + 对话历史"""

    COMPRESS_BATCH = 4
    CONTEXT_THRESHOLD = 0.7
    MAX_LONG_TERM_RECALL = 4

    # 对话历史压缩参数
    HISTORY_COMPRESS_RATIO = 0.85    # 上下文使用率超过此值触发压缩
    HISTORY_KEEP_RECENT_TURNS = 2    # 压缩时保留最近N轮完整原文

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
        self._lock = threading.RLock()
        self._reasoning_trace: List[Dict[str, Any]] = []

        # 对话历史（按轮次组织）
        self._turns: List[Dict[str, Any]] = []
        self._turn_index: int = 0
        self._history_compressed: bool = False  # 早期轮次是否已压缩

        # 增量 history 缓存
        self._last_sent_turn_index: int = 0
        self._cached_history_text: str = ""

        if persist_dir:
            self._load_from_disk()

        logger.info(
            f"双记忆系统初始化 - 会话: {session_id}, "
            f"短期上限: {max_short_term}, 长期上限: {max_long_term}, "
            f"LLM压缩: {'启用' if llm else '禁用'}"
        )

    def set_llm(self, llm: Any) -> None:
        """注入 LLM 服务（支持延迟注入）"""
        self._llm = llm
        self._compressor.llm = llm

    # ── 消息添加 ──────────────────────────────────────────────

    def add_user_message(self, content: str) -> None:
        with self._lock:
            self._short_term.add_user_message(content)
            self._extract_long_term_from_message(content)
            # 开始新轮次
            self._turns.append({
                "turn_index": self._turn_index,
            "history_compressed": self._history_compressed,
                "user_input": content,
                "reasoning": "",
                "tool_calls": [],
                "final_answer": "",
                "timestamp": datetime.now().isoformat(),
            })
            self._turn_index += 1
        logger.debug(f"[记忆] 添加用户消息，轮次: {self._turn_index - 1}")

    def add_ai_message(self, content: str) -> None:
        with self._lock:
            self._short_term.add_ai_message(content)
            self._check_consolidation()
            # 补充当前轮次的 final_answer
            if self._turns and not self._turns[-1].get("final_answer"):
                self._turns[-1]["final_answer"] = content
        logger.debug(f"[记忆] 添加AI消息，短期轮数: {self._current_short_rounds()}")

    def add_ai_message_with_trace(
        self,
        content: str,
        reasoning: str = "",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """记录完整的 AI 回复（含推理过程和工具调用详情）"""
        with self._lock:
            self._short_term.add_ai_message(content)
            # 更新当前轮次
            if self._turns:
                turn = self._turns[-1]
                turn["final_answer"] = content
                if reasoning:
                    turn["reasoning"] = reasoning
                if tool_calls:
                    turn["tool_calls"] = tool_calls
            self._check_consolidation()
        logger.debug(f"[记忆] 添加AI消息(含trace)，轮次: {self._turn_index - 1}")

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
        with self._lock:
            self._reasoning_trace.append(entry)

    def get_reasoning_trace(self) -> List[Dict[str, Any]]:
        return list(self._reasoning_trace)

    # ── 消息读取 ──────────────────────────────────────────────

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

    def get_turns(self) -> List[Dict[str, Any]]:
        """获取所有对话轮次"""
        with self._lock:
            return list(self._turns)

    def get_chat_history_for_system_prompt(self, total_context_chars: int = 0, context_window: int = 0, event_bus=None) -> str:
        """构建对话历史文本，支持增量拼接，超阈值时全量重建并压缩早期轮次

        Args:
            total_context_chars: 当前系统提示词+memory_messages的总字符数（不含对话历史）
            context_window: 模型上下文窗口大小（tokens），用于判断是否需要压缩
            event_bus: EventBus 实例，用于向前端发送压缩状态事件
        """
        with self._lock:
            if not self._turns:
                return ""

            # 判断是否需要压缩
            need_compress = False
            if context_window > 0 and len(self._turns) > self.HISTORY_KEEP_RECENT_TURNS:
                estimated_tokens = (total_context_chars + len(self._cached_history_text)) / 1.5
                if estimated_tokens / context_window > self.HISTORY_COMPRESS_RATIO:
                    need_compress = True

            if need_compress:
                # 压缩触发：全量重建
                recent = self._turns[-self.HISTORY_KEEP_RECENT_TURNS:]
                older = self._turns[:-self.HISTORY_KEEP_RECENT_TURNS]
                if not older:
                    result = self._build_turns_text(self._turns)
                else:
                    older_summary = self._get_cached_or_compress_older(older, event_bus)
                    lines = ["[对话历史]"]
                    if older_summary:
                        lines.append(f"\n[早期对话摘要]\n{older_summary}\n")
                    lines.append(self._build_turns_text(recent))
                    result = "\n".join(lines)
                self._cached_history_text = result
                last_complete = 0
                for i, t in enumerate(self._turns):
                    if t.get("final_answer") or t.get("compressed"):
                        last_complete = i + 1
                    else:
                        break
                self._last_sent_turn_index = last_complete
                return result

        # 全量重建（确保 final_answer 已填入后再输出）
        result = self._build_turns_text(self._turns)
        self._cached_history_text = result
        self._last_sent_turn_index = len(self._turns)
        return result

    def _build_turns_text(self, turns: List[Dict[str, Any]]) -> str:
        """将轮次列表格式化为文本
        
        注意：如果最后一轮尚未完成（final_answer 为空），则跳过它。
        因为当前轮的用户输入会作为单独的 user message 发送给 LLM，
        包含在历史中会导致重复且破坏缓存前缀。
        """
        # 跳过尾部未完成的当前轮（避免与 msg[3] user message 重复）
        effective_turns = turns
        if turns and not turns[-1].get("final_answer"):
            effective_turns = turns[:-1]
        if not effective_turns:
            return ""
        lines = ["[对话历史]"]
        for turn in effective_turns:
            idx = turn.get("turn_index", 0)
            lines.append(f"\n第{idx}轮:")
            lines.append(f"用户: {turn.get('user_input', '')}")
            if turn.get("reasoning"):
                lines.append(f"思考: {turn['reasoning']}")
            if turn.get("tool_calls"):
                for tc in turn["tool_calls"]:
                    tool_name = tc.get("tool_name", tc.get("name", "unknown"))
                    tool_input = tc.get("tool_input", "")
                    tool_output = tc.get("tool_output", tc.get("result", ""))
                    input_summary = str(tool_input)[:200] if tool_input else ""
                    output_summary = str(tool_output)[:300] if tool_output else ""
                    lines.append(f"  调用 {tool_name}: {input_summary}")
                    lines.append(f"  结果: {output_summary}")
            if turn.get("final_answer"):
                lines.append(f"回答: {turn['final_answer']}")
        return "\n".join(lines)

    def _get_cached_or_compress_older(self, older_turns: List[Dict[str, Any]], event_bus=None) -> str:
        """获取早期轮次的压缩摘要，优先用缓存"""
        if self._history_compressed and all(t.get("compressed") for t in older_turns):
            parts = [t["compressed"] for t in older_turns if t.get("compressed") and not str(t["compressed"]).startswith("[汇总")]
            return "\n".join(parts) if parts else ""

        # 发送压缩开始事件
        if event_bus:
            event_bus.emit_context_compress_start()

        if self._llm:
            summary = self._llm_compress_turns(older_turns)
        else:
            summary = self._rule_compress_turns(older_turns)

        self._distribute_compressed(older_turns, summary)
        self._history_compressed = True
        self.save_chat_history()

        # 发送压缩完成事件
        if event_bus:
            event_bus.emit_context_compress_done(summary)

        return summary

    def _distribute_compressed(self, older_turns: List[Dict[str, Any]], summary: str):
        """将压缩摘要分配到各轮的 compressed 字段"""
        if not older_turns:
            return
        if self._llm:
            # LLM 摘要是整体文本，存到第一轮，其余轮标记汇总于第一轮
            older_turns[0]["compressed"] = summary
            for t in older_turns[1:]:
                t["compressed"] = "[汇总于上一轮]"
        else:
            # 规则压缩逐轮，按行分配
            lines = summary.strip().split("\n")
            for i, turn in enumerate(older_turns):
                turn["compressed"] = lines[i] if i < len(lines) else self._compress_turn_rule(turn)

    def _rule_compress_turns(self, turns: List[Dict[str, Any]]) -> str:
        """规则压缩多轮对话为结构化摘要"""
        return "\n".join(self._compress_turn_rule(t) for t in turns)

    def _compress_turn_rule(self, turn: Dict[str, Any]) -> str:
        """规则压缩单轮对话"""
        idx = turn.get("turn_index", 0)
        user = turn.get("user_input", "")[:50]
        parts = [f"第{idx}轮: {user}"]

        tool_calls = turn.get("tool_calls", [])
        if tool_calls:
            tool_parts = []
            for tc in tool_calls:
                name = tc.get("tool_name", tc.get("name", "unknown"))
                output = str(tc.get("tool_output", tc.get("result", "")))[:80]
                tool_parts.append(f"{name}->{output}")
            parts.append(" | ".join(tool_parts))

        answer = turn.get("final_answer", "")
        if answer:
            parts.append(f"-> {answer[:100]}")

        return " | ".join(parts)

    def _llm_compress_turns(self, turns: List[Dict[str, Any]]) -> str:
        """用 LLM 批量压缩多轮对话为摘要"""
        turns_text = self._build_turns_text(turns)
        prompt = (
            "将以下对话历史压缩为简洁的结构化摘要，每轮一行，格式：第N轮: 用户意图 | 关键操作和结果 | 最终结论。"
            "省略推理过程，只保留用户意图、工具调用名称和关键结果、最终回答要点。"
            "每轮不超过80字。\n\n"
            f"{turns_text}"
        )
        try:
            result = self._llm.invoke(prompt)
            return result if result else self._rule_compress_turns(turns)
        except Exception as e:
            logger.warning("LLM压缩对话历史失败，fallback到规则压缩: %s", e)
            return self._rule_compress_turns(turns)

    # ── 内部管理 ──────────────────────────────────────────────

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
            self.save_chat_history()

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

    # ── 清理 ──────────────────────────────────────────────────

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
            self._turns.clear()
            self._turn_index = 0
            self._history_compressed = False
            self._last_sent_turn_index = 0
            self._cached_history_text = ""
        if self.persist_dir:
            self.save_chat_history()

    @property
    def short_term_count(self) -> int:
        return len(self._short_term)

    @property
    def long_term_count(self) -> int:
        return len(self._long_term_store)

    # ── 持久化（统一存储） ────────────────────────────────────

    def save_chat_history(self) -> str:
        """统一保存对话历史到 persist_dir/chat_history.json"""
        if not self.persist_dir:
            return ""
        try:
            os.makedirs(self.persist_dir, exist_ok=True)
            with self._lock:
                data = {
                    "session_id": self.session_id,
                    "turns": [dict(t) for t in self._turns],
                    "compressed_summary": self.compressed_summary,
                    "short_term": [
                        {"role": m.role, "content": m.content}
                        for m in self._short_term.messages
                    ],
                    "long_term": [
                        {"role": m.role, "content": m.content}
                        for m in self._long_term_store.messages
                    ],
                }
            filepath = os.path.join(self.persist_dir, "chat_history.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"[记忆] 对话历史已保存: {filepath}")
            return filepath
        except Exception as e:
            logger.error(f"[记忆] 保存对话历史失败: {e}")
            return ""

    def _load_from_disk(self) -> None:
        if not self.persist_dir:
            return
        filepath = os.path.join(self.persist_dir, "chat_history.json")
        if not os.path.exists(filepath):
            # 兼容旧格式 memory_{session_id}.json
            legacy = os.path.join(self.persist_dir, f"memory_{self.session_id}.json")
            if os.path.exists(legacy):
                self._load_legacy(legacy)
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.compressed_summary = data.get("compressed_summary", "")
            # 加载对话轮次
            self._turns = data.get("turns", [])
            if self._turns:
                self._turn_index = max(t.get("turn_index", 0) for t in self._turns) + 1
            # 从磁盘加载后，增量缓存失效，下次请求全量构建
            self._last_sent_turn_index = 0
            self._cached_history_text = ""
            # 加载短期/长期记忆
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
            logger.info(
                f"[记忆] 从磁盘加载: 轮次={len(self._turns)}, "
                f"短期={len(self._short_term)}, 长期={len(self._long_term_store)}"
            )
        except Exception as e:
            logger.error(f"[记忆] 从磁盘加载失败: {e}")

    def _load_legacy(self, filepath: str) -> None:
        """兼容旧格式 memory_{session_id}.json"""
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
            # 从旧格式的消息重建轮次
            self._rebuild_turns_from_messages()
            logger.info(f"[记忆] 从旧格式加载: 轮次={len(self._turns)}")
        except Exception as e:
            logger.error(f"[记忆] 加载旧格式失败: {e}")

    def _rebuild_turns_from_messages(self) -> None:
        """从短期+长期消息重建轮次（兼容旧数据）"""
        all_msgs = self._long_term_store.messages + self._short_term.messages
        turn_idx = 0
        current_turn = None
        for msg in all_msgs:
            if msg.role == "human":
                if current_turn:
                    self._turns.append(current_turn)
                current_turn = {
                    "turn_index": turn_idx,
                    "user_input": msg.content,
                    "reasoning": "",
                    "tool_calls": [],
                    "final_answer": "",
                    "timestamp": "",
                }
                turn_idx += 1
            elif msg.role == "ai" and current_turn:
                current_turn["final_answer"] = msg.content
        if current_turn:
            self._turns.append(current_turn)
        self._turn_index = turn_idx

    # ── 序列化 ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "max_short_term": self.max_short_term,
                "max_long_term": self.max_long_term,
                "short_term_count": len(self._short_term),
                "long_term_count": len(self._long_term_store),
                "has_compressed_summary": bool(self.compressed_summary),
                "turn_count": len(self._turns),
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
