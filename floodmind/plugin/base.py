"""
FloodMind Plugin System

借鉴 OpenCode PluginV2 架构，提供 Python 原生插件扩展机制。
Plugin 是 MCP 的互补方案：更深入的系统集成、更高的执行性能。

用法:
    1. 继承 FloodmindPlugin 实现自定义插件
    2. 放入 ~/.floodmind/plugins/ 目录
    3. FloodMind 启动时自动发现并加载
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from floodmind.tools.agent_tool import AgentTool


class FloodmindPlugin(ABC):
    """插件基类。

    每个插件可以:
    - 注册工具 (get_tools)
    - 注册事件 hook (get_hooks)
    - 在 Agent 初始化时执行自定义逻辑 (on_agent_init)
    """

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return self.__doc__ or ""

    def on_load(self) -> None:
        """插件被加载时调用（初始化资源）。"""
        pass

    def on_agent_init(self, agent: Any) -> None:
        """Agent 初始化完成后调用，可在此修改 agent 配置。"""
        pass

    def get_tools(self) -> List[AgentTool]:
        """返回此插件提供的工具列表（注册到 orchestrator registry）。"""
        return []

    def get_hooks(self) -> Dict[str, Callable[[dict], None]]:
        """返回事件 hook 映射: {event_type: handler}。

        handler 接收 event dict 作为参数。
        可用 event_type 见 floodmind.agent.native.event_bus.EventBus 文档。
        """
        return {}

    def on_unload(self) -> None:
        """插件被卸载时调用（释放资源）。"""
        pass

    def __repr__(self) -> str:
        return f"<Plugin {self.name} v{self.version}>"
