"""
上下文运行时

管理系统提示、项目规则、会话上下文等，不依赖 LangChain。
"""

import logging
import os
import platform
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.runtime.contracts.messages import system_message

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ContextPriority(IntEnum):
    SYSTEM_PROMPT = 0
    PROJECT_RULES = 10
    SKILL_CATALOG = 20
    TASK_EXPERIENCE = 25
    LONG_TERM_MEMORY = 30
    SYSTEM_ENV = 40
    CURRENT_TIME = 50
    RECENT_RESULT = 60
    LAST_TOOL_USE = 70
    RAG_CONTEXT = 80


@dataclass
class ContextBlock:
    priority: ContextPriority
    label: str
    content: str
    cache_ttl: int = 0
    _cached_content: Optional[str] = None
    _cache_ts: float = 0.0

    def get_content(self) -> str:
        if self.cache_ttl <= 0:
            return self.content
        now = time.time()
        if self._cached_content is not None and (now - self._cache_ts) < self.cache_ttl:
            return self._cached_content
        self._cached_content = self.content
        self._cache_ts = now
        return self._cached_content


class ContextRuntime:
    def __init__(
        self,
        context_window: int = 32768,
        chars_per_token: float = 1.5,
        project_context_ttl: int = 60,
    ):
        self.context_window = context_window
        self.chars_per_token = chars_per_token
        self.project_context_ttl = project_context_ttl
        self._blocks: Dict[str, ContextBlock] = {}
        self._agents_md_cache: Optional[str] = None
        self._agents_md_cache_ts: float = 0.0
        self._system_env_cache: Optional[str] = None
        self._system_env_cache_ts: float = 0.0

    def _estimate_text_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / self.chars_per_token))

    def _truncate_to_budget(self, text: str, remaining_tokens: int) -> str:
        if remaining_tokens <= 0:
            return ""
        max_chars = max(1, int(remaining_tokens * self.chars_per_token))
        if len(text) <= max_chars:
            return text
        if max_chars <= 16:
            return text[:max_chars]
        return text[: max_chars - 16].rstrip() + "\n...[已截断]"

    def set_project_rules(self, content: str) -> None:
        self._blocks["project_rules"] = ContextBlock(
            priority=ContextPriority.PROJECT_RULES,
            label="项目规则",
            content=content,
            cache_ttl=self.project_context_ttl,
        )

    def set_long_term_memory(self, content: str) -> None:
        self._blocks["long_term_memory"] = ContextBlock(
            priority=ContextPriority.LONG_TERM_MEMORY,
            label="长期记忆",
            content=content,
        )

    def set_current_time(self, content: str) -> None:
        self._blocks["current_time"] = ContextBlock(
            priority=ContextPriority.CURRENT_TIME,
            label="当前时间",
            content=content,
        )

    def set_system_env(self, content: str) -> None:
        self._blocks["system_env"] = ContextBlock(
            priority=ContextPriority.SYSTEM_ENV,
            label="系统环境",
            content=content,
            cache_ttl=300,
        )

    def set_recent_result(self, content: str) -> None:
        self._blocks["recent_result"] = ContextBlock(
            priority=ContextPriority.RECENT_RESULT,
            label="最近结果",
            content=content,
        )

    def set_last_tool_use(self, content: str) -> None:
        self._blocks["last_tool_use"] = ContextBlock(
            priority=ContextPriority.LAST_TOOL_USE,
            label="最近工具调用",
            content=content,
        )

    def set_rag_context(self, content: str) -> None:
        self._blocks["rag_context"] = ContextBlock(
            priority=ContextPriority.RAG_CONTEXT,
            label="知识检索",
            content=content,
        )

    def load_project_rules(self) -> str:
        now = time.time()
        if self._agents_md_cache is not None and (now - self._agents_md_cache_ts) < self.project_context_ttl:
            return self._agents_md_cache

        parts = []

        global_path = Path.home() / ".floodagent" / "AGENTS.md"
        global_ctx = self._read_agents_md(global_path, label="全局级指令")
        if global_ctx:
            parts.append(global_ctx)

        cwd_path = Path.cwd() / "AGENTS.md"
        project_path = _PROJECT_ROOT / "AGENTS.md"
        project_ctx = self._read_agents_md(cwd_path, label="项目级指令")
        if not project_ctx:
            project_ctx = self._read_agents_md(project_path, label="项目级指令")
        if project_ctx:
            parts.append(project_ctx)

        result = "\n\n".join(parts)
        self._agents_md_cache = result
        self._agents_md_cache_ts = now
        return result

    def load_current_time(self) -> str:
        return self.load_current_time_static()

    @staticmethod
    def load_current_time_static() -> str:
        now = datetime.now().astimezone()
        timezone_name = now.tzname() or "本地时区"
        return (
            f"当前系统时间: {now.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
            f"ISO时间: {now.isoformat()}\n"
            f"当前时区: {timezone_name}\n"
            f"今天是: {now.strftime('%Y-%m-%d')}\n"
            f"当前星期: 星期{'一二三四五六日'[now.weekday()]}"
        )

    def load_system_env(self) -> str:
        return self.load_system_env_static()

    @staticmethod
    def load_system_env_static() -> str:
        shell_name = "powershell.exe / pwsh" if os.name == "nt" else "bash / sh"
        path_style = "Windows" if os.name == "nt" else "POSIX"
        return (
            f"操作系统: {platform.system()} {platform.release()}\n"
            f"Python 版本: {platform.python_version()}\n"
            f"exec_bash shell 策略: 自动选择当前可用 shell\n"
            f"当前环境优先 shell: {shell_name}\n"
            f"路径风格: {path_style}"
        )

    def prefetch(self) -> None:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self.load_project_rules): "project_rules",
                executor.submit(self.load_current_time): "current_time",
                executor.submit(self.load_system_env): "system_env",
            }
            for future in as_completed(futures):
                label = futures[future]
                try:
                    content = future.result()
                    if content:
                        if label == "project_rules":
                            self.set_project_rules(content)
                        elif label == "current_time":
                            self.set_current_time(content)
                        elif label == "system_env":
                            self.set_system_env(content)
                except Exception as e:
                    logger.warning(f"[ContextRuntime] 预取 {label} 失败: {e}")

    def build_context_messages(self) -> List[Dict[str, str]]:
        """构建上下文消息列表，返回 OpenAI 格式的 dict 列表"""
        sorted_blocks = sorted(self._blocks.values(), key=lambda b: b.priority)
        messages = []
        used_tokens = 0
        for block in sorted_blocks:
            content = block.get_content()
            if content and content.strip():
                block_tokens = self._estimate_text_tokens(content)
                remaining_tokens = self.context_window - used_tokens
                if remaining_tokens <= 0:
                    break
                if block_tokens <= remaining_tokens:
                    messages.append({"role": "system", "content": content})
                    used_tokens += block_tokens
                    continue
                if not messages:
                    truncated = self._truncate_to_budget(content, remaining_tokens)
                    if truncated.strip():
                        messages.append({"role": "system", "content": truncated})
                    break
        return messages

    def estimate_tokens(self) -> int:
        total_chars = sum(len(b.get_content()) for b in self._blocks.values())
        return int(total_chars / self.chars_per_token)

    def invalidate_cache(self, label: Optional[str] = None) -> None:
        if label and label in self._blocks:
            self._blocks[label]._cached_content = None
            self._blocks[label]._cache_ts = 0.0
            if label == "project_rules":
                self._agents_md_cache = None
                self._agents_md_cache_ts = 0.0
            return
        self._agents_md_cache = None
        self._agents_md_cache_ts = 0.0
        self._system_env_cache = None
        self._system_env_cache_ts = 0.0
        for block in self._blocks.values():
            block._cached_content = None
            block._cache_ts = 0.0

    @staticmethod
    def _read_agents_md(path: Path, label: str = "项目级指令") -> str:
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                return ""
            lines = content.splitlines()
            filtered = [line for line in lines if not line.startswith("#! ")]
            body = "\n".join(filtered)
            if not body.strip():
                return ""
            return f"## {label}（来自 {path.name}）\n{body}"
        except Exception as e:
            logger.warning(f"读取 {path} 失败: {e}")
            return ""