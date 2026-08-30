"""Conversation memory backed exclusively by canonical Journal projections.

LongTermMemory remains an independent store for durable facts. Conversation turns
are derived from Journal events and are never persisted separately.
"""

import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 记忆文件默认落在运行时数据目录（可被 FLOODMIND_PROJECT_ROOT 重定向），
# 不再写安装包目录——site-packages 只读安装下旧路径会静默丢失全部长期记忆。
try:
    from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT as _RUNTIME_ROOT
except Exception:  # pragma: no cover - 防御性回退，保持模块可独立导入
    _RUNTIME_ROOT = None

if _RUNTIME_ROOT is not None:
    _DEFAULT_MEMORY_FILE = str(_RUNTIME_ROOT / "data" / "memory" / "long_term_memory.json")
else:  # pragma: no cover
    _DEFAULT_MEMORY_FILE = os.path.join(os.path.expanduser("~"), ".floodmind", "long_term_memory.json")

LONG_TERM_MEMORY_FILE = _DEFAULT_MEMORY_FILE

# SDK 未配置 providers 时的记忆窗口回退值（api.Agent._resolve_context_window 使用）
DEFAULT_CONTEXT_WINDOW_FALLBACK = 32768

# 进程级互斥：LongTermMemory 为多 Agent 实例共享的进程内单文件存储
_LTM_LOCK = threading.RLock()


