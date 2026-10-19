"""G-17：真实工具评测的执行接缝。

真实模式走 ``ToolExecutionService`` + 真实文件工具，仅替换权限根：
评测 workspace 内的读写放行，workspace 外一律拒绝（fail-closed）。
journal_authority 用空实现接收事件，不落任何真实 journal。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from floodmind.agent.runtime.contracts.paths import PathResolveResult


class WorkspacePathService:
    """最小路径服务：只允许评测 workspace 内的读写。"""

    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def resolve_simple(self, raw_path: str, access: str = "read", session_id: str = "") -> PathResolveResult:
        raw = str(raw_path).strip()
        candidate = Path(raw)
        resolved = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        if resolved == self.root or self.root in resolved.parents:
            return PathResolveResult(
                raw_path=raw,
                normalized_path=raw,
                resolved_path=str(resolved),
                source="workspace",
                allowed=True,
            )
        return PathResolveResult(
            raw_path=raw,
            normalized_path=raw,
            resolved_path=str(resolved),
            source="rejected_external",
            allowed=False,
            reason="评测 workspace 之外不可访问",
        )

    def strip_session_prefix(self, path_str: str, session_id: str = "") -> str:
        return path_str


class _NullJournalAuthority:
    """接收事件但不落盘——评测不产生真实 journal。"""

    def emit(self, *args: Any, **kwargs: Any) -> None:
        return None


class RealToolExecutor:
    """真实工具执行器：与 executor 的 tool_executor 接口对齐。

    执行模式（默认）走裸 ``ToolExecutionService``：权限判断由各文件工具自带的
    ``check_permissions_fn``（路径服务）完成，与既有 tool 层用例行为保持一致。

    planning 模式额外接入生产 ``PermissionService``：planning 硬门
    （拒绝 write/delete/move/patch/exec/state_write 类策略）是 PermissionService
    内的独立层级，只有挂了权限服务才会触发；只读工具照常放行。
    """

    def __init__(self):
        from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
        from floodmind.tools.session_context import set_session_context

        self.calls: List[Any] = []
        self._service = ToolExecutionService(set_session_context_fn=set_session_context)

    def _planning_service(self, context: Any):
        """构造带权限服务的执行器实例（planning 硬门 + 路径检查）。"""
        from floodmind.agent.runtime.services.permission_service import PermissionService
        from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
        from floodmind.tools.session_context import set_session_context

        runtime_context = getattr(context, "runtime_context", None)
        path_service = getattr(runtime_context, "path_service", None)
        if path_service is None and getattr(context, "workspace_dir", ""):
            path_service = WorkspacePathService(context.workspace_dir)
        permission_service = PermissionService(ask_service=None, path_service=path_service)
        return ToolExecutionService(
            permission_service=permission_service,
            path_service=path_service,
            set_session_context_fn=set_session_context,
        )

    def execute(
        self,
        call: Any,
        context: Any,
        registry: Any = None,
        journal_authority: Any = None,
        **kwargs: Any,
    ):
        self.calls.append(call)
        service = self._service
        if getattr(context, "mode", "") == "planning":
            service = self._planning_service(context)
        return service.execute(
            call,
            context=context,
            registry=registry,
            journal_authority=journal_authority or _NullJournalAuthority(),
            **kwargs,
        )


def load_real_tools(names: List[str]) -> Dict[str, Any]:
    """把指定名称的 provider 工具转成 ToolSpec。"""
    from floodmind.tools import file_tools

    available = {
        "Glob": file_tools.Glob_tool,
        "Grep": file_tools.Grep_tool,
        "Read": file_tools.Read_tool,
        "Write": file_tools.Write_tool,
        "Edit": file_tools.Edit_tool,
    }
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"未知的真实评测工具: {', '.join(unknown)}")
    return {name: available[name].to_tool_spec() for name in names}
