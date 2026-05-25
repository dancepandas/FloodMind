"""
Agent 工具定义

提供标准化的工具注册和调用机制，不依赖 LangChain。
每个工具都是独立的、可序列化的、可快速迁移的组件。
"""

import inspect
import json
import logging
import os
import re
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, Union

from pydantic import BaseModel, Field

from agent.runtime.contracts.permissions import (
    InterruptBehavior,
    PermissionBehavior,
    PermissionDecision,
    ToolPermissionPolicy,
    ValidationResult,
)
from agent.runtime.contracts.paths import PathResolveResult
from agent.runtime.services.path_service import get_path_service
from agent.runtime.services.permission_service import (
    PermissionService,
    get_permission_service,
    set_permission_service,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ToolResult:
    """工具执行结果"""

    def __init__(self, output: Any, success: bool = True, error: Optional[str] = None):
        self.output = output
        self.success = success
        self.error = error

    def __str__(self) -> str:
        if self.success:
            return str(self.output)
        return f"Error: {self.error}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "output": str(self.output),
            "success": self.success,
            "error": self.error,
        }


class AgentTool(BaseModel):
    """Agent 工具基类

    替代 langchain_core.tools.BaseTool，提供独立的工具定义。
    每个工具都是可序列化的、可独立运行的组件。
    """

    name: str = Field(description="工具名称")
    description: str = Field(description="工具描述")
    func: Optional[Callable] = Field(default=None, description="工具执行函数", exclude=True)
    args_schema: Optional[Type[BaseModel]] = Field(default=None, description="参数 schema")

    # 行为标记
    is_readonly: bool = Field(default=True, description="是否只读")
    is_destructive: bool = Field(default=False, description="是否破坏性操作")
    requires_confirmation: bool = Field(default=False, description="是否需要用户确认")
    is_concurrency_safe: bool = Field(default=True, description="是否并发安全")
    category: str = Field(default="general", description="工具分类")
    tags: List[str] = Field(default_factory=list, description="工具标签")

    # 权限
    check_permissions_fn: Optional[Callable] = Field(default=None, description="权限检查函数", exclude=True)
    permission_policy: Optional[ToolPermissionPolicy] = Field(default=None, description="权限策略")

    model_config = {"arbitrary_types_allowed": True}

    def run(self, **kwargs) -> ToolResult:
        """执行工具"""
        if self.func is None:
            return ToolResult(output=None, success=False, error=f"工具 {self.name} 没有执行函数")

        try:
            if self.args_schema:
                validated = self.args_schema(**kwargs)
                result = self.func(**validated.model_dump())
            else:
                result = self.func(**kwargs)

            if isinstance(result, ToolResult):
                return result
            return ToolResult(output=result)

        except Exception as e:
            error_msg = f"工具 {self.name} 执行失败: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            return ToolResult(output=None, success=False, error=str(e))

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 OpenAI function calling schema"""
        schema: Dict[str, Any] = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
            },
        }

        if self.args_schema:
            args_schema = self.args_schema.model_json_schema()
            properties = args_schema.get("properties", {})
            required = args_schema.get("required", [])

            # 清理内部字段
            for prop in properties.values():
                prop.pop("title", None)

            schema["function"]["parameters"] = {
                "type": "object",
                "properties": properties,
                "required": required,
            }
        else:
            schema["function"]["parameters"] = {
                "type": "object",
                "properties": {},
            }

        return schema

    def to_tool_info(self) -> Dict[str, Any]:
        """返回工具信息字典"""
        return {
            "name": self.name,
            "description": self.description,
            "schema": self.get_schema(),
            "is_readonly": self.is_readonly,
            "is_destructive": self.is_destructive,
            "category": self.category,
        }


def build_agent_tool(
    func: Callable,
    name: Optional[str] = None,
    description: Optional[str] = None,
    args_schema: Optional[Type[BaseModel]] = None,
    is_readonly: bool = True,
    is_destructive: bool = False,
    requires_confirmation: bool = False,
    is_concurrency_safe: bool = True,
    category: str = "general",
    tags: Optional[List[str]] = None,
    check_permissions_fn: Optional[Callable] = None,
    permission_policy: Optional[ToolPermissionPolicy] = None,
) -> AgentTool:
    """从函数构建 AgentTool"""
    tool_name = name or func.__name__
    tool_description = description or func.__doc__ or f"执行 {tool_name}"

    return AgentTool(
        name=tool_name,
        description=tool_description,
        func=func,
        args_schema=args_schema,
        is_readonly=is_readonly,
        is_destructive=is_destructive,
        requires_confirmation=requires_confirmation,
        is_concurrency_safe=is_concurrency_safe,
        category=category,
        tags=tags or [],
        check_permissions_fn=check_permissions_fn,
        permission_policy=permission_policy,
    )


# ---------------------------------------------------------------------------
# 权限检查工厂函数
# ---------------------------------------------------------------------------

def make_readonly_permission_fn() -> Callable[[dict], PermissionDecision]:
    """只读工具权限检查 — 默认允许"""
    def _fn(tool_input: dict) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)
    return _fn


def make_write_permission_fn(path_field: str = "file_path") -> Callable[[dict], PermissionDecision]:
    """写入工具权限检查 — 检查路径是否在允许范围内"""
    def _fn(tool_input: dict) -> PermissionDecision:
        raw_path = tool_input.get(path_field, "")
        if not raw_path:
            return PermissionDecision(behavior=PermissionBehavior.ALLOW)
        result = resolve_tool_path(raw_path, access="write")
        if not result.allowed:
            return PermissionDecision(behavior=PermissionBehavior.DENY, reason=result.reason)
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)
    return _fn


def make_exec_permission_fn(
    command_field: str = "command",
    path_fields: Optional[List[str]] = None,
) -> Callable[[dict], PermissionDecision]:
    """执行工具权限检查 — 检查命令和路径"""
    def _fn(tool_input: dict) -> PermissionDecision:
        for pf in (path_fields or []):
            raw_path = tool_input.get(pf, "")
            if raw_path:
                result = resolve_tool_path(raw_path, access="exec")
                if not result.allowed:
                    return PermissionDecision(behavior=PermissionBehavior.DENY, reason=result.reason)
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)
    return _fn


def make_skill_script_permission_fn() -> Callable[[dict], PermissionDecision]:
    """技能脚本权限检查 — 默认允许（脚本路径由 skill 注册表约束）"""
    def _fn(tool_input: dict) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)
    return _fn


def make_ask_permission_fn(reason: str = "") -> Callable[[dict], PermissionDecision]:
    """需要用户确认的权限检查 — 默认询问"""
    def _fn(tool_input: dict) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ASK, reason=reason)
    return _fn


def make_read_path_permission_fn(path_field: str = "file_path") -> Callable[[dict], PermissionDecision]:
    """读取路径权限检查 — 检查路径是否可读"""
    def _fn(tool_input: dict) -> PermissionDecision:
        raw_path = tool_input.get(path_field, "")
        if not raw_path:
            return PermissionDecision(behavior=PermissionBehavior.ALLOW)
        result = resolve_tool_path(raw_path, access="read")
        if not result.allowed:
            return PermissionDecision(behavior=PermissionBehavior.DENY, reason=result.reason)
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)
    return _fn


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------

def _strip_session_prefix(path_str: str) -> str:
    """剥离 data/sessions/<id>/outputs/ 前缀"""
    return get_path_service().strip_session_prefix(path_str)


def resolve_tool_path(
    path_str: str,
    access: str = "read",
) -> PathResolveResult:
    """统一路径解析入口"""
    return get_path_service().resolve_simple(path_str, access=access)


# ---------------------------------------------------------------------------
# AGENTS.md 路径
# ---------------------------------------------------------------------------

def get_agents_md_path(scope: str = "project") -> Path:
    """获取 AGENTS.md 文件路径"""
    if scope == "global":
        home = Path.home()
        agents_dir = home / ".floodmind"
        agents_dir.mkdir(parents=True, exist_ok=True)
        return agents_dir / "AGENTS.md"
    return _PROJECT_ROOT / "AGENTS.md"


# ---------------------------------------------------------------------------
# 兼容别名
# ---------------------------------------------------------------------------

PermissionResult = PermissionDecision

set_permission_manager = set_permission_service


# ---------------------------------------------------------------------------
# 工具默认配置
# ---------------------------------------------------------------------------

TOOL_DEFAULTS = {
    "max_output_length": 8000,
    "timeout_seconds": 120,
    "retry_count": 0,
}


# ---------------------------------------------------------------------------
# 安全检查函数
# ---------------------------------------------------------------------------

_DANGEROUS_COMMANDS = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){ :|:& };:",
    "wget.*|.*sh", "curl.*|.*sh", "> /dev/sda",
]


def check_dangerous_command(command: str) -> ValidationResult:
    """检查命令是否危险"""
    cmd_lower = command.lower().strip()
    for pattern in _DANGEROUS_COMMANDS:
        if pattern in cmd_lower:
            return ValidationResult(
                valid=False,
                reason=f"命令包含危险模式: {pattern}",
            )
    return ValidationResult(valid=True)


def check_path_permission(path_str: str, access: str = "read") -> ValidationResult:
    """检查路径权限"""
    result = resolve_tool_path(path_str, access=access)
    return ValidationResult(
        valid=result.allowed,
        reason=getattr(result, "reason", ""),
    )

class UpdateProjectInstructionsInput(BaseModel):
    """修改项目指令的输入参数"""
    action: str = Field(description="[必填] 操作类型: append, replace_section, remove_section")
    content: str = Field(description="[必填] 要添加或替换的文本内容")
    section_title: str = Field(default="", description="[可选] 章节标题（replace_section/remove_section 必填）")
    scope: str = Field(default="project", description="[可选] 范围: project 或 global，默认 project")


# ---------------------------------------------------------------------------
# 工具注册中心
# ---------------------------------------------------------------------------

class ToolRegistry:
    """工具注册中心

    集中管理所有工具，支持分类查询和批量注册。
    """

    def __init__(self):
        self._tools: Dict[str, AgentTool] = {}
        self._aliases: Dict[str, str] = {}

    def register(self, tool: AgentTool) -> None:
        """注册工具"""
        self._tools[tool.name] = tool
        logger.debug(f"工具注册: {tool.name}")

    def register_alias(self, alias: str, tool_name: str) -> None:
        """注册工具别名"""
        if tool_name not in self._tools:
            logger.warning(f"别名注册失败：工具 {tool_name} 不存在")
            return
        self._aliases[alias] = tool_name
        logger.debug(f"别名注册: {alias} -> {tool_name}")

    def unregister(self, name: str) -> None:
        """注销工具"""
        if name in self._tools:
            del self._tools[name]
        self._aliases = {a: t for a, t in self._aliases.items() if t != name}

    def get(self, name: str) -> Optional[AgentTool]:
        """获取工具（支持别名查找）"""
        tool = self._tools.get(name)
        if tool:
            return tool
        alias_target = self._aliases.get(name)
        if alias_target:
            return self._tools.get(alias_target)
        return None

    def get_all(self) -> Dict[str, AgentTool]:
        """获取所有工具"""
        return dict(self._tools)

    def get_by_category(self, category: str) -> List[AgentTool]:
        """按分类获取工具"""
        return [t for t in self._tools.values() if t.category == category]

    def get_schemas(self) -> List[Dict[str, Any]]:
        """获取所有工具的 OpenAI function calling schemas"""
        return [tool.get_schema() for tool in self._tools.values()]

    def get_readonly_tools(self) -> List[AgentTool]:
        """获取只读工具"""
        return [t for t in self._tools.values() if t.is_readonly]

    def get_destructive_tools(self) -> List[AgentTool]:
        """获取破坏性工具"""
        return [t for t in self._tools.values() if t.is_destructive]

    def run_tool(self, name: str, **kwargs) -> ToolResult:
        """执行工具"""
        tool = self.get(name)
        if tool is None:
            return ToolResult(output=None, success=False, error=f"工具 {name} 不存在")
        return tool.run(**kwargs)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    @classmethod
    def clear(cls) -> None:
        """清空全局注册表"""
        global_tool_registry._tools.clear()

    @classmethod
    def register(cls, tool: AgentTool) -> None:
        """注册工具到全局注册表"""
        global_tool_registry._tools[tool.name] = tool
        logger.debug(f"工具注册: {tool.name}")

    @classmethod
    def register_alias(cls, alias: str, tool_name: str) -> None:
        """注册别名到全局注册表"""
        if tool_name not in global_tool_registry._tools:
            logger.warning(f"别名注册失败：工具 {tool_name} 不存在")
            return
        global_tool_registry._aliases[alias] = tool_name
        logger.debug(f"别名注册: {alias} -> {tool_name}")

    @classmethod
    def get(cls, name: str) -> Optional[AgentTool]:
        """从全局注册表获取工具（支持别名）"""
        tool = global_tool_registry._tools.get(name)
        if tool:
            return tool
        alias_target = global_tool_registry._aliases.get(name)
        if alias_target:
            return global_tool_registry._tools.get(alias_target)
        return None

    @classmethod
    def get_all(cls) -> Dict[str, AgentTool]:
        """获取全局注册表所有工具"""
        return dict(global_tool_registry._tools)

    @classmethod
    def get_schemas(cls) -> List[Dict[str, Any]]:
        """获取全局注册表所有工具的 schemas"""
        return [tool.get_schema() for tool in global_tool_registry._tools.values()]


# 全局工具注册中心
global_tool_registry = ToolRegistry()