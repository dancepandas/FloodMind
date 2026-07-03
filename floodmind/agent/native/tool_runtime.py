"""
Native Agent Runtime - ToolRuntime

自研工具适配层，将 AgentTool 适配为 Native ToolSpec。
"""

import logging
from typing import Any, Optional

from floodmind.agent.runtime.contracts.tools import ToolSpec
from floodmind.tools.agent_tool import AgentTool

logger = logging.getLogger(__name__)


def native_from_agent_tool(tool: Any) -> ToolSpec:
    """将工具定义归一化为运行时 ``ToolSpec``（薄包装）。

    - 已是 ``ToolSpec``：原样返回。
    - ``AgentTool``：委托其权威的 ``AgentTool.to_tool_spec()``。
    - 其它 tool-like 对象：包成 ``AgentTool`` 再投影，确保转换逻辑只在一处
     （``AgentTool.to_tool_spec``），无散落 getattr。
    """
    if isinstance(tool, ToolSpec):
        return tool
    if isinstance(tool, AgentTool):
        return tool.to_tool_spec()
    return AgentTool(
        name=getattr(tool, "name", "unknown"),
        description=getattr(tool, "description", "") or "",
        func=getattr(tool, "func", None) or getattr(tool, "_run", None),
        args_schema=getattr(tool, "args_schema", None),
        parameters=getattr(tool, "parameters", None),
        is_readonly=getattr(tool, "is_readonly", True),
        is_destructive=getattr(tool, "is_destructive", False),
        is_concurrency_safe=getattr(tool, "is_concurrency_safe", True),
        permission_policy=getattr(tool, "permission_policy", None),
        check_permissions_fn=getattr(tool, "check_permissions_fn", None),
    ).to_tool_spec()


# Keep alias for backward compatibility
tool_spec_from_agent_tool = native_from_agent_tool


def register_agent_tools(tools: list, registry: Optional[Any] = None) -> None:
    """批量将现有 AgentTool 注册到指定 registry 实例。"""
    if registry is None:
        raise ValueError("register_agent_tools() 必须传入 registry 参数")
    for tool in tools:
        spec = native_from_agent_tool(tool)
        registry.register(spec)
    logger.info("Registered %d tools into registry", len(tools))