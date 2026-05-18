"""
Runtime Contracts — 权限协议模型

所有权限相关的数据结构集中定义，不依赖任何业务实现。
PermissionService / AskService / ToolExecutionService 只依赖此模块。
"""

from enum import Enum
from typing import Literal, Optional, Any

from pydantic import BaseModel


class PermissionBehavior(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionDecision(BaseModel):
    behavior: PermissionBehavior = PermissionBehavior.ALLOW
    reason: str = ""


class ToolFeedback(BaseModel):
    error_type: str = ""
    error_code: str = ""
    what_went_wrong: str = ""
    correct_usage: str = ""
    retryable: bool = False
    do_not_retry_same_call: bool = False

    def to_output_string(self) -> str:
        parts = [f"[{self.error_type}] {self.what_went_wrong}"]
        if self.error_code:
            parts[0] = f"[{self.error_type}:{self.error_code}] {self.what_went_wrong}"
        if self.correct_usage:
            parts.append(f"正确做法: {self.correct_usage}")
        if self.do_not_retry_same_call:
            parts.append("不要使用相同参数原样重试，请先修正参数或改用其他方式。")
        elif not self.retryable:
            parts.append("此调用不可重试。")
        return "\n".join(parts)


class PermissionRequest(BaseModel):
    session_id: str = ""
    call_id: str = ""
    tool_name: str = ""
    tool_input: dict = {}
    permission_policy: Optional["ToolPermissionPolicy"] = None

    class Config:
        arbitrary_types_allowed = True

    _check_permissions_fn: Any = None


class PermissionAskRequest(BaseModel):
    session_id: str = ""
    call_id: str = ""
    tool_name: str = ""
    reason: str = ""
    tool_input: dict = {}


class PermissionAskResponse(BaseModel):
    session_id: str = ""
    ask_id: str = ""
    approved: bool = False


class PermissionAskSnapshot(BaseModel):
    ask_id: str = ""
    session_id: str = ""
    call_id: str = ""
    tool_name: str = ""
    reason: str = ""
    created_at: float = 0.0


class PermissionRule(BaseModel):
    name: str = ""
    tool_name: Optional[str] = None
    pattern: Optional[str] = None
    behavior: PermissionBehavior = PermissionBehavior.DENY
    reason: str = ""

    def matches(self, tool_name: str, tool_input: dict) -> bool:
        if self.tool_name and self.tool_name != tool_name:
            return False
        if self.pattern:
            import json
            import re as _re
            try:
                text = json.dumps(tool_input, ensure_ascii=False) if isinstance(tool_input, dict) else str(tool_input)
            except (TypeError, ValueError):
                text = str(tool_input)
            if not _re.search(self.pattern, text):
                return False
        return True


class ToolPermissionPolicy(BaseModel):
    policy_type: Literal["readonly", "write", "exec", "ask", "read_path", "skill_script", "internal", "state_write", "network"] = "readonly"
    reason: str = ""
    path_field: str = "file_path"
    command_field: str = "command"
    path_fields: list[str] = []


class ValidationResult(BaseModel):
    valid: bool = True
    reason: str = ""


class InterruptBehavior(str, Enum):
    CANCEL = "cancel"
    BLOCK = "block"
