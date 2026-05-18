"""
全局搜索模块

跨会话搜索历史对话，不依赖 LangChain。
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from agent.runtime.contracts.messages import Message, ai_message, human_message

logger = logging.getLogger(__name__)

_MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
CHAT_HISTORY_DIR = os.path.join(_MEMORY_DIR, "chat_history")


class GlobalSearch:
    """全局搜索：跨会话搜索历史对话"""

    def __init__(self, memory_dir: Optional[str] = None):
        self.memory_dir = memory_dir or _MEMORY_DIR
        self.chat_history_dir = os.path.join(self.memory_dir, "chat_history")

    def search(
        self,
        query: str,
        top_k: int = 5,
        session_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """搜索历史对话"""
        if not os.path.exists(self.chat_history_dir):
            return []

        query_terms = set(re.findall(r"[一-龥A-Za-z0-9]{2,}", query.lower()))
        if not query_terms:
            return []

        results: List[Dict[str, Any]] = []

        for filename in os.listdir(self.chat_history_dir):
            if not filename.endswith(".json"):
                continue

            if session_filter and session_filter not in filename:
                continue

            filepath = os.path.join(self.chat_history_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    messages = json.load(f)

                for msg in messages:
                    content = msg.get("content", "")
                    if not content:
                        continue

                    content_terms = set(re.findall(r"[一-龥A-Za-z0-9]{2,}", content.lower()))
                    score = len(query_terms & content_terms) / max(len(query_terms), 1)

                    if score > 0:
                        results.append({
                            "content": content,
                            "role": msg.get("role", "unknown"),
                            "score": score,
                            "source": filename,
                            "timestamp": self._extract_timestamp(filename),
                        })
            except Exception as e:
                logger.error(f"[全局搜索] 读取文件失败 {filename}: {e}")

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def search_by_keywords(
        self,
        keywords: List[str],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """按关键词搜索"""
        query = " ".join(keywords)
        return self.search(query, top_k)

    def get_recent_sessions(self, n: int = 10) -> List[Dict[str, Any]]:
        """获取最近的会话列表"""
        if not os.path.exists(self.chat_history_dir):
            return []

        sessions = []
        for filename in os.listdir(self.chat_history_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(self.chat_history_dir, filename)
            try:
                stat = os.stat(filepath)
                sessions.append({
                    "filename": filename,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
            except Exception:
                continue

        sessions.sort(key=lambda x: x["modified"], reverse=True)
        return sessions[:n]

    def _extract_timestamp(self, filename: str) -> Optional[str]:
        """从文件名提取时间戳"""
        match = re.search(r"(\d{8}_\d{6})", filename)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S").isoformat()
            except ValueError:
                pass
        return None