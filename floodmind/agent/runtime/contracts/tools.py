"""
Runtime Contracts — 工具协议模型

工具执行管线的数据结构集中定义。
ToolExecutionService 只依赖此模块 + permissions + paths。
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

from floodmind.agent.runtime.contracts.permissions import (
    InterruptBehavior,
    ToolPermissionPolicy,
    ValidationResult,
)
from floodmind.agent.runtime.contracts.paths import PathResolveResult


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict

    # 允许运行时附加原始参数字符串（用于调试/错误提示）
    model_config = ConfigDict(extra="allow")


class ToolResult(BaseModel):
    tool_call_id: str
    name: str
    content: str
    status: Literal["completed", "error", "awaiting_permission"]
    artifacts: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]
    is_readonly: bool = True
    is_destructive: bool = False
    is_concurrency_safe: bool = True
    permission_policy: Optional[ToolPermissionPolicy] = None
    check_permissions_fn: Optional[Callable[[dict], Any]] = None
    validate_input_fn: Optional[Callable[[dict], Any]] = None
    args_schema: Optional[Type[Any]] = None

    def check_permissions(self, tool_input: dict) -> Any:
        if self.check_permissions_fn is not None:
            return self.check_permissions_fn(tool_input)
        if self.permission_policy is not None:
            from floodmind.agent.runtime.services.permission_service import get_permission_service
            svc = get_permission_service()
            if svc is not None:
                return svc.check_tool_policy(self.permission_policy, tool_input)
        from floodmind.agent.runtime.contracts.permissions import PermissionDecision, PermissionBehavior
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

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
