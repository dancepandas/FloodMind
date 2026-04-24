"""
AgentTool 统一工具运行时

借鉴 Claude Code 的 buildTool 模式，为所有工具提供统一的行为特征接口：
- is_readonly: 工具是否只读（不修改文件系统）
- is_destructive: 工具是否具有破坏性
- is_concurrency_safe: 工具是否可并发执行
- check_permissions: 权限检查（路径白名单、危险命令检测）
- validate_input: 输入校验
- interrupt_behavior: 中断行为 ('cancel' | 'block')

工具分类：
- 只读工具: knowledge_search, search_artifacts, read_artifact, get_skill, search_memory, search_tool_error_memory
- 写入工具: write_text_file, update_project_instructions, add_knowledge, add_memory
- 执行工具: exec_bash, run_script, exec_python_file
- 网络工具: web_search
"""

import json
import logging
import os
import re
import threading
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

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


class InterruptBehavior(str, Enum):
    CANCEL = "cancel"
    BLOCK = "block"


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

_WRITE_ALLOWED_TOPLEVEL_FILES = {
    "AGENTS.md",
}


def _is_write_allowed(resolved: Path) -> bool:
    try:
        resolved = resolved.resolve()
    except Exception:
        return False

    def _is_relative_to(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base.resolve())
            return True
        except ValueError:
            return False

    for prefix in _WRITE_ALLOWED_PREFIXES:
        if _is_relative_to(resolved, prefix):
            return True
    project_root = _PROJECT_ROOT.resolve()
    if _is_relative_to(resolved, project_root):
        rel = str(resolved.relative_to(project_root))
        if not rel:
            return False
        if rel in _WRITE_ALLOWED_TOPLEVEL_FILES:
            return True
        top_dir = rel.split(os.sep)[0].split("/")[0]
        if top_dir in ("data", "scripts"):
            return True
        return False
    return False


def _get_session_output_dir() -> Optional[str]:
    try:
        from tools.base_tools import get_current_session_output_dir
        return get_current_session_output_dir()
    except Exception:
        return None


def _resolve_path(path_str: str) -> Path:
    try:
        from tools.base_tools import _strip_session_prefix
        path_str = _strip_session_prefix(path_str)
    except Exception:
        pass
    p = Path(path_str)
    if p.is_absolute():
        return p.resolve()
    output_dir = _get_session_output_dir()
    if output_dir:
        return (Path(output_dir) / p).resolve()
    return (_PROJECT_ROOT / p).resolve()


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
        if not _is_write_allowed(resolved):
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                reason=f"写入路径 {resolved} 不在允许目录内",
            )

    return PermissionResult(behavior=PermissionBehavior.ALLOW)


class AgentTool(BaseTool):
    is_readonly: bool = True
    is_destructive: bool = False
    is_concurrency_safe: bool = True
    interrupt_behavior: str = InterruptBehavior.CANCEL
    func: Optional[Callable] = None
    check_permissions_fn: Optional[Callable] = None
    validate_input_fn: Optional[Callable] = None

    class Config:
        arbitrary_types_allowed = True

    def check_permissions(self, tool_input: Dict[str, Any]) -> PermissionResult:
        if self.check_permissions_fn:
            return self.check_permissions_fn(tool_input)
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    def validate_input(self, tool_input: Dict[str, Any]) -> ValidationResult:
        if self.validate_input_fn:
            return self.validate_input_fn(tool_input)
        return ValidationResult(valid=True)

    def _run(self, *args, **kwargs):
        perm_result = self._check_execution_permissions(kwargs)
        if perm_result is not None:
            return perm_result

        validation = self.validate_input(kwargs)
        if not validation.valid:
            return f"[输入校验失败] {validation.reason}"

        if self.func is not None:
            return self.func(**kwargs)
        raise NotImplementedError(f"Tool {self.name} has no func implementation")

    def _check_execution_permissions(self, tool_input: Dict[str, Any]) -> Optional[str]:
        if _permission_manager is not None:
            result = _permission_manager.check(self, tool_input)
        else:
            result = self.check_permissions(tool_input)
        if result.behavior == PermissionBehavior.DENY:
            return f"[权限拒绝] {result.reason}"
        if result.behavior == PermissionBehavior.ASK:
            logger.warning(f"工具 {self.name} 需要 ASK 确认: {result.reason}，默认拒绝")
            return f"[权限拒绝] 需要用户确认: {result.reason}"
        return None


TOOL_DEFAULTS = {
    "is_readonly": True,
    "is_destructive": False,
    "is_concurrency_safe": True,
    "interrupt_behavior": InterruptBehavior.CANCEL,
}


def build_agent_tool(
    name: str,
    description: str,
    args_schema: type[BaseModel],
    func: Callable,
    *,
    is_readonly: bool = TOOL_DEFAULTS["is_readonly"],
    is_destructive: bool = TOOL_DEFAULTS["is_destructive"],
    is_concurrency_safe: bool = TOOL_DEFAULTS["is_concurrency_safe"],
    interrupt_behavior: str = TOOL_DEFAULTS["interrupt_behavior"],
    check_permissions_fn: Optional[Callable] = None,
    validate_input_fn: Optional[Callable] = None,
) -> AgentTool:
    return AgentTool(
        name=name,
        description=description,
        args_schema=args_schema,
        func=func,
        is_readonly=is_readonly,
        is_destructive=is_destructive,
        is_concurrency_safe=is_concurrency_safe,
        interrupt_behavior=interrupt_behavior,
        check_permissions_fn=check_permissions_fn,
        validate_input_fn=validate_input_fn,
    )


