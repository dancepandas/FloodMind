"""
Runtime Contracts — 工具协议模型

工具执行管线的数据结构集中定义。
ToolExecutionService 只依赖此模块 + permissions + paths。
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Type

from agent.runtime.contracts.permissions import (
    InterruptBehavior,
    ToolPermissionPolicy,
    ValidationResult,
)
from agent.runtime.contracts.paths import PathResolveResult


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    content: str
    status: Literal["completed", "error"]
    artifacts: List[str] = field(default_factory=list)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]
    is_readonly: bool = True
    is_destructive: bool = False
    is_concurrency_safe: bool = True
    interrupt_behavior: str = "cancel"
    permission_policy: Optional[ToolPermissionPolicy] = None
    check_permissions_fn: Optional[Callable[[dict], Any]] = None
    validate_input_fn: Optional[Callable[[dict], Any]] = None
    args_schema: Optional[Type[Any]] = None

    def check_permissions(self, tool_input: dict) -> Any:
        if self.check_permissions_fn is not None:
            return self.check_permissions_fn(tool_input)
        if self.permission_policy is not None:
            from agent.runtime.services.permission_service import get_permission_service
            svc = get_permission_service()
            if svc is not None:
                return svc.check_tool_policy(self.permission_policy, tool_input)
        from agent.runtime.contracts.permissions import PermissionDecision, PermissionBehavior
        return PermissionDecision(behavior=PermissionBehavior.DENY, reason=f"工具 {self.name} 未声明权限策略，默认拒绝")

    def validate_input(self, tool_input: dict) -> Any:
        if self.validate_input_fn is not None:
            return self.validate_input_fn(tool_input)
        return ValidationResult(valid=True)

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolExecutionContext:
    session_id: str = ""
    output_dir: str = ""
    call_id: str = ""
    resolved_paths: Dict[str, PathResolveResult] = field(default_factory=dict)
