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

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path.cwd()

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
    def __init__(self, project_root: Optional[Path] = None):
        self._project_root = (project_root or _PROJECT_ROOT).resolve()

    def resolve(self, request: PathResolveRequest) -> PathResolveResult:
        raw = str(request.raw_path).strip().strip('"').strip("'")
        normalized = self._strip_session_prefix(raw)
        p = Path(normalized)

        if p.is_absolute():
            resolved = p.resolve()
            allowed, reason = self._check_path_allowed(resolved, request.access)
            return PathResolveResult(
                raw_path=raw,
                normalized_path=normalized,
                resolved_path=str(resolved),
                source="absolute",
                allowed=allowed,
                reason=reason,
            )

        if request.access in ("write", "exec", "cwd"):
            output_dir = self._get_session_output_dir(request.session_id)
            if output_dir:
                resolved = (Path(output_dir) / p).resolve()
                allowed, reason = self._check_path_allowed(resolved, request.access)
                return PathResolveResult(
                    raw_path=raw,
                    normalized_path=normalized,
                    resolved_path=str(resolved),
                    source="session_output",
                    allowed=allowed,
                    reason=reason,
                )

            dialog_dir = self._get_dialog_output_dir(request.session_id)
            if dialog_dir:
                resolved = (Path(dialog_dir) / p).resolve()
                allowed, reason = self._check_path_allowed(resolved, request.access)
                return PathResolveResult(
                    raw_path=raw,
                    normalized_path=normalized,
                    resolved_path=str(resolved),
                    source="dialog_fallback",
                    allowed=allowed,
                    reason=reason,
                )

            return PathResolveResult(
                raw_path=raw,
                normalized_path=normalized,
                resolved_path=str((self._project_root / p).resolve()),
                source="no_context_rejected",
                allowed=False,
                reason="无会话上下文时相对路径写入被拒绝。正确做法：只写文件名（如 result.py），系统会自动写入当前对话输出目录。不要传 data/sessions/... 等目录前缀。",
            )

        output_dir = self._get_session_output_dir(request.session_id)
        if output_dir:
            resolved = (Path(output_dir) / p).resolve()
            allowed, reason = self._check_path_allowed(resolved, request.access)
            return PathResolveResult(
                raw_path=raw,
                normalized_path=normalized,
                resolved_path=str(resolved),
                source="session_output",
                allowed=allowed,
                reason=reason,
            )

        resolved = (self._project_root / p).resolve()
        allowed, reason = self._check_path_allowed(resolved, request.access)
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

        for prefix in _WRITE_ALLOWED_PREFIXES:
            if self._is_relative_to(resolved, prefix):
                return True

        project_root = self._project_root.resolve()
        if self._is_relative_to(resolved, project_root):
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

    def _check_path_allowed(self, resolved: Path, access: str) -> tuple:
        for pattern in _FORBIDDEN_PATH_PATTERNS:
            if pattern.match(str(resolved)):
                return False, f"禁止访问系统目录: {pattern.pattern}"
        if access in ("write", "exec", "cwd") and not self.is_write_allowed(resolved):
            return False, f"写入路径 {resolved} 不在允许目录内"
        if access == "read" and not self.is_read_allowed(resolved):
            return False, f"读取路径 {resolved} 不在允许目录内"
        return True, ""

    def is_read_allowed(self, resolved: Path) -> bool:
        try:
            resolved = resolved.resolve()
        except Exception:
            return False

        for pattern in _FORBIDDEN_PATH_PATTERNS:
            if pattern.match(str(resolved)):
                return False

        for prefix in _READ_ALLOWED_PREFIXES:
            if self._is_relative_to(resolved, prefix):
                return True

        for prefix in _READ_ALLOWED_OUTSIDE_PREFIXES:
            if self._is_relative_to(resolved, prefix):
                return True

        session_output = self._get_session_output_dir()
        if session_output and self._is_relative_to(resolved, Path(session_output)):
            return True

        return False

    def _get_session_output_dir(self, session_id: str = "") -> Optional[str]:
        if session_id:
            data_dir = os.environ.get('DATA_DIR', str(self._project_root / "data"))
            output_dir = os.path.join(data_dir, "sessions", session_id, "outputs")
            if os.path.isdir(output_dir):
                return output_dir
        try:
            from floodmind.tools.base_tools import get_current_session_output_dir
            d = get_current_session_output_dir()
            if d:
                return d
        except Exception:
            pass
        return None

    def _get_dialog_output_dir(self, session_id: str = "") -> Optional[str]:
        try:
            from floodmind.tools.base_tools import _get_active_session_id, _SESSION_ROOT
            active_id = session_id or _get_active_session_id()
            if active_id:
                candidate = str(_SESSION_ROOT / active_id / "outputs")
                os.makedirs(candidate, exist_ok=True)
                return candidate
        except Exception:
            pass
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
