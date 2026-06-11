"""
Agent 抽象基类。

每个 Agent 角色实现：
- get_system_prompt() — 角色特定的系统提示
- should_continue() — 判断当前状态是否继续执行
- get_tools() — 根据权限过滤可用工具
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .config import AgentConfig


class BaseAgent(ABC):
    """Agent 抽象基类。"""

    def __init__(self, config: AgentConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def steps(self) -> int:
        return self.config.steps

    def get_system_prompt(self, base_prompt: str) -> str:
        """根据角色追加系统提示。"""
        if self.config.prompt:
            return f"{base_prompt}\n\n【角色指令】\n{self.config.prompt}"
        return base_prompt

    def filter_tools(self, all_tools: List[Any]) -> List[Any]:
        """根据权限过滤工具列表。"""
        return self.config.permission.filter_tools(all_tools)

    def can_use_tool(self, tool_name: str) -> bool:
        """检查是否允许使用指定工具。"""
        return self.config.permission.can_use(tool_name)

    def is_last_step(self, step: int) -> bool:
        """检查是否已达到最大步数。"""
        return step >= self.config.steps

    def get_last_step_hint(self) -> str:
        """最后一步时注入的提示语。"""
        return (
            "[系统提示] 已达到最大步数限制，请直接给出最终回答，"
            "不要调用任何工具。总结当前已完成的成果并给出结论。"
        )
