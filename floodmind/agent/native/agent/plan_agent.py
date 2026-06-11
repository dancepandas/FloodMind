"""
Plan Agent — 规划角色。

只读不编辑，专注于制定执行计划。
完成后通过 plan_exit 工具切换回 build agent。
"""

from typing import Optional

from .base import BaseAgent
from .config import AgentConfig


class PlanAgent(BaseAgent):
    """Plan agent：规划角色。"""

    PLAN_PROMPT = """你处于 **Plan 模式**。你的任务是制定详细的执行计划，但不要实际执行文件编辑或破坏性操作。

你可以：
- 读取文件、搜索代码、查询知识库
- 分析需求、拆解任务、制定步骤
- 使用 web_search/web_fetch 获取信息
- 使用 plan_exit 工具完成规划并切换到执行模式

你不可以：
- 编辑、写入、删除任何文件
- 运行可能修改系统状态的命令（如 git push、数据库写入）

规划完成后，调用 plan_exit 工具，系统会询问用户是否切换到 Build 模式开始执行。"""

    def __init__(self, config: Optional[AgentConfig] = None):
        if config is None:
            from .config import _DEFAULT_AGENTS
            config = _DEFAULT_AGENTS["plan"]
        super().__init__(config)

    def get_system_prompt(self, base_prompt: str) -> str:
        """追加 Plan 模式专属提示。"""
        prompt = super().get_system_prompt(base_prompt)
        return f"{prompt}\n\n{self.PLAN_PROMPT}"
