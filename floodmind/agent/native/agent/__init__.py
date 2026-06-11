"""
Agent 角色系统。

FloodMind 支持多 Agent 角色协作，每个角色有自己的 prompt、工具权限和步数限制。

默认角色：
- build: 执行 agent，可调用所有工具（基于权限配置）
- plan: 规划 agent，禁止文件编辑，只允许 plan_exit 切换
"""

from .base import BaseAgent
from .build_agent import BuildAgent
from .config import AgentConfig, AgentRegistry, get_agent_registry
from .permissions import AgentPermission
from .plan_agent import PlanAgent

__all__ = [
    "BaseAgent",
    "BuildAgent",
    "PlanAgent",
    "AgentConfig",
    "AgentRegistry",
    "AgentPermission",
    "get_agent_registry",
]
