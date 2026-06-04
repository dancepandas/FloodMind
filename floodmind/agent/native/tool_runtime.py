"""
Native Agent Runtime - ToolRuntime

自研工具适配层，将 AgentTool 适配为 Native ToolSpec。
"""

import logging
from typing import Any, Optional

from floodmind.agent.runtime.contracts.tools import ToolSpec

logger = logging.getLogger(__name__)


def native_from_agent_tool(tool: Any) -> ToolSpec:
    """将现有 AgentTool 适配为 Native ToolSpec。"""
    parameters = {}
    if hasattr(tool, "args_schema") and tool.args_schema is not None:
        try:
            parameters = tool.args_schema.model_json_schema()
        except Exception:
            parameters = {"type": "object", "properties": {}}
    else:
        parameters = {"type": "object", "properties": {}}

    func = getattr(tool, "func", None) or getattr(tool, "_run", None)
    if func is None:
        def _no_impl(**kwargs):
            return f"工具 {tool.name} 无实现"
        func = _no_impl

    permission_policy = getattr(tool, "permission_policy", None)
    args_schema = getattr(tool, "args_schema", None)

    return ToolSpec(
        name=tool.name,
        description=tool.description or "",
        parameters=parameters,
        func=func,
        is_readonly=getattr(tool, "is_readonly", True),
        is_destructive=getattr(tool, "is_destructive", False),
        is_concurrency_safe=getattr(tool, "is_concurrency_safe", True),
        interrupt_behavior=getattr(tool, "interrupt_behavior", "cancel"),
        permission_policy=permission_policy,
        check_permissions_fn=getattr(tool, "check_permissions_fn", None),
        validate_input_fn=getattr(tool, "validate_input_fn", None),
        args_schema=args_schema,
    )


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