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
        
        while len(self._agents) > max_sessions:
            oldest_id = next(iter(self._agents))
            self._evict_session(oldest_id, agent_factory)
    
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
        return session_id in self._agents
    
    def get_agent(self, session_id: str) -> Optional[Any]:
        """获取会话的 Agent"""
        session_id = validate_session_id(session_id)
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
                # 新格式：turns
                if "turns" in data:
                    for turn in data["turns"]:
                        content = turn.get("user_input", "")
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
        agent = self.get_agent(session_id)
        if agent and hasattr(agent, 'memory') and hasattr(agent.memory, 'get_chat_history_for_frontend'):
            try:
                live_messages = agent.memory.get_chat_history_for_frontend()
                if live_messages:
                    return live_messages
            except Exception as e:
                logger.warning(f"读取活动会话内存失败，回退到磁盘历史: {e}")

        # 新路径：会话目录下 memory/chat_history.json
        history_file = self.get_memory_dir(session_id) / "chat_history.json"
        if not history_file.exists():
            # 兼容旧路径：memory/chat_history/chat_{session_id}_*.json
            old_dir = Path(__file__).parent / "chat_history"
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
        """将新格式轮次转为前端消息列表

        每轮合并为一条 FloodMind 消息，包含 reasoning、tool_calls、final_answer，
        前端 fromServerMessage 会据此构建 thought/action/answer blocks 并自动折叠。
        """
        result = []
        for turn in turns:
            # 用户消息
            if turn.get("user_input"):
                result.append({
                    "role": "human",
                    "content": turn["user_input"],
                })
            # 合并本轮所有 AI 内容为一条消息
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
            # 只有有内容才添加
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
        """删除会话（包括所有数据）"""
        session_id = validate_session_id(session_id)
        with self._lock:
            if session_id in self._agents:
                del self._agents[session_id]
            
            if session_id in self._sessions:
                del self._sessions[session_id]
            
            session_dir = self.get_session_dir(session_id)
            if session_dir.exists():
                shutil.rmtree(session_dir)
            
            self._save_session_index()
            logger.info(f"删除会话: {session_id}")
    
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
                "data_dir": str(self.data_dir),
                "total_size_mb": round(total_size / 1024 / 1024, 2),
                "config": self.config,
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
