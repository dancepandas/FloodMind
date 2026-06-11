"""
会话上下文管理 — 独立的工具层模块

提供跨工具的会话状态访问，避免循环导入。
"""

import contextvars
import os
from pathlib import Path
from typing import Any, Dict, Optional

_PROJECT_ROOT = Path.cwd()
_SESSION_ROOT = _PROJECT_ROOT / "data" / "sessions"

_session_ctx_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "session_context",
)


class _SessionContextProxy:
    def get(self, key: str, default: Any = None) -> Any:
        return _session_ctx_var.get({}).get(key, default)

    def __getitem__(self, key: str) -> Any:
        return _session_ctx_var.get({}).get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        ctx = dict(_session_ctx_var.get({}))
        ctx[key] = value
        _session_ctx_var.set(ctx)


SESSION_CONTEXT = _SessionContextProxy()


def set_session_context(session_id: str, output_dir: Optional[str] = None):
    ctx = {
        "session_id": session_id,
        "output_dir": None,
    }
    if output_dir:
        ctx["output_dir"] = output_dir
        os.makedirs(output_dir, exist_ok=True)
    else:
        ctx["output_dir"] = str(_SESSION_ROOT / session_id / "outputs")
        os.makedirs(ctx["output_dir"], exist_ok=True)
    _session_ctx_var.set(ctx)


def get_current_session_output_dir() -> Optional[str]:
    return _session_ctx_var.get({}).get("output_dir")


def get_current_session_id() -> Optional[str]:
    return _session_ctx_var.get({}).get("session_id")
