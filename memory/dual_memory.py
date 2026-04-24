"""
AI驱动的双层记忆系统

架构设计：
┌─────────────────────────────────────────────────────────────┐
│                      DualMemory                              │
├─────────────────────────────────────────────────────────────┤
│  短期记忆（ShortTermMemory）                                  │
│  ├── 对话历史：ChatMessageHistory                            │
│  ├── 自动压缩：当超过阈值时AI生成摘要                         │
│  └── 系统提示：始终完整保留                                   │
├─────────────────────────────────────────────────────────────┤
│  长期记忆（LongTermMemory）                                   │
│  ├── memory.md：AI主动记录的重要内容                          │
│  ├── 通过工具调用添加（add_memory工具）                       │
│  └── 避免自动提取导致的幻觉                                   │
├─────────────────────────────────────────────────────────────┤
│  全局搜索（GlobalSearch）                                    │
│  ├── 完整对话历史搜索                                        │
│  ├── Skills文档搜索                                          │
│  └── 基于正则表达式的关键词检索                               │
├─────────────────────────────────────────────────────────────┤
│  压缩机制                                                     │
│  ├── 当对话轮数超过阈值时触发                                 │
│  ├── AI生成摘要替换旧对话                                     │
│  └── 保留最近的N轮对话                                        │
└─────────────────────────────────────────────────────────────┘
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from memory.global_search import GlobalSearch, DualMemorySearch

logger = logging.getLogger(__name__)

_MEMORY_DIR = Path(__file__).parent
LONG_TERM_MEMORY_FILE = _MEMORY_DIR / "memory.md"


class LongTermMemory:
    """长期记忆管理 - 由AI主动添加"""
    
    def __init__(self, file_path: Path = LONG_TERM_MEMORY_FILE):
        self.file_path = file_path
        self.content: str = ""
        self.entries: List[Dict[str, str]] = []
        self._lock = threading.Lock()
        self._load()
    
    def _load(self):
        try:
            if self.file_path.exists():
                self.content = self.file_path.read_text(encoding="utf-8")
                self._parse_entries()
                logger.info(f"[长期记忆] 加载成功 - {len(self.entries)} 条记录")
            else:
                self._init_file()
        except Exception as e:
            logger.error(f"[长期记忆] 加载失败: {e}")
            self.content = ""
            self.entries = []
    
    def _init_file(self):
        initial_content = """# 长期记忆

本文件存储AI主动记录的重要内容，包括：
- 用户明确要求记住的事项
- 重要的决策和结论
- 用户偏好和习惯

