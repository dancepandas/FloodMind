"""
对话记忆模块（统一历史源）

memory._turns 是唯一可变对话历史源（扁平条目日志：user 条目 + 每个 LLM 调用轮各一条 assistant 条目）。
- 前端历史展示 ← 全量 chat_history.json（完整 _turns）。
- 智能体对话上下文 ← get_chat_history_for_system_prompt 产出的结构化精简视图（早期压缩摘要 + 近期原文），
  绝不灌全量 _turns（控 token / 防长文本幻觉）。

LongTermMemory 是独立的"长期事实"存储（偏好/决策/规则），由 MemoryAdd 工具 / /api/memory/* 显式写入，
与对话历史（_turns）、任务经验树（task_experience）是不同种类的存储，不参与对话上下文压缩。
"""

import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
LONG_TERM_MEMORY_FILE = os.path.join(_MEMORY_DIR, "long_term_memory.json")


class LongTermMemory:
    """长期记忆管理器（独立的事实存储：偏好/决策/规则）"""

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


class DualMemory:
    """统一对话记忆：_turns 为唯一历史源 + 结构化精简上下文 + 长期事实存储。

    历史压缩只有两道：
    1) get_chat_history_for_system_prompt 内的 _turns 压缩（早期轮→摘要，近期轮→原文）—— 控制 history 注入。
    2) executor 的 ContextCompressor（压 live state.messages）—— 控制运行中消息。
    （旧的 _short_term/_consolidate/compressed_summary 子系统已移除。）
    """

    # 对话历史压缩参数
    HISTORY_COMPRESS_RATIO = 0.85    # 上下文使用率超过此值触发压缩
    HISTORY_KEEP_RECENT_ENTRIES = 6  # 压缩时保留最近 N 个 entries（user/assistant 各计 1）原文

    def __init__(
        self,
        session_id: str,
        max_short_term: int = 20,   # 已弃用（_short_term 子系统已删除），保留仅兼容旧签名
        max_long_term: int = 100,   # 已弃用，保留仅兼容旧签名
        persist_dir: Optional[str] = None,
        llm: Optional[Any] = None,
        context_window: int = 32768,
    ):
        # 弃用警告（不影响使用，仅提示迁移）
        if max_short_term != 20 or max_long_term != 100:
            import warnings
            warnings.warn(
                "DualMemory(max_short_term=..., max_long_term=...) 已弃用——"
                "_short_term/_long_term 子系统已删除，这些参数不再生效。"
                "请移除调用中的这些参数。",
                DeprecationWarning, stacklevel=2,
            )
        self.session_id = session_id
        self.persist_dir = persist_dir
        self._llm = llm
        self.context_window = context_window

        self.long_term_memory = LongTermMemory()
        self._lock = threading.RLock()
        self._reasoning_trace: List[Dict[str, Any]] = []

        # 对话历史（扁平条目日志：唯一可变历史源）
        self._turns: List[Dict[str, Any]] = []
        self._turn_index: int = 0
        self._history_compressed: bool = False  # 早期轮次是否已压缩

        # history 文本缓存
        self._last_sent_turn_index: int = 0
        self._cached_history_text: str = ""

        if persist_dir:
            self._load_from_disk()

        logger.info(f"[记忆] DualMemory 初始化 - 会话: {session_id}, LLM压缩: {'启用' if llm else '禁用'}")

    def set_llm(self, llm: Any) -> None:
        """注入 LLM 服务（支持延迟注入），用于 _turns 历史压缩。"""
        self._llm = llm

    # ── 消息添加（_turns 唯一历史源）────────────────────────────

    def add_user_message(self, content: str) -> None:
        """追加一条用户消息（开启新一轮）。

        _turns 是扁平条目日志：每个 user 消息、每个 assistant LLM 调用轮各一条。
        assistant 轮由 executor 在每轮原子完成后通过 add_assistant_round 追加。
        """
        with self._lock:
            self._turns.append({
                "role": "user",
                "turn_index": self._turn_index,
                "content": content,
                "timestamp": datetime.now().isoformat(),
            })
            self._turn_index += 1
        logger.debug(f"[记忆] 添加用户消息，轮次: {self._turn_index - 1}")

    def add_ai_message(self, content: str) -> None:
        """追加 AI 消息（兼容旧入口；按一个终态轮处理）。"""
        self.add_assistant_round(content=content, reasoning="", tool_calls=None, is_final=True)

    def add_ai_message_with_trace(
        self,
        content: str,
        reasoning: str = "",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """记录完整的 AI 回复（含推理过程和工具调用详情）。兼容旧入口，委托给 add_assistant_round。"""
        self.add_assistant_round(
            content=content,
            reasoning=reasoning or "",
            tool_calls=tool_calls,
            is_final=True,
        )

    def add_assistant_round(
        self,
        content: str,
        reasoning: str = "",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        is_final: bool = False,
    ) -> None:
        """记录一次完整的 LLM 调用轮（assistant 产物 + 本轮工具调用/结果）。

        这是 executor 在每轮原子完成后写入 memory 的入口。history 粒度 = 每次 LLM 调用。
        中断（abort）时不会调用本方法，故 memory 永远只含完整轮（断点续行的最小单元）。
        """
        with self._lock:
            turn_index = max(self._turn_index - 1, 0)
            self._turns.append({
                "role": "assistant",
                "turn_index": turn_index,
                "content": content or "",
                "reasoning": reasoning or "",
                "tool_calls": tool_calls or [],
                "is_final": bool(is_final),
                "timestamp": datetime.now().isoformat(),
            })
        logger.debug(f"[记忆] 添加 assistant 轮，turn={turn_index}, final={is_final}, tools={len(tool_calls or [])}")
        # 落盘以保证中途崩溃不丢已完成轮（durability）
        if self.persist_dir:
            try:
                self.save_chat_history()
            except Exception as e:
                logger.warning(f"[记忆] 落盘失败（非致命）: {e}")

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

    # ── 历史读取（agent 上下文 / 排队 / 检索）──────────────────

    def get_user_messages(self) -> List[str]:
        """按顺序返回所有用户消息内容（供 executor 检测运行中追加的排队指令）。"""
        with self._lock:
            return [e.get("content", "") for e in self._turns if e.get("role") == "user"]

    def get_pending_user_messages(self) -> List[str]:
        """返回尾部尚未被 assistant 轮回应的用户消息（当前 run 的用户指令 + 排队指令）。"""
        with self._lock:
            if not self._turns:
                return []
            pending: List[str] = []
            for e in reversed(self._turns):
                if e.get("role") == "user":
                    pending.append(e.get("content", ""))
                else:
                    break
            return list(reversed(pending))

    def get_turns(self) -> List[Dict[str, Any]]:
        """获取所有对话历史条目（扁平日志）。前端展示走 SessionManager 读 chat_history.json。"""
        with self._lock:
            return list(self._turns)

    def search_history(self, query, top_k: int = 5) -> str:
        """在对话历史（_turns）中按关键词搜索，返回最相关轮次文本。

        供 ConversationSearch / MemorySearch 工具使用。query 可为 str 或 list。
        按“user + 其后续连续 assistant”分块打分，返回命中关键词数最高的 top_k 块。
        无命中时返回含“未找到”的提示串（供工具层判断）。
        """
        with self._lock:
            turns = list(self._turns)
        if not turns:
            return f"未找到与 '{query}' 相关的对话"
        words = self._normalize_search_words(query)
        if not words:
            return f"未找到与 '{query}' 相关的对话"
        # 分块：每个 user 条目 + 其后连续的 assistant 条目
        blocks: List[List[Dict[str, Any]]] = []
        i = 0
        while i < len(turns):
            if turns[i].get("role") == "user":
                j = i + 1
                while j < len(turns) and turns[j].get("role") == "assistant":
                    j += 1
                blocks.append(turns[i:j])
                i = j
            else:
                i += 1
        if not blocks:
            return f"未找到与 '{query}' 相关的对话"
        scored: List[Tuple[int, List[Dict[str, Any]]]] = []
        for b in blocks:
            text = self._build_turns_text(b).lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                scored.append((score, b))
        if not scored:
            return f"未找到与 '{query}' 相关的对话"
        scored.sort(key=lambda x: x[0], reverse=True)
        out = [f"# 历史搜索结果（query: {query}，命中 {len(scored)} 块）"]
        for score, b in scored[:top_k]:
            out.append(f"\n## 相关度 {score}\n" + self._build_turns_text(b))
        return "\n".join(out)

    @staticmethod
    def _normalize_search_words(query) -> List[str]:
        if isinstance(query, (list, tuple)):
            return [str(w).lower().strip() for w in query if str(w).strip()]
        q = str(query).lower().strip()
        return [w for w in re.split(r"\s+", q) if w]

    # ── 长期事实存储（MemoryAdd / /api/memory/*）────────────────

    def add_long_term_memory(self, content: str, entry_type: str = "note") -> bool:
        """记录到长期事实存储。去重，返回是否新增。"""
        content = str(content).strip()
        if not content:
            return False
        with self._lock:
            existing = [e.get("content", "") for e in self.long_term_memory.entries]
            if content in existing:
                return False
            self.long_term_memory.add_entry(content, category=entry_type or "note", importance=0.7)
        return True

    def search_long_term(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """搜索长期事实存储（/api/memory/search 后端）。"""
        with self._lock:
            return self.long_term_memory.search(query, top_k=top_k)

    # ── 智能体上下文：结构化精简视图（唯一 history 注入点）──────

    def get_chat_history_for_system_prompt(self, total_context_chars: int = 0, context_window: int = 0, event_bus=None) -> str:
        """构建对话历史文本（精简上下文）：早期 entries 压缩为摘要，近期 entries 保留原文。

        这是 LLM 常规上下文的唯一历史来源。全量 _turns 持久化在 chat_history.json，
        仅供前端展示 / MemorySearch 检索，不进常规上下文（控 token / 防长文本幻觉）。
        """
        with self._lock:
            if not self._turns:
                return ""

            # 判断是否需要压缩
            need_compress = False
            if context_window > 0 and len(self._turns) > self.HISTORY_KEEP_RECENT_ENTRIES:
                estimated_tokens = (total_context_chars + len(self._cached_history_text)) / 1.5
                if estimated_tokens / context_window > self.HISTORY_COMPRESS_RATIO:
                    need_compress = True

            if need_compress:
                # 压缩触发：保留近期 entries 原文，早期 entries 压缩为摘要
                recent = self._turns[-self.HISTORY_KEEP_RECENT_ENTRIES:]
                older = self._turns[:-self.HISTORY_KEEP_RECENT_ENTRIES]
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
                return result

        # 全量重建
        result = self._build_turns_text(self._turns)
        self._cached_history_text = result
        return result

    def _build_turns_text(self, entries: List[Dict[str, Any]]) -> str:
        """将扁平条目列表格式化为文本。

        跳过尾部尚未被 assistant 轮回应的用户消息（当前 run 的用户指令），
        因为它会作为单独的 user message 发给 LLM，包含在历史中会重复且破坏缓存前缀。
        """
        effective = entries
        if entries and entries[-1].get("role") == "user":
            effective = entries[:-1]
        if not effective:
            return ""
        lines = ["[对话历史]"]
        for e in effective:
            role = e.get("role")
            idx = e.get("turn_index", 0)
            if role == "user":
                lines.append(f"\n第{idx}轮:")
                lines.append(f"用户: {e.get('content', '')}")
            elif role == "assistant":
                if e.get("reasoning"):
                    lines.append(f"思考: {e['reasoning']}")
                for tc in e.get("tool_calls") or []:
                    name = tc.get("tool_name", tc.get("name", "unknown"))
                    inp = str(tc.get("tool_input", ""))[:200]
                    out = str(tc.get("tool_output", tc.get("result", "")))[:300]
                    lines.append(f"  调用 {name}: {inp}")
                    lines.append(f"  结果: {out}")
                if e.get("content"):
                    lines.append(f"回答: {e['content']}")
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

    def _compress_turn_rule(self, entry: Dict[str, Any]) -> str:
        """规则压缩单个历史条目（user 或 assistant 轮）"""
        role = entry.get("role")
        idx = entry.get("turn_index", 0)
        if role == "user":
            return f"第{idx}轮: 用户 {entry.get('content', '')[:50]}"
        # assistant 轮
        parts = [f"第{idx}轮"]
        for tc in entry.get("tool_calls") or []:
            name = tc.get("tool_name", tc.get("name", "unknown"))
            output = str(tc.get("tool_output", tc.get("result", "")))[:80]
            parts.append(f"{name}->{output}")
        ans = entry.get("content", "")
        if ans:
            parts.append(f"-> {ans[:100]}")
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

    # ── 清理 ──────────────────────────────────────────────────

    def clear_all(self) -> None:
        """清空对话历史与长期事实。"""
        with self._lock:
            self._turns.clear()
            self._turn_index = 0
            self._history_compressed = False
            self._last_sent_turn_index = 0
            self._cached_history_text = ""
            self._reasoning_trace.clear()
            self.long_term_memory.clear()
        if self.persist_dir:
            self.save_chat_history()

    def clear(self) -> None:
        """向后兼容别名：等价于 clear_all()。"""
        self.clear_all()

    def force_heartbeat(self) -> bool:
        """向后兼容入口（/api/memory/heartbeat）：旧语义是强制归纳，整合子系统移除后
        退化为"立即落盘"，保证记忆持久化。返回是否成功。"""
        if not self.persist_dir:
            return False
        try:
            self.save_chat_history()
            return True
        except Exception as e:
            logger.warning(f"[记忆] force_heartbeat 落盘失败: {e}")
            return False

    @property
    def turn_count(self) -> int:
        with self._lock:
            return len(self._turns)

    @property
    def long_term_count(self) -> int:
        return len(self.long_term_memory.entries)

    # ── 持久化（chat_history.json = 完整 _turns，前端展示源）────

    def save_chat_history(self) -> str:
        """统一保存对话历史到 persist_dir/chat_history.json（完整 _turns）。"""
        if not self.persist_dir:
            return ""
        try:
            os.makedirs(self.persist_dir, exist_ok=True)
            with self._lock:
                data = {
                    "session_id": self.session_id,
                    "turns": [dict(t) for t in self._turns],
                    # 持久化压缩状态，避免重启后冷缓存触发重复 LLM 压缩
                    "history_compressed": self._history_compressed,
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
            # 恢复压缩状态，避免重启后冷缓存触发重复 LLM 压缩
            self._history_compressed = bool(data.get("history_compressed", False))
            # 加载对话历史条目
            self._turns = data.get("turns", [])
            # 迁移旧格式（user_input/final_answer）→ 扁平条目（role/content）
            if self._turns and "role" not in self._turns[0]:
                self._turns = self._migrate_old_turns(self._turns)
            if self._turns:
                self._turn_index = max((t.get("turn_index", 0) for t in self._turns), default=-1) + 1
            # 从磁盘加载后，缓存失效，下次请求全量构建
            self._last_sent_turn_index = 0
            self._cached_history_text = ""
            # 旧文件可能含 short_term/long_term/compressed_summary 字段（已废弃），忽略
            logger.info(f"[记忆] 从磁盘加载: 轮次={len(self._turns)}")
        except Exception as e:
            logger.error(f"[记忆] 从磁盘加载失败: {e}")

    def _load_legacy(self, filepath: str) -> None:
        """兼容旧格式 memory_{session_id}.json（含 short_term/long_term messages）。"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._reasoning_trace = data.get("reasoning_trace", [])
            # 旧格式按 messages 重建扁平 _turns
            legacy_msgs = list(data.get("long_term", [])) + list(data.get("short_term", []))
            self._turns = self._turns_from_legacy_messages(legacy_msgs)
            if data.get("turns"):  # 极少数旧文件同时含 turns
                self._turns = self._migrate_old_turns(data["turns"]) if "role" not in data["turns"][0] else data["turns"]
            if self._turns:
                self._turn_index = max((t.get("turn_index", 0) for t in self._turns), default=-1) + 1
            logger.info(f"[记忆] 从旧格式加载: 轮次={len(self._turns)}")
        except Exception as e:
            logger.error(f"[记忆] 加载旧格式失败: {e}")

    @staticmethod
    def _turns_from_legacy_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """从旧格式 short/long term 消息列表重建扁平 _turns。"""
        flat: List[Dict[str, Any]] = []
        turn_idx = 0
        for msg in messages:
            role = msg.get("role") or msg.get("type")
            content = msg.get("content", "")
            if role in ("human", "user"):
                flat.append({"role": "user", "turn_index": turn_idx, "content": content, "timestamp": ""})
                turn_idx += 1
            elif role in ("ai", "assistant"):
                flat.append({
                    "role": "assistant", "turn_index": max(turn_idx - 1, 0), "content": content,
                    "reasoning": "", "tool_calls": [], "is_final": True, "timestamp": "",
                })
        return flat

    def _migrate_old_turns(self, old_turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把旧版 user_input/final_answer 轮结构迁移为扁平 role/content 条目日志。"""
        flat: List[Dict[str, Any]] = []
        for t in old_turns:
            idx = t.get("turn_index", 0)
            ts = t.get("timestamp", "")
            flat.append({
                "role": "user",
                "turn_index": idx,
                "content": t.get("user_input", ""),
                "timestamp": ts,
            })
            ans = t.get("final_answer", "")
            if ans or t.get("tool_calls") or t.get("reasoning"):
                entry = {
                    "role": "assistant",
                    "turn_index": idx,
                    "content": ans,
                    "reasoning": t.get("reasoning", ""),
                    "tool_calls": t.get("tool_calls", []) or [],
                    "is_final": True,
                    "timestamp": ts,
                }
                if t.get("compressed"):
                    entry["compressed"] = t["compressed"]
                flat.append(entry)
        logger.info(f"[记忆] 迁移旧格式轮次: {len(old_turns)} 轮 → {len(flat)} 条目")
        return flat

    # ── 序列化 ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "turn_count": len(self._turns),
                "long_term_count": len(self.long_term_memory.entries),
            }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DualMemory':
        memory = cls(session_id=data.get("session_id", "unknown"))
        with memory._lock:
            memory._turns = list(data.get("turns", []))
            if memory._turns:
                memory._turn_index = max((t.get("turn_index", 0) for t in memory._turns), default=-1) + 1
        return memory