class LongTermMemory:
    """长期记忆管理器（独立的事实存储：偏好/决策/规则）

    单进程假设：跨进程并发写同一文件不在本类保证范围内（与 SessionManager 一致）。
    写入为 tmp + fsync + os.replace 原子替换，崩溃不会留下半写文件。
    """

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
        with _LTM_LOCK:
            self.entries.append(entry)
            self._save(merge=True)
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
        # access_count 只在内存累加，随下一次写操作持久化——读路径不再整文件重写
        return results

    def get_recent(self, n: int = 5) -> List[Dict[str, Any]]:
        return self.entries[-n:]

    def get_by_category(self, category: str) -> List[Dict[str, Any]]:
        return [e for e in self.entries if e.get("category") == category]

    def clear(self):
        with _LTM_LOCK:
            self.entries.clear()
            self._save()

    def _load(self):
        with _LTM_LOCK:
            if not os.path.exists(self.memory_file):
                self.entries = []
                self._migrate_legacy_file()
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

    def _migrate_legacy_file(self):
        """旧版本把记忆文件放在安装包目录，首次运行时迁移到数据目录。"""
        legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "long_term_memory.json")
        if not os.path.isfile(legacy) or os.path.abspath(legacy) == os.path.abspath(self.memory_file):
            return
        try:
            with open(legacy, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                self.entries = json.loads(content)
                self._save()
                logger.info(f"[长期记忆] 已从旧路径迁移 {len(self.entries)} 条记录: {legacy}")
        except Exception as e:
            logger.warning(f"[长期记忆] 旧路径迁移失败（忽略）: {e}")

    def _save(self, merge: bool = False):
        """原子落盘。merge=True 时（add 路径）先合并磁盘上的其他实例新增条目——
        LongTermMemory 虽有进程级写锁，但各 DualMemory 实例各持快照整文件覆写，
        不合并会 last-writer-wins 丢掉其他实例的条目（P2-9/D03）。"""
        try:
            entries_to_write = self.entries
            if merge:
                disk_entries = []
                try:
                    if os.path.exists(self.memory_file):
                        with open(self.memory_file, "r", encoding="utf-8") as f:
                            text = f.read()
                        if text.strip():
                            loaded = json.loads(text)
                            if isinstance(loaded, list):
                                disk_entries = [e for e in loaded if isinstance(e, dict)]
                except Exception as e:
                    logger.warning(f"[长期记忆] 合并前读取失败（按内存快照写）: {e}")
                if disk_entries:
                    seen = {(str(e.get("content")), str(e.get("timestamp"))) for e in self.entries}
                    for e in disk_entries:
                        key = (str(e.get("content")), str(e.get("timestamp")))
                        if key not in seen:
                            self.entries.append(e)
                            seen.add(key)
                    entries_to_write = self.entries
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            tmp_path = self.memory_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(entries_to_write, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.memory_file)
        except Exception as e:
            logger.error(f"[长期记忆] 保存失败: {e}")


class DualMemory:
    """Journal-projected conversation history plus independent long-term facts."""

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

        self._journal_authority = None
        self._runtime_dir = None
        self._conversation_id = ""
        self._compression_cache: Dict[str, str] = {}
        self._history_compressed: bool = False

        # history 文本缓存
        self._last_sent_turn_index: int = 0
        self._cached_history_text: str = ""

        logger.info(f"[记忆] DualMemory 初始化 - 会话: {session_id}, LLM压缩: {'启用' if llm else '禁用'}")

    def set_llm(self, llm: Any) -> None:
        """Inject the LLM service used for derived history compression."""
        self._llm = llm

    def bind_journal(self, authority: Any, runtime_dir: Any, conversation_id: str) -> None:
        """Bind this memory view to the current run and conversation Journal."""
        self._journal_authority = authority
        self._runtime_dir = runtime_dir
        self._conversation_id = conversation_id
        # 重绑新会话必须清空派生缓存：_compression_cache 的 key 只含 turn_index:role，
        # 旧会话残留的摘要在新会话会按相同 key 命中复现（跨会话内容泄漏）；
        # 同时复位 history 文本缓存，避免用旧会话长度误判压缩需求。
        with self._lock:
            self._compression_cache.clear()
            self._history_compressed = False
            self._last_sent_turn_index = 0
            self._cached_history_text = ""

    def _current_turns(self) -> List[Dict[str, Any]]:
        if self._journal_authority is None:
            return []
        from floodmind.agent.runtime.services.history_projection import project_current
        return project_current(self._journal_authority)

    def _conversation_turns(self) -> List[Dict[str, Any]]:
        if self._runtime_dir is None or not self._conversation_id:
            return self._current_turns()
        from floodmind.agent.runtime.services.history_projection import project_conversation
        return project_conversation(self._runtime_dir, self._conversation_id)

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
        return [e.get("content", "") for e in self._current_turns() if e.get("role") == "user"]

    def get_pending_user_messages(self) -> List[str]:
        """返回尾部尚未被 assistant 轮回应的用户消息（当前 run 的用户指令 + 排队指令）。"""
        turns = self._current_turns()
        if not turns:
            return []
        pending: List[str] = []
        for entry in reversed(turns):
            if entry.get("role") == "user":
                pending.append(entry.get("content", ""))
            else:
                break
        return list(reversed(pending))

    def get_turns(self) -> List[Dict[str, Any]]:
        """Get the current run's projected flat turns."""
        return self._current_turns()

    def search_history(self, query, top_k: int = 5) -> str:
        """Search projected conversation turns and return the best matching blocks.

        供 ConversationSearch / MemorySearch 工具使用。query 可为 str 或 list。
        按“user + 其后续连续 assistant”分块打分，返回命中关键词数最高的 top_k 块。
        无命中时返回含“未找到”的提示串（供工具层判断）。
        """
        turns = self._conversation_turns()
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

        The canonical Journal projection is the only conversation-history input.
        """
        turns = self._conversation_turns()
        if not turns:
            return ""

        with self._lock:
            need_compress = False
            if context_window > 0 and len(turns) > self.HISTORY_KEEP_RECENT_ENTRIES:
                # 基于本次将生成的全量历史文本估算，而非上次返回值 _cached_history_text：
                # 用缓存估算会"压缩后低估→本轮返回全量→再高估"交替震荡，改为对
                # _build_turns_text(turns) 的长度做无状态判定，天然收敛。
                full_text = self._build_turns_text(turns)
                estimated_tokens = (total_context_chars + len(full_text)) / 1.5
                need_compress = estimated_tokens / context_window > self.HISTORY_COMPRESS_RATIO

            if need_compress:
                recent = turns[-self.HISTORY_KEEP_RECENT_ENTRIES:]
                older = turns[:-self.HISTORY_KEEP_RECENT_ENTRIES]
                if older:
                    older_summary = self._get_cached_or_compress_older(older, event_bus)
                    lines = ["[对话历史]"]
                    if older_summary:
                        lines.append(f"\n[早期对话摘要]\n{older_summary}\n")
                    lines.append(self._build_turns_text(recent))
                    result = "\n".join(lines)
                else:
                    result = self._build_turns_text(turns)
                self._cached_history_text = result
                return result

        result = self._build_turns_text(turns)
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
        # key 掺入 conversation_id 双保险：即使缓存未随 bind_journal 清空，
        # 跨会话的同 turn_index:role 组合也不会误命中旧会话摘要。
        cache_key = self._conversation_id + "|" + "|".join(
            str(t.get("turn_index", "")) + ":" + t.get("role", "") for t in older_turns
        )
        cached = self._compression_cache.get(cache_key)
        if cached is not None:
            return cached

        # 发送压缩开始事件
        if event_bus:
            event_bus.emit_context_compress_start()

        if self._llm:
            summary = self._llm_compress_turns(older_turns)
        else:
            summary = self._rule_compress_turns(older_turns)

        self._compression_cache[cache_key] = summary
        self._history_compressed = True

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

    # ── derived-cache cleanup ───────────────────────────────────

    def clear_all(self) -> None:
        """Clear derived caches, reasoning trace, and independent long-term facts."""
        with self._lock:
            self._history_compressed = False
            self._compression_cache.clear()
            self._last_sent_turn_index = 0
            self._cached_history_text = ""
            self._reasoning_trace.clear()
            self.long_term_memory.clear()

    def clear(self) -> None:
        self.clear_all()

    def force_heartbeat(self) -> bool:
        """Journal events are durable immediately; report whether one is bound."""
        return self._journal_authority is not None

    @property
    def turn_count(self) -> int:
        return len(self._current_turns())

    @property
    def long_term_count(self) -> int:
        return len(self.long_term_memory.entries)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_count": self.turn_count,
            "long_term_count": len(self.long_term_memory.entries),
        }