---
"""
        self.file_path.write_text(initial_content, encoding="utf-8")
        self.content = initial_content
        self.entries = []
        logger.info("[长期记忆] 文件已初始化")
    
    def _parse_entries(self):
        import re
        self.entries = []
        pattern = r"##\s+(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)\n(.+?)(?=\n##\s+|\n---|\Z)"
        matches = re.findall(pattern, self.content, re.DOTALL)
        
        for timestamp, entry_content in matches:
            self.entries.append({
                "timestamp": timestamp.strip(),
                "content": entry_content.strip(),
            })
    
    def add_entry(self, content: str, entry_type: str = "note") -> bool:
        if not content or not content.strip():
            return False
        
        with self._lock:
            normalized = content.strip()
            
            if self._is_duplicate(normalized):
                logger.debug(f"[长期记忆] 跳过重复内容: {normalized[:50]}...")
                return False
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            type_labels = {
                "preference": "用户偏好",
                "decision": "重要决策",
                "rule": "重要规则",
                "note": "备注",
            }
            type_label = type_labels.get(entry_type, "备注")
            
            entry_text = f"\n## {timestamp}\n**[{type_label}]** {normalized}\n"
            
            try:
                with open(self.file_path, "a", encoding="utf-8") as f:
                    f.write(entry_text)
                
                self.content += entry_text
                self.entries.append({
                    "timestamp": timestamp,
                    "content": normalized,
                    "type": entry_type,
                })
                
                logger.info(f"[长期记忆] 已添加: {normalized[:60]}...")
                return True
                
            except Exception as e:
                logger.error(f"[长期记忆] 写入失败: {e}")
                return False
    
    def _is_duplicate(self, content: str) -> bool:
        import re
        normalized = re.sub(r"\s+", "", content.lower())
        
        for entry in self.entries:
            import re
            existing = re.sub(r"\s+", "", entry["content"].lower())
            if normalized == existing:
                return True
            
            if len(normalized) > 20 and len(existing) > 20:
                if normalized in existing or existing in normalized:
                    return True
        
        return False
    
    def get_context(self, max_entries: int = 10, keywords: Optional[List[str]] = None) -> str:
        if not self.entries:
            return ""
        
        scored = self._score_entries(keywords)
        top = scored[:max_entries]
        
        lines = ["[长期记忆]"]
        for entry in top:
            lines.append(f"- {entry['content']}")
        
        return "\n".join(lines)

    def _score_entries(self, keywords: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        now = datetime.now()
        scored = []
        for entry in self.entries:
            age_hours = 0.0
            try:
                ts = datetime.strptime(entry.get("timestamp", ""), "%Y-%m-%d %H:%M")
                age_hours = max(0, (now - ts).total_seconds() / 3600)
            except (ValueError, TypeError):
                age_hours = 720.0

            time_score = 1.0 / (1.0 + age_hours / 168.0)

            type_scores = {
                "rule": 2.0,
                "preference": 1.5,
                "decision": 1.3,
                "note": 1.0,
            }
            type_score = type_scores.get(entry.get("type", "note"), 1.0)

            relevance_score = 0.0
            if keywords:
                content_lower = entry.get("content", "").lower()
                for kw in keywords:
                    if kw.lower() in content_lower:
                        relevance_score += 1.0

            final_score = time_score * type_score + relevance_score * 2.0
            scored_entry = dict(entry)
            scored_entry["_score"] = final_score
            scored.append(scored_entry)

        scored.sort(key=lambda e: e.get("_score", 0), reverse=True)
        return scored
    
    def clear(self):
        self._init_file()
        logger.info("[长期记忆] 已清空")


class ContextCompressor:
    """AI驱动的上下文压缩器"""
    
    COMPRESS_PROMPT = """请将以下对话历史压缩为简洁的摘要，保留关键信息：

{conversation}

要求：
1. 保留重要的决策、结论和用户要求
2. 保留用户提到的偏好和习惯
3. 省略冗余的细节和中间过程
4. 使用简洁的中文描述
5. 不要编造或添加信息

直接输出摘要内容，不要其他解释："""

    DISTILL_PROMPT = """请将以下已有摘要和新增对话内容统一蒸馏为一份简洁摘要：

【已有摘要】
{existing_summary}

【新增对话】
{new_conversation}

要求：
1. 将已有摘要和新对话内容合并为一份统一的摘要
2. 输出长度不超过 {max_chars} 字符
3. 保留最重要的决策、结论、用户偏好和规则
4. 省略冗余细节和中间过程
5. 如果已有摘要中的信息在新对话中被修正，以新信息为准

