"""
Build Agent — 执行角色。

默认 agent，可调用所有工具（受权限配置限制）。
负责实际执行用户任务：编辑文件、运行命令、调用 API 等。
"""

from typing import Optional

from .base import BaseAgent
from .config import AgentConfig


class BuildAgent(BaseAgent):
    """Build agent：执行角色。"""

    def __init__(self, config: Optional[AgentConfig] = None):
        if config is None:
            from .config import _DEFAULT_AGENTS
            config = _DEFAULT_AGENTS["build"]
        super().__init__(config)
