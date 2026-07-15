"""
会话管理器

负责会话的创建、持久化、恢复和清理。
支持本地化部署场景，确保数据安全和服务稳定。
"""

import json
import logging
import os
import shutil
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger(__name__)


def validate_session_id(session_id: str) -> str:
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id 不能为空")
    if len(session_id) > 128:
        raise ValueError("session_id 过长")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(ch not in allowed for ch in session_id):
        raise ValueError("session_id 含非法字符")
    return session_id


@dataclass
class SessionInfo:
    """会话信息"""
    session_id: str
    created_at: str
    last_active: str
    title: str = ""
    message_count: int = 0
    status: str = "active"
    parent_session_id: Optional[str] = None
    branch_name: Optional[str] = None
    worktree_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def touch(self):
        """更新最后活跃时间"""
        self.last_active = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionInfo':
        return cls(**data)


class SessionManager:
    """
    会话管理器
    
    功能：
    - 会话创建和销毁
    - LRU 缓存淘汰
    - 会话持久化和恢复
    - 过期会话清理
    - 文件清理
    """
    
    DEFAULT_CONFIG = {
        "max_active_sessions": 10,
        "idle_timeout_minutes": 30,
        "session_retention_days": 30,
        "upload_retention_days": 7,
        "output_retention_days": 30,
        "cleanup_interval_minutes": 60,
        "data_dir": "./data",
    }
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        
        self.data_dir = Path(self.config["data_dir"])
        self.sessions_dir = self.data_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.worktrees_dir = self.data_dir / "worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        
        self._sessions: OrderedDict[str, SessionInfo] = OrderedDict()
        self._agents: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._running = False
        
        self._on_session_create: Optional[Callable] = None
        self._on_session_restore: Optional[Callable] = None
        self._on_session_evict: Optional[Callable] = None
        
        self._load_existing_sessions()
        
        logger.info(
            f"SessionManager 初始化 - "
            f"最大会话数: {self.config['max_active_sessions']}, "
            f"空闲超时: {self.config['idle_timeout_minutes']}分钟, "
            f"会话保留: {self.config['session_retention_days']}天"
        )
    
    def _load_existing_sessions(self):
        """加载已存在的会话索引"""
        index_file = self.sessions_dir / ".session_index.json"
        if index_file.exists():
            try:
                data = json.loads(index_file.read_text(encoding="utf-8"))
                for session_data in data.get("sessions", []):
                    info = SessionInfo.from_dict(session_data)
                    if info.status != "expired":
                        self._sessions[info.session_id] = info
                logger.info(f"加载 {len(self._sessions)} 个历史会话")
            except Exception as e:
                logger.error(f"加载会话索引失败: {e}")
        self._reconcile_session_index_with_disk()
    
    def _save_session_index(self):
        """保存会话索引"""
        index_file = self.sessions_dir / ".session_index.json"
        try:
            data = {
                "updated_at": datetime.now().isoformat(),
                "sessions": [info.to_dict() for info in self._sessions.values()]
            }
            index_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"保存会话索引失败: {e}")

    def _reconcile_session_index_with_disk(self):
        """根据磁盘上真实存在的 sessions 目录修正内存索引和 .session_index.json。"""
        try:
            existing_dirs = {
                path.name
                for path in self.sessions_dir.iterdir()
                if path.is_dir() and not path.name.startswith('.')
            }
        except FileNotFoundError:
            existing_dirs = set()

        # 移除索引中存在但磁盘上不存在的会话
        stale_session_ids = [session_id for session_id in self._sessions.keys() if session_id not in existing_dirs]
        for session_id in stale_session_ids:
            self._sessions.pop(session_id, None)
            self._agents.pop(session_id, None)

        # 添加磁盘上存在但索引中缺失的会话
        new_count = 0
        for session_id in existing_dirs - self._sessions.keys():
            session_file = self.sessions_dir / session_id / "session.json"
            if session_file.exists():
                try:
                    data = json.loads(session_file.read_text(encoding="utf-8"))
                    info = SessionInfo.from_dict(data)
                    self._sessions[session_id] = info
                    new_count += 1
                except Exception as e:
                    logger.warning(f"加载磁盘会话 {session_id} 失败: {e}")

        if stale_session_ids or new_count:
            self._save_session_index()
            if stale_session_ids:
                logger.info(f"已从会话索引中移除 {len(stale_session_ids)} 个不存在的会话目录")
            if new_count:
                logger.info(f"已从磁盘恢复 {new_count} 个缺失的会话")
    
    def set_callbacks(
        self,
        on_create: Optional[Callable] = None,
        on_restore: Optional[Callable] = None,
        on_evict: Optional[Callable] = None,
    ):
        """设置回调函数"""
        self._on_session_create = on_create
        self._on_session_restore = on_restore
        self._on_session_evict = on_evict
    
    def get_or_create_session(
        self,
        session_id: str,
        agent_factory: Optional[Callable] = None,
    ) -> tuple:
        """
        获取或创建会话
        
        Args:
            session_id: 会话ID
            agent_factory: 创建 Agent 的工厂函数
            
        Returns:
            (SessionInfo, agent) 元组
        """
        session_id = validate_session_id(session_id)
        with self._lock:
            if session_id in self._sessions:
                info = self._sessions[session_id]
                info.touch()
                self._sessions.move_to_end(session_id)
                
                if session_id not in self._agents:
                    agent = self._restore_session(session_id, agent_factory)
                else:
                    agent = self._agents[session_id]
                
                return info, agent
            
            info = SessionInfo(
                session_id=session_id,
                created_at=datetime.now().isoformat(),
                last_active=datetime.now().isoformat(),
                status="active",
            )
            
            self._sessions[session_id] = info
            self._create_session_dir(session_id)
            
            if agent_factory:
                agent = agent_factory(session_id)
                self._agents[session_id] = agent
                
                if self._on_session_create:
                    try:
                        self._on_session_create(session_id, agent)
                    except Exception as e:
                        logger.error(f"会话创建回调失败: {e}")
            
            self._evict_if_needed(agent_factory)
            self._save_session_index()
            
            logger.info(f"创建新会话: {session_id}")
            return info, self._agents.get(session_id)
    
    def _create_session_dir(self, session_id: str):
        """创建会话目录结构"""
        session_id = validate_session_id(session_id)
        session_dir = self.get_session_dir(session_id)
        (session_dir / "memory").mkdir(parents=True, exist_ok=True)
        (session_dir / "uploads").mkdir(parents=True, exist_ok=True)
        (session_dir / "outputs").mkdir(parents=True, exist_ok=True)
        
        session_file = session_dir / "session.json"
        if not session_file.exists():
            info = self._sessions.get(session_id)
            if info:
                session_file.write_text(
                    json.dumps(info.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
    
    def _restore_session(self, session_id: str, agent_factory: Optional[Callable]) -> Any:
        """从磁盘恢复会话"""
        session_id = validate_session_id(session_id)
        session_dir = self.get_session_dir(session_id)
        session_file = session_dir / "session.json"
        
        if session_file.exists():
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                info = SessionInfo.from_dict(data)
                info.status = "active"
                self._sessions[session_id] = info
                logger.info(f"从磁盘恢复会话: {session_id}")
            except Exception as e:
                logger.error(f"恢复会话失败: {e}")
        
        if agent_factory:
            agent = agent_factory(session_id)
            self._agents[session_id] = agent
            
            if self._on_session_restore:
                try:
                    self._on_session_restore(session_id, agent)
                except Exception as e:
                    logger.error(f"会话恢复回调失败: {e}")
            
            return agent
        
        return None
    
    def _evict_if_needed(self, agent_factory: Optional[Callable] = None):
        """LRU 淘汰：如果超过最大会话数，淘汰最久未使用的"""
        max_sessions = self.config["max_active_sessions"]
        max_attempts = len(self._agents) + len(self._sessions)

        while len(self._agents) > max_sessions and max_attempts > 0:
            max_attempts -= 1
            oldest_id = next(iter(self._sessions))
            if oldest_id in self._agents:
                self._evict_session(oldest_id, agent_factory)
            else:
                with self._lock:
                    self._sessions.pop(oldest_id, None)

    def _evict_session(self, session_id: str, agent_factory: Optional[Callable] = None):
        """淘汰会话（持久化后释放内存）"""
        session_id = validate_session_id(session_id)
        if session_id not in self._agents:
            return
        
        agent = self._agents[session_id]
        
        if hasattr(agent, 'memory') and hasattr(agent.memory, 'save_chat_history'):
            try:
                agent.memory.save_chat_history()
                logger.debug(f"会话 {session_id} 对话历史已保存")
            except Exception as e:
                logger.error(f"保存会话对话历史失败: {e}")
        
        if self._on_session_evict:
            try:
                self._on_session_evict(session_id, agent)
            except Exception as e:
                logger.error(f"会话淘汰回调失败: {e}")
        
        del self._agents[session_id]
        
        if session_id in self._sessions:
            self._sessions[session_id].status = "persisted"
        
        logger.info(f"淘汰会话（已持久化）: {session_id}")
    
    def get_session_dir(self, session_id: str) -> Path:
        """获取会话目录路径"""
        session_id = validate_session_id(session_id)
        return self.sessions_dir / session_id
    
    def get_memory_dir(self, session_id: str) -> Path:
        """获取会话记忆目录路径"""
        return self.get_session_dir(session_id) / "memory"
    
    def get_upload_dir(self, session_id: str) -> Path:
        """获取会话上传目录路径"""
        return self.get_session_dir(session_id) / "uploads"
    
    def get_output_dir(self, session_id: str) -> Path:
        """获取会话输出目录路径"""
        return self.get_session_dir(session_id) / "outputs"
    
    def touch_session(self, session_id: str):
        """更新会话活跃时间"""
        session_id = validate_session_id(session_id)
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].touch()
                self._sessions.move_to_end(session_id)
    
    def increment_message_count(self, session_id: str):
        """增加消息计数"""
        session_id = validate_session_id(session_id)
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].message_count += 1
                self._sessions[session_id].touch()

    def update_session_title(self, session_id: str, title: str):
        """更新会话标题"""
        session_id = validate_session_id(session_id)
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].title = title
                self._save_session_index()

    def get_active_sessions(self) -> List[SessionInfo]:
        """获取所有活跃会话"""
        with self._lock:
            self._reconcile_session_index_with_disk()
            return [
                info for info in self._sessions.values()
                if info.status == "active"
            ]
    
    def get_all_sessions(self) -> List[SessionInfo]:
        """获取所有会话"""
        with self._lock:
            self._reconcile_session_index_with_disk()
            return list(self._sessions.values())
    
    def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """获取会话信息"""
        session_id = validate_session_id(session_id)
        with self._lock:
            self._reconcile_session_index_with_disk()
            return self._sessions.get(session_id)
    
    def has_active_agent(self, session_id: str) -> bool:
        """检查会话是否有活跃的 Agent"""
        session_id = validate_session_id(session_id)
        with self._lock:
            return session_id in self._agents

    def get_agent(self, session_id: str) -> Optional[Any]:
        """获取会话的 Agent"""
        session_id = validate_session_id(session_id)
        with self._lock:
            return self._agents.get(session_id)
    
    def get_session_title(self, session_id: str) -> str:
        """获取会话标题（优先使用保存的标题，否则从第一条用户消息提取）"""
        session_id = validate_session_id(session_id)
        info = self._sessions.get(session_id)
        if info and info.title:
            return info.title

        history_file = self.get_memory_dir(session_id) / "chat_history.json"
        if history_file.exists():
            try:
                data = json.loads(history_file.read_text(encoding="utf-8"))
                # 新格式：turns（扁平 role/content 或旧 per-turn）
                if "turns" in data:
                    for turn in data["turns"]:
                        # 扁平 user 条目
                        if turn.get("role") == "user":
                            content = turn.get("content", "")
                        else:
                            content = turn.get("user_input", "")  # 旧 per-turn 格式
                        if content:
                            return self._extract_title_from_user_input(content)
                # 旧格式：messages
                messages = data.get("messages", [])
                for msg in messages:
                    if msg.get("type") == "human":
                        content = msg.get("content", "")
                        if content:
                            return self._extract_title_from_user_input(content)
            except Exception:
                pass

        if info:
            return "新会话"

        return "新会话"

    @staticmethod
    def _extract_title_from_user_input(content: str) -> str:
        """从用户输入中提取标题，去除环境信息前缀"""
        text = content.strip()
        # 去除 [会话环境信息]... 前缀块，找到环境信息结束后的实际用户消息
        if "[会话环境信息]" in text or "[已上传的文件]" in text:
            # 找到最后一个环境信息标记之后的内容
            markers = ["[会话环境信息]", "[已上传的文件]"]
            last_end = 0
            for marker in markers:
                idx = text.find(marker)
                if idx >= 0:
                    # 找到该标记所在段落的结束位置（下一个空行或下一个标记）
                    end = text.find("\n\n", idx)
                    if end < 0:
                        end = len(text)
                    else:
                        end += 2
                    last_end = max(last_end, end)
            # 也跳过环境信息中的键值行
            remaining = text[last_end:].strip()
            # 过滤掉残留的环境信息行
            lines = []
            for line in remaining.split("\n"):
                s = line.strip()
                if not s:
                    continue
                if s.startswith("输出目录:") or s.startswith("上传目录:") or s.startswith("生成文件时"):
                    continue
                if s.startswith("- 文件名:") or s.startswith("用户提到"):
                    continue
                if s.startswith("路径:"):
                    continue
                lines.append(s)
            if lines:
                text = " ".join(lines)
        title = text[:50] + ("..." if len(text) > 50 else "")
        return title
    
    def get_session_messages(self, session_id: str) -> List[Dict[str, str]]:
        """获取会话的对话历史（用于前端恢复）"""
        session_id = validate_session_id(session_id)

        # 会话目录下 memory/chat_history.json（DualMemory._turns 持久化）
        history_file = self.get_memory_dir(session_id) / "chat_history.json"
        if not history_file.exists():
            # 兼容旧路径：memory/chat_history/chat_{session_id}_*.json
            old_dir = Path.cwd() / "data" / "chat_history"
            old_files = sorted(old_dir.glob(f"chat_{session_id}_*.json")) if old_dir.exists() else []
            if old_files:
                history_file = old_files[-1]
            else:
                return []

        try:
            data = json.loads(history_file.read_text(encoding="utf-8"))
            # 新格式：{"turns": [...], "short_term": [...], ...}
            if "turns" in data:
                return self._turns_to_frontend(data["turns"])
            # 旧格式：{"messages": [...]} 或 [...]
            messages = data.get("full_messages", data.get("messages", []))
            if isinstance(data, list):
                messages = data
            return self._legacy_messages_to_frontend(messages)
        except Exception as e:
            logger.error(f"获取会话消息失败: {e}")
            return []

    @staticmethod
    def _turns_to_frontend(turns: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """将对话历史转为前端消息列表。

        支持两种 _turns 格式：
        - 扁平条目（role/content）：当前架构，一次 LLM 调用一条 assistant 条目。
          同一用户轮的多条 assistant 条目合并为一条 FloodMind 消息（保留原 per-turn UX）。
        - 旧 per-turn 字典（user_input/final_answer）：迁移前的格式，按原逻辑渲染。
        前端 fromServerMessage 据此构建 thought/action/answer blocks 并自动折叠。
        """
        if not turns:
            return []
        # 旧格式（无 role 键）：per-turn 字典
        if "role" not in turns[0]:
            return SessionManager._legacy_turns_to_frontend(turns)

        # 扁平条目：按用户轮聚合 assistant 条目
        result: List[Dict[str, Any]] = []
        pending_ai: Optional[Dict[str, Any]] = None

        def _flush() -> None:
            nonlocal pending_ai
            if pending_ai and (pending_ai.get("reasoning") or pending_ai.get("tool_calls") or pending_ai.get("content")):
                result.append(pending_ai)
            pending_ai = None

        for e in turns:
            role = e.get("role")
            if role == "user":
                _flush()
                content = e.get("content", "")
                if content:
                    result.append({"role": "human", "content": content})
            elif role == "assistant":
                if pending_ai is None:
                    pending_ai = {"role": "FloodMind", "content": "", "reasoning": "", "tool_calls": []}
                reasoning = e.get("reasoning", "")
                if reasoning:
                    pending_ai["reasoning"] = (
                        pending_ai["reasoning"] + "\n" + reasoning
                    ) if pending_ai["reasoning"] else reasoning
                for tc in (e.get("tool_calls") or []):
                    pending_ai["tool_calls"].append({
                        "tool_name": tc.get("tool_name", tc.get("name", "unknown")),
                        "tool_output": tc.get("tool_output", tc.get("result", "")),
                    })
                content = e.get("content", "")
                if content:
                    pending_ai["content"] = content  # 最后一条非空 assistant 内容（= 终态回答）
        _flush()
        return result

    @staticmethod
    def _legacy_turns_to_frontend(turns: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """旧 per-turn 格式（user_input/final_answer）转前端消息列表。"""
        result: List[Dict[str, Any]] = []
        for turn in turns:
            if turn.get("user_input"):
                result.append({"role": "human", "content": turn["user_input"]})
            ai_parts: Dict[str, Any] = {"role": "FloodMind", "content": ""}
            if turn.get("reasoning"):
                ai_parts["reasoning"] = turn["reasoning"]
            tool_calls = turn.get("tool_calls", [])
            if tool_calls:
                ai_parts["tool_calls"] = [
                    {
                        "tool_name": tc.get("tool_name", tc.get("name", "unknown")),
                        "tool_output": tc.get("tool_output", tc.get("result", "")),
                    }
                    for tc in tool_calls
                ]
            if turn.get("final_answer"):
                ai_parts["content"] = turn["final_answer"]
            if ai_parts.get("reasoning") or ai_parts.get("tool_calls") or ai_parts.get("content"):
                result.append(ai_parts)
        return result

    @staticmethod
    def _legacy_messages_to_frontend(messages: list) -> List[Dict[str, str]]:
        """将旧格式消息转为前端格式"""
        result = []
        for m in messages:
            msg_data = {
                "role": "human" if m.get("type") == "human" else "FloodMind",
                "content": m.get("content", "")
            }
            if m.get("reasoning"):
                msg_data["reasoning"] = m.get("reasoning", "")
            if m.get("tool_calls"):
                msg_data["tool_calls"] = m.get("tool_calls", [])
            result.append(msg_data)
        return result
    
    def delete_session(self, session_id: str):
        """删除会话（包括所有数据和工作树）"""
        session_id = validate_session_id(session_id)
        with self._lock:
            if session_id in self._agents:
                del self._agents[session_id]

            if session_id in self._sessions:
                del self._sessions[session_id]

            session_dir = self.get_session_dir(session_id)
            if session_dir.exists():
                shutil.rmtree(session_dir)

            # 清理关联的 worktree（按元数据精确匹配，避免前缀误删）
            for wt_dir in list(self.worktrees_dir.iterdir()):
                if not wt_dir.is_dir():
                    continue
                meta = self._read_worktree_meta(wt_dir)
                wt_session_id = meta.get("session_id") if meta else wt_dir.name.split("-", 1)[0]
                if wt_session_id == session_id:
                    try:
                        shutil.rmtree(wt_dir)
                    except Exception as e:
                        logger.warning(f"清理 worktree 失败: {wt_dir} — {e}")

            self._save_session_index()
            logger.info(f"删除会话: {session_id}")

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有会话。返回 [{id, title, msg_count, last_active, status}]"""
        with self._lock:
            result = []
            for sid, info in self._sessions.items():
                result.append({
                    "id": sid,
                    "title": info.title or "Untitled",
                    "msg_count": info.message_count,
                    "last_active": info.last_active,
                    "status": info.status,
                })
            result.sort(key=lambda x: x["last_active"], reverse=True)
            return result

    def rename_session(self, session_id: str, title: str) -> None:
        """重命名会话。"""
        session_id = validate_session_id(session_id)
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].title = title
                self._save_session_index()
                logger.info(f"会话重命名: {session_id} → {title}")

    def fork_session(self, session_id: str, from_message_id: str = "") -> str:
        """分叉会话：复制到指定消息，创建新会话。"""
        import uuid
        new_id = str(uuid.uuid4())[:12]
        session_id = validate_session_id(session_id)
        messages = self.get_session_messages(session_id)

        # 截断到指定消息
        if from_message_id:
            cut_idx = next((i for i, m in enumerate(messages) if m.get("id") == from_message_id), len(messages))
            messages = messages[:cut_idx + 1]

        # 在新会话中保存消息
        for msg in messages:
            self.save_message(new_id, msg.get("role", "user"), msg.get("content", ""))

        logger.info(f"会话分叉: {session_id} → {new_id} ({len(messages)} messages)")
        return new_id

    def get_worktree_dir(self, session_id: str, branch_name: str = "") -> Path:
        """获取工作树目录"""
        session_id = validate_session_id(session_id)
        branch_name = branch_name.strip() or session_id
        # 安全处理：将非法文件名字符替换为 _
        safe_branch = "".join(c if c.isalnum() or c in "-_." else "_" for c in branch_name)
        return self.worktrees_dir / f"{session_id}-{safe_branch}"

    def _worktree_meta_file(self, worktree_dir: Path) -> Path:
        """worktree 元数据文件路径"""
        return worktree_dir / ".floodmind_worktree.json"

    def _read_worktree_meta(self, worktree_dir: Path) -> Optional[Dict[str, str]]:
        """读取 worktree 元数据（session_id / branch_name）"""
        meta_file = self._worktree_meta_file(worktree_dir)
        if not meta_file.exists():
            return None
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_worktree_meta(
        self, worktree_dir: Path, session_id: str, branch_name: str
    ) -> None:
        """写入 worktree 元数据"""
        try:
            meta_file = self._worktree_meta_file(worktree_dir)
            meta_file.write_text(
                json.dumps(
                    {"session_id": session_id, "branch_name": branch_name},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"写入 worktree 元数据失败: {worktree_dir} — {e}")

    def create_worktree(
        self, session_id: str, branch_name: str = "",
        base_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """创建 git worktree 隔离的会话工作区。

        Args:
            session_id: 父会话 ID
            branch_name: 分支名（可选，默认根据 session_id 生成）
            base_path: git 仓库根路径（可选，默认项目根目录）

        Returns:
            {"success": bool, "worktree_path": str, "branch_name": str, "error": str}
        """
        import subprocess

        session_id = validate_session_id(session_id)
        branch_name = branch_name.strip() or f"branch-{session_id[:8]}"
        worktree_dir = self.get_worktree_dir(session_id, branch_name)
        # 桌面端/编译模式下 CWD 不可靠，优先使用显式项目根
        repo_path = base_path or os.environ.get('FLOODMIND_PROJECT_ROOT') or os.getcwd()

        # 避免重复创建
        if worktree_dir.exists():
            self._write_worktree_meta(worktree_dir, session_id, branch_name)
            logger.info(f"Worktree 已存在: {worktree_dir}")
            return {
                "success": True,
                "worktree_path": str(worktree_dir),
                "branch_name": branch_name,
            }

        try:
            result = subprocess.run(
                ["git", "worktree", "add", str(worktree_dir), "-b", branch_name],
                cwd=repo_path,
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                # 写入元数据，避免 session_id 含 '-' 时目录名解析歧义
                self._write_worktree_meta(worktree_dir, session_id, branch_name)
                # 更新会话信息
                with self._lock:
                    if session_id in self._sessions:
                        self._sessions[session_id].worktree_path = str(worktree_dir)
                        self._sessions[session_id].branch_name = branch_name
                        self._save_session_index()
                logger.info(f"Worktree 创建成功: {worktree_dir} (branch: {branch_name})")
                return {
                    "success": True,
                    "worktree_path": str(worktree_dir),
                    "branch_name": branch_name,
                }
            else:
                error_msg = result.stderr.strip()
                logger.error(f"Worktree 创建失败: {error_msg}")
                return {"success": False, "error": error_msg}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "git worktree add 超时"}
        except FileNotFoundError:
            return {"success": False, "error": "未找到 git 命令，请确保已安装 Git"}
        except Exception as e:
            logger.error(f"Worktree 创建异常: {e}")
            return {"success": False, "error": str(e)}

    def list_worktrees(self, session_id: str = "") -> List[Dict[str, Any]]:
        """列出所有工作树（或指定会话的工作树）"""
        result = []
        session_id = validate_session_id(session_id) if session_id else ""

        if not self.worktrees_dir.exists():
            return result

        for wt_dir in self.worktrees_dir.iterdir():
            if not wt_dir.is_dir():
                continue

            # 优先读取元数据，避免目录名解析歧义
            meta = self._read_worktree_meta(wt_dir)
            if meta:
                sid = meta.get("session_id", "")
                branch = meta.get("branch_name", "")
            else:
                # 兼容旧格式：按目录名解析（session_id-branch_name）
                parts = wt_dir.name.split("-", 1)
                sid = parts[0] if parts else wt_dir.name
                branch = parts[1] if len(parts) > 1 else ""

            if session_id and sid != session_id:
                continue

            result.append({
                "id": wt_dir.name,
                "session_id": sid,
                "branch_name": branch,
                "path": str(wt_dir),
                "created_at": datetime.fromtimestamp(wt_dir.stat().st_ctime).isoformat(),
            })

        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result

    def remove_worktree(self, session_id: str, branch_name: str = "") -> Dict[str, Any]:
        """删除工作树。

        返回: {"success": bool, "error": str}
        """
        import subprocess

        session_id = validate_session_id(session_id)
        worktree_dir = self.get_worktree_dir(session_id, branch_name)

        if not worktree_dir.exists():
            return {"success": False, "error": f"Worktree 不存在: {worktree_dir}"}

        try:
            # git worktree remove（清理 git 元数据 + 删除目录）
            result = subprocess.run(
                ["git", "worktree", "remove", str(worktree_dir), "--force"],
                capture_output=True, text=True, timeout=30,
            )
            # 无论 git 是否成功，只要目录还在就强制清理
            if worktree_dir.exists():
                shutil.rmtree(worktree_dir)
            if result.returncode == 0 or not worktree_dir.exists():
                # 更新会话信息
                with self._lock:
                    if session_id in self._sessions:
                        self._sessions[session_id].worktree_path = None
                        self._sessions[session_id].branch_name = None
                        self._save_session_index()
                logger.info(f"Worktree 已删除: {worktree_dir}")
                return {"success": True}
            else:
                return {"success": False, "error": result.stderr.strip()}
        except subprocess.TimeoutExpired:
            # 超时后仍尝试强制删除目录
            if worktree_dir.exists():
                try:
                    shutil.rmtree(worktree_dir)
                    return {"success": True}
                except Exception as e:
                    return {"success": False, "error": f"git worktree remove 超时且目录删除失败: {e}"}
            return {"success": False, "error": "git worktree remove 超时"}
        except FileNotFoundError:
            # git 未安装时直接删除目录
            if worktree_dir.exists():
                try:
                    shutil.rmtree(worktree_dir)
                    with self._lock:
                        if session_id in self._sessions:
                            self._sessions[session_id].worktree_path = None
                            self._sessions[session_id].branch_name = None
                            self._save_session_index()
                    return {"success": True}
                except Exception as e:
                    return {"success": False, "error": f"未找到 git 命令且目录删除失败: {e}"}
            return {"success": False, "error": "未找到 git 命令"}
        except Exception as e:
            logger.error(f"Worktree 删除异常: {e}")
            return {"success": False, "error": str(e)}

    def fork_to_worktree(
        self, session_id: str, branch_name: str = "", from_message_id: str = "",
    ) -> Dict[str, Any]:
        """分叉会话到新的 worktree 工作区。

        先调用 fork_session 创建新会话，再为新会话创建 worktree。
        """
        new_id = self.fork_session(session_id, from_message_id)
        branch = branch_name.strip() or f"fork-{new_id[:8]}"
        result = self.create_worktree(new_id, branch)
        if result["success"]:
            # 标记新会话的父关系和分支
            with self._lock:
                if new_id in self._sessions:
                    self._sessions[new_id].parent_session_id = session_id
                    self._sessions[new_id].branch_name = branch
                    self._sessions[new_id].worktree_path = result["worktree_path"]
                    self._save_session_index()
            logger.info(f"会话已分叉到 worktree: {session_id} → {new_id} ({branch})")
        return {
            **result,
            "new_session_id": new_id,
            "parent_session_id": session_id,
        }

    def export_session(self, session_id: str) -> str:
        """导出会话为 Markdown。"""
        messages = self.get_session_messages(session_id)
        info = self.get_session_info(session_id)
        title = info.title if info else session_id
        lines = [f"# {title}", "", f"Session: {session_id}", f"Exported: {datetime.now().isoformat()}", ""]
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")
            if ts:
                lines.append(f"### {role} ({ts})")
            else:
                lines.append(f"### {role}")
            lines.append("")
            lines.append(content)
            lines.append("")
        return "\n".join(lines)

    def save_message(self, session_id: str, role: str, content: str, tool_name: str = "", msg_id: str = "") -> None:
        """保存单条消息到会话。"""
        import uuid
        session_id = validate_session_id(session_id)
        msg_id = msg_id or uuid.uuid4().hex[:16]
        msg = {"id": msg_id, "role": role, "content": content, "timestamp": datetime.now().isoformat()}
        if tool_name:
            msg["tool_name"] = tool_name
        with self._lock:
            msg_file = self.get_session_dir(session_id) / f"msg_{msg_id}.json"
            msg_file.parent.mkdir(parents=True, exist_ok=True)
            with open(msg_file, "w", encoding="utf-8") as f:
                json.dump(msg, f, ensure_ascii=False, indent=2)
            if session_id in self._sessions:
                self._sessions[session_id].message_count += 1
                self._sessions[session_id].touch()
    
    def start_cleanup_thread(self):
        """启动清理线程"""
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            return
        
        self._running = True
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="session-cleanup"
        )
        self._cleanup_thread.start()
        logger.info("会话清理线程已启动")
    
    def stop_cleanup_thread(self):
        """停止清理线程"""
        self._running = False
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)
        logger.info("会话清理线程已停止")
    
    def _cleanup_loop(self):
        """清理循环"""
        while self._running:
            try:
                self.cleanup_expired_sessions()
                self.cleanup_old_files()
            except Exception as e:
                logger.error(f"清理任务失败: {e}")
            
            interval = self.config["cleanup_interval_minutes"] * 60
            for _ in range(int(interval)):
                if not self._running:
                    break
                time.sleep(1)
    
    def cleanup_expired_sessions(self):
        """清理过期会话"""
        retention_days = self.config["session_retention_days"]
        cutoff = datetime.now() - timedelta(days=retention_days)
        
        to_delete = []
        
        with self._lock:
            for session_id, info in list(self._sessions.items()):
                last_active = datetime.fromisoformat(info.last_active)
                if last_active < cutoff:
                    to_delete.append(session_id)
        
        for session_id in to_delete:
            self.delete_session(session_id)
            logger.info(f"清理过期会话: {session_id}")
        
        if to_delete:
            logger.info(f"清理了 {len(to_delete)} 个过期会话")
    
    def cleanup_old_files(self):
        """清理旧文件"""
        upload_retention = self.config["upload_retention_days"]
        output_retention = self.config["output_retention_days"]
        
        upload_cutoff = datetime.now() - timedelta(days=upload_retention)
        output_cutoff = datetime.now() - timedelta(days=output_retention)
        
        cleaned_uploads = 0
        cleaned_outputs = 0
        
        for session_dir in self.sessions_dir.iterdir():
            if not session_dir.is_dir() or session_dir.name.startswith('.'):
                continue
            
            uploads_dir = session_dir / "uploads"
            if uploads_dir.exists():
                for file_path in uploads_dir.iterdir():
                    if file_path.is_file():
                        mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                        if mtime < upload_cutoff:
                            file_path.unlink()
                            cleaned_uploads += 1
            
            outputs_dir = session_dir / "outputs"
            if outputs_dir.exists():
                for file_path in outputs_dir.iterdir():
                    if file_path.is_file():
                        mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                        if mtime < output_cutoff:
                            file_path.unlink()
                            cleaned_outputs += 1
        
        if cleaned_uploads or cleaned_outputs:
            logger.info(
                f"清理文件: 上传文件 {cleaned_uploads} 个, "
                f"输出文件 {cleaned_outputs} 个"
            )
    
    def cleanup_idle_sessions(self):
        """清理空闲超时的会话（释放内存但保留数据）"""
        idle_timeout = self.config["idle_timeout_minutes"]
        cutoff = datetime.now() - timedelta(minutes=idle_timeout)
        
        evicted = []
        
        with self._lock:
            for session_id, info in list(self._sessions.items()):
                if session_id not in self._agents:
                    continue
                
                last_active = datetime.fromisoformat(info.last_active)
                if last_active < cutoff:
                    self._evict_session(session_id, None)
                    evicted.append(session_id)
        
        if evicted:
            logger.info(f"释放 {len(evicted)} 个空闲会话的内存")
        
        return evicted
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            total_sessions = len(self._sessions)
            active_agents = len(self._agents)
            
            total_size = 0
            for session_dir in self.sessions_dir.iterdir():
                if session_dir.is_dir() and not session_dir.name.startswith('.'):
                    for file_path in session_dir.rglob('*'):
                        if file_path.is_file():
                            total_size += file_path.stat().st_size
            
            return {
                "total_sessions": total_sessions,
                "active_agents": active_agents,
                "max_sessions": self.config["max_active_sessions"],
                "total_size_mb": round(total_size / 1024 / 1024, 2),
            }
    
    def save_all(self):
        """保存所有会话状态"""
        with self._lock:
            self._save_session_index()
            
            for session_id, info in self._sessions.items():
                session_file = self.get_session_dir(session_id) / "session.json"
                try:
                    session_file.write_text(
                        json.dumps(info.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )
                except Exception as e:
                    logger.error(f"保存会话 {session_id} 失败: {e}")
        
        logger.info("所有会话状态已保存")