直接输出蒸馏后的摘要，不要其他解释："""

    MAX_SUMMARY_CHARS = 2000

    def __init__(self, llm: Optional[BaseLanguageModel] = None):
        self.llm = llm
    
    def set_llm(self, llm: BaseLanguageModel):
        self.llm = llm
    
    def compress(self, messages: List[BaseMessage]) -> str:
        if not self.llm:
            return self._simple_compress(messages)
        
        conversation_text = self._messages_to_text(messages)
        
        if len(conversation_text) < 500:
            return ""
        
        try:
            prompt = self.COMPRESS_PROMPT.format(conversation=conversation_text)
            response = self.llm.invoke(prompt)
            summary = response.content if hasattr(response, "content") else str(response)
            summary = summary.strip()
            
            logger.info(f"[上下文压缩] 生成摘要: {len(conversation_text)} -> {len(summary)} 字符")
            return summary
            
        except Exception as e:
            logger.error(f"[上下文压缩] AI压缩失败: {e}")
            return self._simple_compress(messages)

    def distill(self, existing_summary: str, new_conversation_text: str) -> str:
        if not self.llm:
            combined = f"{existing_summary}\n\n{new_conversation_text}"
            return combined[:self.MAX_SUMMARY_CHARS]

        try:
            prompt = self.DISTILL_PROMPT.format(
                existing_summary=existing_summary,
                new_conversation=new_conversation_text,
                max_chars=self.MAX_SUMMARY_CHARS,
            )
            response = self.llm.invoke(prompt)
            summary = response.content if hasattr(response, "content") else str(response)
            summary = summary.strip()
            logger.info(f"[摘要蒸馏] {len(existing_summary)} + {len(new_conversation_text)} -> {len(summary)} 字符")
            return summary
        except Exception as e:
            logger.error(f"[摘要蒸馏] 失败: {e}")
            new_budget = min(len(new_conversation_text), int(self.MAX_SUMMARY_CHARS * 0.7))
            old_budget = self.MAX_SUMMARY_CHARS - new_budget
            return existing_summary[-old_budget:] + "\n\n" + new_conversation_text[:new_budget]
    
    def _simple_compress(self, messages: List[BaseMessage]) -> str:
        lines = ["[对话摘要]"]
        for msg in messages:
            if isinstance(msg, HumanMessage):
                content = str(msg.content)
                if len(content) > 100:
                    content = content[:100] + "..."
                lines.append(f"用户: {content}")
            elif isinstance(msg, AIMessage):
                content = str(msg.content)
                if len(content) > 200:
                    content = content[:200] + "..."
                lines.append(f"助手: {content}")
        
        return "\n".join(lines)
    
    def _messages_to_text(self, messages: List[BaseMessage]) -> str:
        lines = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                lines.append(f"用户: {msg.content}")
            elif isinstance(msg, AIMessage):
                lines.append(f"助手: {msg.content}")
        return "\n".join(lines)


class DualMemory:
    """AI驱动的双层记忆系统"""
    
    COMPRESS_THRESHOLD = 16
    KEEP_RECENT_TURNS = 6
    TOKEN_BUDGET_RATIO = 0.8
    CHARS_PER_TOKEN = 1.5
    
    def __init__(
        self,
        max_history: int = 30,
        llm: Optional[BaseLanguageModel] = None,
        context_window: int = 32768,
        memory_dir: Optional[Path] = None,
        session_id: Optional[str] = None,
    ):
        self.max_history = max_history
        self.llm = llm
        self.context_window = context_window
        self.session_id = session_id or "default"

        if memory_dir:
            self.memory_dir = Path(memory_dir)
        else:
            self.memory_dir = _MEMORY_DIR

        self.chat_history = ChatMessageHistory()
        self.full_chat_history = ChatMessageHistory()
        self._lock = threading.Lock()

        self.long_term_memory = LongTermMemory(self.memory_dir / "memory.md")
        self.compressor = ContextCompressor(llm)
        self.global_search_engine = GlobalSearch(memory_dir=self.memory_dir)
        self.memory_search = DualMemorySearch(self)

        self._compressed_summary: str = ""
        self._recent_reusable_result: str = ""
        self._last_tool_use: Dict[str, str] = {}
        self._turn_count = 0
        self._status_callback = None

        logger.info(
            f"[双层记忆] 初始化完成 - "
            f"会话: {self.session_id}, "
            f"压缩阈值: {self.COMPRESS_THRESHOLD}轮, "
            f"长期记忆: {len(self.long_term_memory.entries)} 条"
        )
    
    def set_llm(self, llm: BaseLanguageModel):
        self.llm = llm
        self.compressor.set_llm(llm)

    def set_status_callback(self, callback):
        self._status_callback = callback

    def _emit_status(self, event_type: str, content: str):
        if self._status_callback:
            try:
                self._status_callback({"event": event_type, "content": content})
            except Exception as exc:
                logger.debug(f"[双层记忆] 状态回调发送失败: {exc}")
    
    def add_user_message(self, message: str) -> Optional[str]:
        with self._lock:
            self.chat_history.add_user_message(message)
            self.full_chat_history.add_user_message(message)
            self._turn_count += 1
            
            if self._should_compress():
                self._compress_history()
        
        logger.debug(f"[记忆] 添加用户消息，当前轮数: {self._turn_count}")
        return None
    
    def add_ai_message(self, message: str):
        with self._lock:
            self.chat_history.add_ai_message(message)
            self.full_chat_history.add_ai_message(message)
        
        logger.debug(f"[记忆] 添加AI消息")
    
    def add_ai_message_with_reasoning(self, message: str, reasoning: str = "", tool_calls: Optional[List[Dict[str, str]]] = None):
        with self._lock:
            self.chat_history.add_ai_message(message)
            
            ai_msg = AIMessage(content=message)
            metadata: Dict[str, Any] = {}
            if reasoning:
                metadata["reasoning"] = reasoning
            if tool_calls:
                metadata["tool_calls"] = [
                    {
                        "tool_name": str(item.get("tool_name", "")),
                        "tool_input": str(item.get("tool_input", "")),
                        "tool_output": str(item.get("tool_output", "")),
                    }
                    for item in tool_calls
                    if isinstance(item, dict)
                ]
            if metadata:
                ai_msg.additional_kwargs = metadata
            self.full_chat_history.messages.append(ai_msg)
        
        logger.debug(f"[记忆] 添加AI消息（含思考过程）")

    def add_ai_message_with_trace(self, message: str, reasoning: str = "", tool_calls: Optional[List[Dict[str, str]]] = None):
        self.add_ai_message_with_reasoning(message, reasoning=reasoning, tool_calls=tool_calls)

    def upsert_last_ai_message(self, message: str):
        normalized = (message or "").strip()
        if not normalized:
            return

        with self._lock:
            for history in (self.chat_history.messages, self.full_chat_history.messages):
                for idx in range(len(history) - 1, -1, -1):
                    msg = history[idx]
                    if isinstance(msg, AIMessage):
                        msg.content = normalized
                        break
                else:
                    history.append(AIMessage(content=normalized))

    def set_recent_reusable_result(self, content: str):
        with self._lock:
            self._recent_reusable_result = (content or "").strip()

    def get_recent_reusable_result(self) -> str:
        with self._lock:
            return self._recent_reusable_result

    def set_last_tool_use(self, tool_name: str, tool_input: str = "", tool_output: str = ""):
        with self._lock:
            self._last_tool_use = {
                "tool_name": (tool_name or "").strip(),
                "tool_input": (tool_input or "").strip(),
                "tool_output": (tool_output or "").strip(),
            }

    def get_last_tool_use(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._last_tool_use)
    
    def save_conversation(self, user_msg: str, ai_msg: str):
        self.save_chat_history()
    
    def get_messages(self) -> List[BaseMessage]:
        with self._lock:
            return list(self.chat_history.messages)
    
    def _estimate_tokens(self) -> int:
        total_chars = len(self._compressed_summary)
        for msg in self.chat_history.messages:
            total_chars += len(str(msg.content))
        return int(total_chars / self.CHARS_PER_TOKEN)

    def _should_compress(self) -> bool:
        if self._turn_count >= self.COMPRESS_THRESHOLD:
            return True
        estimated_tokens = self._estimate_tokens()
        budget = int(self.context_window * self.TOKEN_BUDGET_RATIO)
        return estimated_tokens > budget

    def _compress_history(self):
        messages = list(self.chat_history.messages)
        
        if len(messages) <= self.KEEP_RECENT_TURNS * 2:
            return
        
        old_messages = messages[:-self.KEEP_RECENT_TURNS * 2]
        recent_messages = messages[-self.KEEP_RECENT_TURNS * 2:]

        self._emit_status("compressing", "正在压缩较早的会话上下文，请稍候...")

        new_conversation_text = self.compressor._messages_to_text(old_messages)

        if self._compressed_summary:
            self._compressed_summary = self.compressor.distill(
                existing_summary=self._compressed_summary,
                new_conversation_text=new_conversation_text,
            )
        else:
            self._compressed_summary = self.compressor.compress(old_messages)
        
        self.chat_history = ChatMessageHistory()
        for msg in recent_messages:
            if isinstance(msg, HumanMessage):
                self.chat_history.add_user_message(msg.content)
            elif isinstance(msg, AIMessage):
                self.chat_history.add_ai_message(msg.content)
        
        self._turn_count = len(recent_messages) // 2

        logger.info(f"[上下文压缩] 完成，保留最近 {self._turn_count} 轮对话，摘要长度 {len(self._compressed_summary)} 字符")
        self._emit_status("compressed", f"会话压缩完成，已保留最近 {self._turn_count} 轮对话。")
    
    def _messages_to_text(self, messages: List[BaseMessage]) -> str:
        lines = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                lines.append(f"用户: {msg.content}")
            elif isinstance(msg, AIMessage):
                lines.append(f"助手: {msg.content}")
        return "\n".join(lines)

    @staticmethod
    def _extract_keywords(text: str, max_keywords: int = 5) -> List[str]:
        import re as _re
        segments = _re.split(r'[，。、；：？！\s,;:?!()\[\]{}]+\s*', text)
        keywords = [s.strip() for s in segments if s.strip() and 2 <= len(s) <= 20]
        return keywords[:max_keywords]
    
    def clear(self):
        with self._lock:
            self.chat_history.clear()
            self.full_chat_history.clear()
            self._compressed_summary = ""
            self._recent_reusable_result = ""
            self._last_tool_use = {}
            self._turn_count = 0
        logger.info("[记忆] 短期记忆已清空")

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "max_history": self.max_history,
                "current_rounds": self._turn_count,
                "message_count": len(self.chat_history.messages),
                "long_term_entries": len(self.long_term_memory.entries),
                "has_compressed_summary": bool(self._compressed_summary),
            }
    
    def add_long_term_memory(self, content: str, entry_type: str = "note") -> bool:
        return self.long_term_memory.add_entry(content, entry_type)
    
    def get_long_term_context(self, keywords: Optional[List[str]] = None) -> str:
        return self.long_term_memory.get_context(keywords=keywords)

    def search_history(self, keywords: Union[str, List[str]], max_results: int = 5) -> str:
        """
        搜索完整对话历史

        Args:
            keywords: 搜索关键词或正则表达式
            max_results: 最大结果数

        Returns:
            格式化后的搜索结果
        """
        return self.memory_search.search_conversation(keywords, max_results)

    def global_search(self, keywords: Union[str, List[str]], max_results: int = 10) -> str:
        """
        全局搜索（对话历史 + Skills）

        Args:
            keywords: 搜索关键词或正则表达式
            max_results: 最大结果数

        Returns:
            格式化后的搜索结果
        """
        return self.memory_search.search_all(keywords, max_results=max_results)

    def save_chat_history(self):
        """保存聊天历史到磁盘"""
        history_file = self.memory_dir / "chat_history.json"
        try:
            messages = []
            for msg in self.chat_history.messages:
                if isinstance(msg, HumanMessage):
                    messages.append({"type": "human", "content": msg.content})
                elif isinstance(msg, AIMessage):
                    messages.append({"type": "ai", "content": msg.content})

            full_messages = []
            for msg in self.full_chat_history.messages:
                if isinstance(msg, HumanMessage):
                    full_messages.append({"type": "human", "content": msg.content})
                elif isinstance(msg, AIMessage):
                    msg_data = {"type": "ai", "content": msg.content}
                    if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
                        reasoning = msg.additional_kwargs.get("reasoning", "")
                        tool_calls = msg.additional_kwargs.get("tool_calls", [])
                        if reasoning:
                            msg_data["reasoning"] = reasoning
                        if tool_calls:
                            msg_data["tool_calls"] = tool_calls
                    full_messages.append(msg_data)

            data = {
                "session_id": self.session_id,
                "messages": messages,
                "full_messages": full_messages,
                "compressed_summary": self._compressed_summary,
                "recent_reusable_result": self._recent_reusable_result,
                "last_tool_use": self._last_tool_use,
            }

            history_file.parent.mkdir(parents=True, exist_ok=True)
            history_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[双层记忆] 保存聊天历史: {len(messages)} 条消息（压缩），{len(full_messages)} 条消息（完整）到 {history_file}")
        except Exception as e:
            logger.error(f"[双层记忆] 保存聊天历史失败: {e}")

    def load_chat_history(self) -> List[Dict[str, str]]:
        """从磁盘加载聊天历史"""
        history_file = self.memory_dir / "chat_history.json"
        if not history_file.exists():
            return []

        try:
            data = json.loads(history_file.read_text(encoding="utf-8"))
            messages = data.get("messages", [])
            full_messages = data.get("full_messages", messages)
            self._compressed_summary = data.get("compressed_summary", "")
            self._recent_reusable_result = data.get("recent_reusable_result", "")
            self._last_tool_use = data.get("last_tool_use", {}) or {}

            self.chat_history = ChatMessageHistory()
            for msg in messages:
                if msg.get("type") == "human":
                    self.chat_history.add_user_message(msg.get("content", ""))
                elif msg.get("type") == "ai":
                    self.chat_history.add_ai_message(msg.get("content", ""))

            self.full_chat_history = ChatMessageHistory()
            for msg in full_messages:
                if msg.get("type") == "human":
                    self.full_chat_history.add_user_message(msg.get("content", ""))
                elif msg.get("type") == "ai":
                    ai_msg = AIMessage(content=msg.get("content", ""))
                    metadata: Dict[str, Any] = {}
                    if msg.get("reasoning"):
                        metadata["reasoning"] = msg.get("reasoning", "")
                    if msg.get("tool_calls"):
                        metadata["tool_calls"] = msg.get("tool_calls", [])
                    if metadata:
                        ai_msg.additional_kwargs = metadata
                    self.full_chat_history.messages.append(ai_msg)

            logger.info(f"[双层记忆] 加载聊天历史: {len(messages)} 条消息（压缩），{len(full_messages)} 条消息（完整）")
            return [
                {"role": "user" if m["type"] == "human" else "assistant", "content": m["content"]}
                for m in full_messages
            ]
        except Exception as e:
            logger.error(f"[双层记忆] 加载聊天历史失败: {e}")
            return []
    
    def get_chat_history_for_frontend(self) -> List[Dict[str, str]]:
        messages = []
        for msg in self.full_chat_history.messages:
            if isinstance(msg, HumanMessage):
                messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                msg_data = {"role": "assistant", "content": msg.content}
                if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
                    reasoning = msg.additional_kwargs.get("reasoning", "")
                    tool_calls = msg.additional_kwargs.get("tool_calls", [])
                    if reasoning:
                        msg_data["reasoning"] = reasoning
                    if tool_calls:
                        msg_data["tool_calls"] = tool_calls
                messages.append(msg_data)
        return messages