class ToolRegistry:
    _tools: Dict[str, AgentTool] = {}
    _lock = threading.Lock()

    @classmethod
    def register(cls, tool: AgentTool) -> None:
        with cls._lock:
            cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> Optional[AgentTool]:
        with cls._lock:
            return cls._tools.get(name)

    @classmethod
    def all(cls) -> List[AgentTool]:
        with cls._lock:
            return list(cls._tools.values())

    @classmethod
    def readonly_tools(cls) -> List[AgentTool]:
        with cls._lock:
            return [t for t in cls._tools.values() if t.is_readonly]

    @classmethod
    def destructive_tools(cls) -> List[AgentTool]:
        with cls._lock:
            return [t for t in cls._tools.values() if t.is_destructive]

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._tools = {}

    @classmethod
    def metadata_table(cls) -> List[Dict[str, Any]]:
        with cls._lock:
            return [
                {
                    "name": t.name,
                    "readonly": t.is_readonly,
                    "destructive": t.is_destructive,
                    "concurrency_safe": t.is_concurrency_safe,
                    "interrupt": t.interrupt_behavior,
                    "category": _tool_category(t),
                }
                for t in cls._tools.values()
            ]


def _tool_category(tool: AgentTool) -> str:
    if tool.is_destructive:
        return "execution"
    if tool.is_readonly:
        if "search" in tool.name or "read" in tool.name or "get" in tool.name:
            return "retrieval"
        return "read"
    return "write"


_GLOBAL_AGENTS_DIR = Path.home() / ".floodagent"
_GLOBAL_AGENTS_MD = _GLOBAL_AGENTS_DIR / "AGENTS.md"
_PROJECT_AGENTS_MD = _PROJECT_ROOT / "AGENTS.md"


def get_agents_md_path(scope: str) -> Path:
    if scope == "global":
        return _GLOBAL_AGENTS_MD
    return _PROJECT_AGENTS_MD


class PermissionRule(BaseModel):
    name: str = ""
    tool_name: Optional[str] = None
    pattern: Optional[str] = None
    behavior: PermissionBehavior = PermissionBehavior.DENY
    reason: str = ""

    def matches(self, tool_name: str, tool_input: Dict[str, Any]) -> bool:
        if self.tool_name and self.tool_name != tool_name:
            return False
        if self.pattern:
            import re as _re
            try:
                text = json.dumps(tool_input, ensure_ascii=False) if isinstance(tool_input, dict) else str(tool_input)
            except (TypeError, ValueError):
                text = str(tool_input)
            if not _re.search(self.pattern, text):
                return False
        return True


class PermissionManager:
    def __init__(self):
        self._deny_rules: List[PermissionRule] = []
        self._allow_rules: List[PermissionRule] = []
        self._on_ask_callback: Optional[Callable] = None

    def add_deny_rule(self, rule: PermissionRule) -> None:
        self._deny_rules.append(rule)

    def add_allow_rule(self, rule: PermissionRule) -> None:
        self._allow_rules.append(rule)

    def set_on_ask_callback(self, callback: Callable) -> None:
        self._on_ask_callback = callback

    def check(self, tool: AgentTool, tool_input: Dict[str, Any]) -> PermissionResult:
        result = tool.check_permissions(tool_input)
        if result.behavior == PermissionBehavior.DENY:
            return result

        for rule in self._deny_rules:
            if rule.matches(tool.name, tool_input):
                return PermissionResult(
                    behavior=rule.behavior,
                    reason=rule.reason or f"全局拒绝规则 '{rule.name}' 命中",
                )

        for rule in self._allow_rules:
            if rule.matches(tool.name, tool_input):
                return PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    reason=rule.reason or f"全局允许规则 '{rule.name}' 命中",
                )

        if result.behavior == PermissionBehavior.ASK:
            if self._on_ask_callback:
                try:
                    user_decision = self._on_ask_callback(tool.name, tool_input, result.reason)
                    if user_decision:
                        return PermissionResult(behavior=PermissionBehavior.ALLOW, reason="用户确认允许")
                    return PermissionResult(behavior=PermissionBehavior.DENY, reason="用户拒绝")
                except Exception as e:
                    logger.warning(f"权限确认回调异常: {e}")
            return result

        return result

    @classmethod
    def create_default(cls) -> "PermissionManager":
        mgr = cls()
        mgr.add_deny_rule(PermissionRule(
            name="deny_system_path_write",
            pattern=r"(/etc/|C:\\\\Windows\\\\|C:\\\\Program Files)",
            behavior=PermissionBehavior.DENY,
            reason="禁止写入系统目录",
        ))
        mgr.add_deny_rule(PermissionRule(
            name="deny_destructive_command",
            tool_name="exec_bash",
            pattern=r"(rm\s+-rf|rm -rf|del\s+/[sS]|del /s|format\s+[A-Za-z]:|rmdir\s+/[sS]|rmdir /s)",
            behavior=PermissionBehavior.DENY,
            reason="检测到破坏性命令",
        ))
        return mgr


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


_permission_manager: Optional["PermissionManager"] = None


def set_permission_manager(mgr: "PermissionManager") -> None:
    global _permission_manager
    _permission_manager = mgr
    logger.info("PermissionManager 已接入执行路径")


def get_permission_manager() -> Optional["PermissionManager"]:
    return _permission_manager
