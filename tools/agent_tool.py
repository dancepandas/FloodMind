"""
AgentTool 统一工具基类与权限系统

借鉴 Claude Code 的 buildTool 模式，为所有工具提供统一的行为特征接口：
- is_readonly: 工具是否只读（不修改文件系统）
- is_destructive: 工具是否具有破坏性
- is_concurrency_safe: 工具是否可并发执行
- check_permissions: 权限检查（路径白名单、危险命令检测）
"""

import logging
import re
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class PermissionBehavior(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionResult(BaseModel):
    behavior: PermissionBehavior = PermissionBehavior.ALLOW
    reason: str = ""


class ValidationResult(BaseModel):
    valid: bool = True
    reason: str = ""


_DANGEROUS_COMMAND_PATTERNS = [
    re.compile(r'\brm\s+-rf\b', re.IGNORECASE),
    re.compile(r'\bdel\s+/[sS]\b', re.IGNORECASE),
    re.compile(r'\bformat\s+[A-Za-z]:', re.IGNORECASE),
    re.compile(r'\brmdir\s+/[sS]\b', re.IGNORECASE),
    re.compile(r'\bshred\b', re.IGNORECASE),
    re.compile(r'\bdd\s+if=', re.IGNORECASE),
    re.compile(r'\bmkfs\b', re.IGNORECASE),
    re.compile(r'>\s*/dev/sd', re.IGNORECASE),
    re.compile(r'\bchmod\s+-R\s+777\b', re.IGNORECASE),
    re.compile(r'\bchown\s+-R\b', re.IGNORECASE),
    re.compile(r'\bgit\s+push\s+--force\b', re.IGNORECASE),
    re.compile(r'\bgit\s+reset\s+--hard\b', re.IGNORECASE),
    re.compile(r'\bdocker\s+system\s+prune', re.IGNORECASE),
    re.compile(r'\bdocker\s+rm\s+-f\b', re.IGNORECASE),
]

_FORBIDDEN_PATH_PATTERNS = [
    re.compile(r'^/etc/', re.IGNORECASE),
    re.compile(r'^C:\\Windows\\', re.IGNORECASE),
    re.compile(r'^C:\\Program Files\\', re.IGNORECASE),
    re.compile(r'^C:\\Program Files \\(x86\\)\\', re.IGNORECASE),
    re.compile(r'^/usr/sbin/', re.IGNORECASE),
    re.compile(r'^/sbin/', re.IGNORECASE),
]

_WRITE_ALLOWED_PREFIXES = [
    _PROJECT_ROOT / "data",
    _PROJECT_ROOT / "scripts",
]


def _resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = (_PROJECT_ROOT / p).resolve()
    return p.resolve()


def check_dangerous_command(command: str) -> PermissionResult:
    for pattern in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                reason=f"检测到危险命令模式: {pattern.pattern}",
            )
    return PermissionResult(behavior=PermissionBehavior.ALLOW)


def check_path_permission(path_str: str, *, require_write: bool = False) -> PermissionResult:
    try:
        resolved = _resolve_path(path_str)
    except Exception:
        return PermissionResult(behavior=PermissionBehavior.DENY, reason=f"无效路径: {path_str}")

    for pattern in _FORBIDDEN_PATH_PATTERNS:
        if pattern.match(str(resolved)):
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                reason=f"禁止访问系统目录: {pattern.pattern}",
            )

    if require_write:
        allowed = any(
            str(resolved).startswith(str(prefix.resolve()))
            for prefix in _WRITE_ALLOWED_PREFIXES
        )
        if not allowed:
            allowed_dirs = ", ".join(str(p) for p in _WRITE_ALLOWED_PREFIXES)
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                reason=f"写入路径 {resolved} 不在允许目录内（允许: {allowed_dirs}）",
            )

    return PermissionResult(behavior=PermissionBehavior.ALLOW)


class AgentTool(BaseTool):
    is_readonly: bool = True
    is_destructive: bool = False
    is_concurrency_safe: bool = True

    def check_permissions(self, tool_input: Dict[str, Any]) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    def validate_input(self, tool_input: Dict[str, Any]) -> ValidationResult:
        return ValidationResult(valid=True)


def build_agent_tool(
    name: str,
    description: str,
    args_schema: type[BaseModel],
    func,
    *,
    is_readonly: bool = True,
    is_destructive: bool = False,
    is_concurrency_safe: bool = True,
    check_permissions_fn=None,
    validate_input_fn=None,
) -> AgentTool:
    return AgentTool(
        name=name,
        description=description,
        args_schema=args_schema,
        func=func,
        is_readonly=is_readonly,
        is_destructive=is_destructive,
        is_concurrency_safe=is_concurrency_safe,
        check_permissions=check_permissions_fn,
        validate_input=validate_input_fn,
    )


_GLOBAL_AGENTS_DIR = Path.home() / ".floodagent"
_GLOBAL_AGENTS_MD = _GLOBAL_AGENTS_DIR / "AGENTS.md"
_PROJECT_AGENTS_MD = _PROJECT_ROOT / "AGENTS.md"


def get_agents_md_path(scope: str) -> Path:
    if scope == "global":
        return _GLOBAL_AGENTS_MD
    return _PROJECT_AGENTS_MD


class UpdateProjectInstructionsInput(BaseModel):
    action: str = Field(
        default="append",
        description="操作类型: append=追加内容, replace_section=替换章节, remove_section=删除章节",
    )
    content: str = Field(
        default="",
        description="要追加或替换的内容（纯文本，不需要加 ## 标题，工具会自动处理）",
    )
    section_title: str = Field(
        default="",
        description="章节标题（replace_section/remove_section 时必填，如 '用户偏好'）",
    )
    scope: str = Field(
        default="project",
        description="作用域: project=本项目AGENTS.md, global=全局~/.floodagent/AGENTS.md",
    )
