"""
Agent 工具定义

提供标准化的工具注册和调用机制，不依赖 LangChain。
每个工具都是独立的、可序列化的、可快速迁移的组件。
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

from floodmind.agent.runtime.contracts.permissions import (
    InterruptBehavior,
    PermissionBehavior,
    PermissionDecision,
    ToolPermissionPolicy,
    ValidationResult,
)
from floodmind.agent.runtime.contracts.paths import PathResolveResult
from floodmind.agent.runtime.contracts.tools import ToolSpec
from floodmind.agent.runtime.services.path_service import get_path_service
from floodmind.agent.runtime.services.permission_service import (
    PermissionService,
    get_permission_service,
    set_permission_service,
)
from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT as _PROJECT_ROOT

logger = logging.getLogger(__name__)


def _sanitize_parameters(schema: Any) -> Dict[str, Any]:
    """Strip pydantic schema noise before sending ``parameters`` to the model.

    ``model_json_schema()`` leaks class names via ``title`` and wraps Optional
    fields in ``anyOf: [{type: T}, {type: null}]`` — both pure noise to the model
    (and the class-name leak is the long-standing hygiene issue). We drop titles
    and collapse Optional ``anyOf`` to its non-null type. ``$defs``/``$ref``
    (nested-model references) are left intact: dereferencing risks correctness,
    and tool arg schemas are virtually always flat.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    def _clean(node: Any) -> Any:
        if isinstance(node, dict):
            any_of = node.get("anyOf")
            if isinstance(any_of, list) and any(
                isinstance(b, dict) and b.get("type") == "null" for b in any_of
            ):
                non_null = [b for b in any_of if not (isinstance(b, dict) and b.get("type") == "null")]
                if len(non_null) == 1:
                    merged = dict(node)
                    merged.pop("anyOf")
                    merged.update(non_null[0])
                    node = merged
                elif len(non_null) >= 2:
                    node = {**node, "anyOf": non_null}
            node.pop("title", None)
            return {k: _clean(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_clean(v) for v in node]
        return node

    return _clean(schema)


class AgentTool(BaseModel):
    """Agent 工具基类

    替代 langchain_core.tools.BaseTool，提供独立的工具定义。
    每个工具都是可序列化的、可独立运行的组件。
    """

    name: str = Field(description="工具名称")
    description: str = Field(description="工具描述")
    func: Optional[Callable] = Field(default=None, description="工具执行函数", exclude=True)
    args_schema: Optional[Type[BaseModel]] = Field(default=None, description="参数 schema")
    parameters: Optional[Dict[str, Any]] = Field(
        default=None,
        description="原始 JSON Schema（与 args_schema 互斥的 raw 编写模式；供无 pydantic 模型的系统工具等使用）",
    )

    # 行为标记（is_readonly/is_destructive/is_concurrency_safe 经
    # tool_runtime.native_from_agent_tool 透传到运行时 ToolSpec，供 PermissionService 策略判定）
    is_readonly: bool = Field(default=True, description="是否只读")
    is_destructive: bool = Field(default=False, description="是否破坏性操作")
    is_concurrency_safe: bool = Field(default=True, description="是否并发安全")

    # 权限
    check_permissions_fn: Optional[Callable] = Field(default=None, description="权限检查函数", exclude=True)
    validate_input_fn: Optional[Callable] = Field(default=None, description="输入校验函数", exclude=True)
    permission_policy: Optional[ToolPermissionPolicy] = Field(default=None, description="权限策略")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def to_tool_spec(self) -> ToolSpec:
        """投影到运行时 ``ToolSpec`` —— AgentTool→ToolSpec 的唯一权威转换点。

        schema 生成优先级：raw ``parameters`` > ``args_schema``（pydantic，剥冗余）
        > 空 object schema。运行时桥（``native_from_agent_tool``）统一委托到此。
        """
        if self.parameters is not None:
            parameters = self.parameters
        elif self.args_schema is not None:
            try:
                parameters = _sanitize_parameters(self.args_schema.model_json_schema())
            except Exception:
                parameters = {"type": "object", "properties": {}}
        else:
            parameters = {"type": "object", "properties": {}}

        func = self.func
        if func is None:
            def _no_impl(**kwargs):
                return f"工具 {self.name} 无实现"
            func = _no_impl

        return ToolSpec(
            name=self.name,
            description=self.description or "",
            parameters=parameters,
            func=func,
            is_readonly=self.is_readonly,
            is_destructive=self.is_destructive,
            is_concurrency_safe=self.is_concurrency_safe,
            permission_policy=self.permission_policy,
            check_permissions_fn=self.check_permissions_fn,
            validate_input_fn=self.validate_input_fn,
            args_schema=self.args_schema,
        )


def build_agent_tool(
    func: Callable,
    name: Optional[str] = None,
    description: Optional[str] = None,
    args_schema: Optional[Type[BaseModel]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    is_readonly: bool = True,
    is_destructive: bool = False,
    is_concurrency_safe: bool = True,
    check_permissions_fn: Optional[Callable] = None,
    validate_input_fn: Optional[Callable] = None,
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
        parameters=parameters,
        is_readonly=is_readonly,
        is_destructive=is_destructive,
        is_concurrency_safe=is_concurrency_safe,
        check_permissions_fn=check_permissions_fn,
        validate_input_fn=validate_input_fn,
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
    """统一路径解析入口。

    自动从 SESSION_CONTEXT 透传当前 session_id 给 PathService，
    使子代理(sub-*)的 workspace 写路径强制得以生效。
    """
    try:
        from floodmind.tools.session_context import get_current_session_id
        session_id = get_current_session_id() or ""
    except Exception:
        session_id = ""
    return get_path_service().resolve_simple(path_str, access=access, session_id=session_id)


# ---------------------------------------------------------------------------
# AGENTS.md 路径
# ---------------------------------------------------------------------------

def get_agents_md_path(scope: str = "project") -> Path:
    """获取 AGENTS.md 文件路径"""
    if scope == "global":
        home = Path.home()
        agents_dir = home / ".config" / "floodmind"
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
    """工具注册中心。

    集中管理所有工具。采用全局单例模式：模块底部创建唯一的
    ``global_tool_registry`` 实例，外部统一通过 classmethod
    （``ToolRegistry.register`` / ``ToolRegistry.get`` 等）访问，避免再实例化。
    """

    def __init__(self):
        self._tools: Dict[str, AgentTool] = {}
        self._aliases: Dict[str, str] = {}

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


# 全局工具注册中心
global_tool_registry = ToolRegistry()