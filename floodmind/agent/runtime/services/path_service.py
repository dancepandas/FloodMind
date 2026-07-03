"""
PathService — 统一路径解析服务

所有路径解析逻辑集中在此，权限层和执行层共用同一个解析结果。
解决旧代码中权限检查和实际执行使用不同路径的问题。

设计原则：
- 写入类工具没有 session context 时直接 rejected，不 fallback 到项目根
- data/sessions/<id>/outputs/foo 输入统一归一成 foo
- 权限层和 _impl_* 层传递同一个 PathResolveResult
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

from floodmind.agent.runtime.contracts.paths import PathResolveRequest, PathResolveResult
from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT as _PROJECT_ROOT

logger = logging.getLogger(__name__)

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


_READ_ALLOWED_PREFIXES = [
    _PROJECT_ROOT,
]

_READ_ALLOWED_OUTSIDE_PREFIXES = []
try:
    _home = Path.home()
    _READ_ALLOWED_OUTSIDE_PREFIXES.append(_home / ".config" / "floodmind")
except Exception:
    pass


class PathService:
    def __init__(
        self,
        project_root: Optional[Path] = None,
        workspace: Optional["Workspace"] = None,
    ):
        self._project_root = (project_root or _PROJECT_ROOT).resolve()
        # 静态白名单：从模块常量复制为实例属性，便于运行时追加 workspace 动态根。
        # workspace 默认 None，运行时 fallback get_workspace()，保证测试可显式注入。
        self._workspace = workspace
        self._static_write_allowed_prefixes = list(_WRITE_ALLOWED_PREFIXES)
        self._static_read_allowed_prefixes = list(_READ_ALLOWED_PREFIXES)
        self._static_read_allowed_outside_prefixes = list(_READ_ALLOWED_OUTSIDE_PREFIXES)
        self._static_write_allowed_toplevel_files = set(_WRITE_ALLOWED_TOPLEVEL_FILES)

    # ── workspace 动态根 ──────────────────────────────────────────
    def _effective_workspace(self) -> Optional["Workspace"]:
        if self._workspace is not None:
            return self._workspace
        try:
            from floodmind.agent.runtime.services.workspace_service import get_workspace
            return get_workspace()
        except Exception:
            return None

    def _dynamic_write_roots(self) -> list:
        """当前 workspace 提供的额外写根（user_dir / sandbox_base / writable_roots）。"""
        ws = self._effective_workspace()
        if ws is None:
            return []
        return [ws.user_dir, ws.sandbox_base, *ws.writable_roots]

    def _dynamic_read_roots(self) -> list:
        ws = self._effective_workspace()
        if ws is None:
            return []
        return [ws.user_dir, ws.sandbox_base, *ws.readable_roots]

    def resolve(self, request: PathResolveRequest) -> PathResolveResult:
        raw = str(request.raw_path).strip().strip('"').strip("'")
        normalized = self._strip_session_prefix(raw)
        p = Path(normalized)

        if p.is_absolute():
            resolved = p.resolve()
            allowed, reason = self._check_path_allowed(resolved, request.access, request.session_id)
            return PathResolveResult(
                raw_path=raw,
                normalized_path=normalized,
                resolved_path=str(resolved),
                source="absolute",
                allowed=allowed,
                reason=reason,
            )

        if request.access in ("write", "exec", "cwd"):
            output_dir = self._get_user_dir(request.session_id)
            if output_dir:
                resolved = (Path(output_dir) / p).resolve()
                allowed, reason = self._check_path_allowed(resolved, request.access, request.session_id)
                return PathResolveResult(
                    raw_path=raw,
                    normalized_path=normalized,
                    resolved_path=str(resolved),
                    source="user_dir",
                    allowed=allowed,
                    reason=reason,
                )

            return PathResolveResult(
                raw_path=raw,
                normalized_path=normalized,
                resolved_path=str((self._project_root / p).resolve()),
                source="no_context_rejected",
                allowed=False,
                reason="无会话上下文时相对路径写入被拒绝。正确做法：只写文件名（如 result.py），系统会自动写入当前工作区。不要传 data/sessions/... 等目录前缀。",
            )

        output_dir = self._get_user_dir(request.session_id)
        if output_dir:
            resolved = (Path(output_dir) / p).resolve()
            allowed, reason = self._check_path_allowed(resolved, request.access, request.session_id)
            return PathResolveResult(
                raw_path=raw,
                normalized_path=normalized,
                resolved_path=str(resolved),
                source="user_dir",
                allowed=allowed,
                reason=reason,
            )

        resolved = (self._project_root / p).resolve()
        allowed, reason = self._check_path_allowed(resolved, request.access, request.session_id)
        return PathResolveResult(
            raw_path=raw,
            normalized_path=normalized,
            resolved_path=str(resolved),
            source="project_root_fallback",
            allowed=allowed,
            reason=reason,
        )

    def resolve_simple(self, raw_path: str, access: str = "read", session_id: str = "") -> PathResolveResult:
        return self.resolve(PathResolveRequest(
            raw_path=raw_path,
            access=access,
            session_id=session_id,
        ))

    def is_write_allowed(self, resolved: Path) -> bool:
        try:
            resolved = resolved.resolve()
        except Exception:
            return False

        # 动态根优先（workspace user_dir / sandbox_base / writable_roots）
        for prefix in self._dynamic_write_roots():
            if self._is_relative_to(resolved, prefix):
                return True

        for prefix in self._static_write_allowed_prefixes:
            if self._is_relative_to(resolved, prefix):
                return True

        project_root = self._project_root.resolve()
        if self._is_relative_to(resolved, project_root):
            rel = str(resolved.relative_to(project_root))
            if not rel:
                return False
            if rel in self._static_write_allowed_toplevel_files:
                return True
            top_dir = rel.split(os.sep)[0].split("/")[0]
            if top_dir in ("data", "scripts"):
                return True
            return False
        return False

    def is_forbidden_path(self, resolved: Path) -> bool:
        for pattern in _FORBIDDEN_PATH_PATTERNS:
            if pattern.match(str(resolved)):
                return True
        return False

    def strip_session_prefix(self, path_str: str) -> str:
        return self._strip_session_prefix(path_str)

    def _strip_session_prefix(self, path_str: str) -> str:
        s = path_str.replace("\\", "/")
        m = re.match(r"^data/sessions/[^/]+/outputs/(.+)$", s)
        if m:
            return m.group(1)
        m = re.match(r"^data/sessions/[^/]+/(.+)$", s)
        if m:
            return m.group(1)
        m = re.match(r"^data/sessions/(.+)$", s)
        if m:
            return m.group(1)
        return path_str

    def _check_path_allowed(self, resolved: Path, access: str, session_id: str = "") -> tuple:
        for pattern in _FORBIDDEN_PATH_PATTERNS:
            if pattern.match(str(resolved)):
                return False, f"禁止访问系统目录: {pattern.pattern}"
        if access in ("write", "exec", "cwd") and not self.is_write_allowed(resolved):
            return False, f"写入路径 {resolved} 不在允许目录内"
        if access == "read" and not self.is_read_allowed(resolved):
            return False, f"读取路径 {resolved} 不在允许目录内"
        # 子代理写范围：sandbox workspace / user_dir / delegate_cwd（阶段C放权）
        # delegate_cwd 由 ToolExecutionService 经 SESSION_CONTEXT 注入（主代理委派时指定）。
        if access in ("write", "exec", "cwd") and session_id.startswith("sub-"):
            ws = self._effective_workspace()
            allowed_roots = []
            if ws is not None:
                allowed_roots += [ws.sandbox_base / session_id / "workspace", ws.user_dir]
            else:
                workspace = self._project_root / "data" / "sessions" / session_id / "workspace"
                allowed_roots.append(workspace)
            # 追加 delegate_cwd（子代理被指定的工作目录，桌面版并行写 user_dir 子目录）
            try:
                from floodmind.tools.session_context import SESSION_CONTEXT
                delegate_cwd = SESSION_CONTEXT.get("delegate_cwd", "")
                if delegate_cwd:
                    allowed_roots.append(Path(delegate_cwd))
            except Exception:
                pass
            ok = any(self._is_relative_to(resolved, r) for r in allowed_roots)
            if not ok:
                return False, f"子代理只能写入自己的工作区或当前工作区: {[str(r) for r in allowed_roots]}"
        # 覆盖保护开关（默认关，桌面版可开）
        if access == "write":
            ws = self._effective_workspace()
            if ws is not None and ws.overwrite_protection and resolved.exists():
                return False, f"覆盖保护开启：{resolved} 已存在，拒绝覆盖"
        return True, ""

    def is_read_allowed(self, resolved: Path) -> bool:
        try:
            resolved = resolved.resolve()
        except Exception:
            return False

        for pattern in _FORBIDDEN_PATH_PATTERNS:
            if pattern.match(str(resolved)):
                return False

        # 动态根优先（workspace user_dir / sandbox_base / readable_roots）
        for prefix in self._dynamic_read_roots():
            if self._is_relative_to(resolved, prefix):
                return True

        for prefix in self._static_read_allowed_prefixes:
            if self._is_relative_to(resolved, prefix):
                return True

        for prefix in self._static_read_allowed_outside_prefixes:
            if self._is_relative_to(resolved, prefix):
                return True

        # 兼容：无 workspace 时回退到 SESSION_CONTEXT 的 output_dir
        session_output = self._get_user_dir()
        if session_output and self._is_relative_to(resolved, Path(session_output)):
            return True

        return False

    def _get_user_dir(self, session_id: str = "") -> Optional[str]:
        """当前可写产物目录：主代理=user_dir，子代理=sandbox outputs（经 SESSION_CONTEXT）。

        解析顺序：
        1. SESSION_CONTEXT["output_dir"]（权威的"当前可写目录"：主代理=user_dir，
           子代理=sandbox outputs，由 set_session_context 注入）
        2. workspace.user_dir（SESSION_CONTEXT 未设时的回退，如非工具执行上下文）
        3. 旧路径回退：session_id 推 data/sessions/<id>/outputs（兼容未注入 workspace 的测试）

        注意：必须 SESSION_CONTEXT 优先——子代理与主代理共享同一个 workspace contextvar
        （继承的 ws），只能靠 SESSION_CONTEXT["output_dir"] 区分主/子的可写目录。
        若 workspace 优先，子代理相对路径写入会错误落到主代理 user_dir，破坏 sandbox 隔离。

        网页版 build_workspace 不传 user_dir 时回退到 session_root/<sid>/outputs，
        与 session_manager.get_output_dir 等价 → 网页版零回归。
        """
        # 1. SESSION_CONTEXT["output_dir"] 优先（区分主/子）
        try:
            from floodmind.tools.session_context import get_current_session_output_dir
            d = get_current_session_output_dir()
            if d:
                return d
        except Exception:
            pass
        # 2. workspace.user_dir 回退
        ws = self._effective_workspace()
        if ws is not None:
            return str(ws.user_dir)
        # 3. 兼容回退：未注入 workspace 且无 SESSION_CONTEXT 时，按旧 session_id 推导
        if session_id:
            data_dir = os.environ.get('DATA_DIR', str(self._project_root / "data"))
            output_dir = os.path.join(data_dir, "sessions", session_id, "outputs")
            if os.path.isdir(output_dir):
                return output_dir
        return None

    @staticmethod
    def _is_relative_to(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base.resolve())
            return True
        except ValueError:
            return False


_global_path_service: Optional[PathService] = None


def get_path_service() -> PathService:
    global _global_path_service
    if _global_path_service is None:
        _global_path_service = PathService()
    return _global_path_service


def set_path_service(svc: PathService) -> None:
    global _global_path_service
    _global_path_service = svc
