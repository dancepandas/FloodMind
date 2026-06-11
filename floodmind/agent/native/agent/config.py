"""
Agent 配置注册表。

从 settings.json 的 agent 段加载配置，支持多角色、权限、步数限制。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .permissions import AgentPermission


@dataclass
class AgentConfig:
    """单个 Agent 角色的配置。"""

    name: str
    description: str = ""
    prompt: str = ""  # 系统提示附加内容
    steps: int = 50  # 最大迭代步数
    permission: AgentPermission = field(default_factory=lambda: AgentPermission(default="allow"))
    options: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "AgentConfig":
        """从 settings.json 配置解析。"""
        perm_data = data.get("permission", {})
        if "tools" in data:
            # 兼容简写格式: tools: ["read", "write"] 或 {"read": true}
            perm_data = {**perm_data, "tools": data["tools"]}

        return cls(
            name=name,
            description=data.get("description", ""),
            prompt=data.get("prompt", ""),
            steps=data.get("steps", 50),
            permission=AgentPermission.from_dict(perm_data),
            options=data.get("options", {}),
        )


class AgentRegistry:
    """Agent 注册表，管理所有可用角色。"""

    def __init__(self, agents: Dict[str, AgentConfig]):
        self._agents = agents

    def get(self, name: str) -> Optional[AgentConfig]:
        return self._agents.get(name)

    def list_names(self) -> List[str]:
        return list(self._agents.keys())

    def default(self) -> AgentConfig:
        """默认使用 build agent，如未配置则创建一个默认的。"""
        if "build" in self._agents:
            return self._agents["build"]
        if self._agents:
            return next(iter(self._agents.values()))
        return AgentConfig(name="build", description="Default build agent")


_DEFAULT_AGENTS = {
    "build": AgentConfig(
        name="build",
        description="执行 agent，可调用所有工具完成用户任务",
        steps=50,
        permission=AgentPermission(default="allow"),
    ),
    "plan": AgentConfig(
        name="plan",
        description="规划 agent，只读不编辑，用于制定执行计划",
        steps=20,
        prompt="你处于 **Plan 模式**。你的任务是制定详细的执行计划，但不要实际执行文件编辑或破坏性操作。\n\n"
               "你可以：\n"
               "- 读取文件、搜索代码、查询知识库\n"
               "- 分析需求、拆解任务、制定步骤\n"
               "- 使用 web_search/web_fetch 获取信息\n"
               "- 使用 plan_exit 工具完成规划并切换到执行模式\n\n"
               "你不可以：\n"
               "- 编辑、写入、删除任何文件\n"
               "- 运行可能修改系统状态的命令（如 git push、数据库写入）\n\n"
               "规划完成后，调用 plan_exit 工具，系统会询问用户是否切换到 Build 模式开始执行。",
        permission=AgentPermission(
            default="deny",
            allow_list={"read", "web_search", "web_fetch", "bash", "plan_exit"},
        ),
    ),
}


def get_agent_registry(cfg: Optional[Dict[str, Any]] = None) -> AgentRegistry:
    """从 settings.json 配置构建 AgentRegistry。"""
    if not cfg:
        return AgentRegistry(dict(_DEFAULT_AGENTS))

    agent_cfg = cfg.get("agent", {})
    # 兼容旧格式：agent 段是扁平配置（maxHistory, contextWindow 等）
    # 新格式：agent 段包含 "roles" 子段
    roles = agent_cfg.get("roles", {}) if isinstance(agent_cfg, dict) else {}

    if not roles:
        # 未配置角色，使用默认值
        return AgentRegistry(dict(_DEFAULT_AGENTS))

    agents: Dict[str, AgentConfig] = {}
    for name, data in roles.items():
        if isinstance(data, dict) and not data.get("disable"):
            agents[name] = AgentConfig.from_dict(name, data)

    # 确保至少有一个 build agent
    if "build" not in agents:
        agents["build"] = _DEFAULT_AGENTS["build"]

    return AgentRegistry(agents)
