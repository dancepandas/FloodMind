"""
会话上下文管理 — 独立的工具层模块

提供跨工具的会话状态访问，避免循环导入。
"""

import contextvars
import os
from pathlib import Path
from typing import Any, Dict, Optional

from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT as _PROJECT_ROOT
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


def set_session_context(
    session_id: str,
    output_dir: Optional[str] = None,
    delegate_cwd: Optional[str] = None,
):
    """注入会话上下文。output_dir 一律由调用方从 Workspace.user_dir 传入。

    语义收窄：不再自己 makedirs _SESSION_ROOT/<id>/outputs 兜底。
    - output_dir 非空：写入 SESSION_CONTEXT 并 makedirs（主代理=user_dir，子代理=sandbox outputs）。
    - output_dir 为空：写入空串，调用方负责保证后续 workspace 已注入。
    - delegate_cwd：阶段C子代理放权——主代理委派时指定子代理工作目录（桌面版并行写
      user_dir 子目录）。None=不覆盖；""=显式清除。

    这样 _get_user_dir 优先取 workspace.user_dir，子代理经本函数注入 sandbox outputs +
    delegate_cwd。PathService 子代理写范围检查会读 delegate_cwd。
    """
    ctx = {
        "session_id": session_id,
        "output_dir": output_dir or "",
    }
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    # delegate_cwd：None 不写入键（保持上一轮值语义清晰起见，每次显式覆盖）
    if delegate_cwd is not None:
        ctx["delegate_cwd"] = delegate_cwd
    _session_ctx_var.set(ctx)


def get_current_delegate_cwd() -> Optional[str]:
    """当前子代理被指定的工作目录（主代理委派时设）。"""
    return _session_ctx_var.get({}).get("delegate_cwd", "")


def get_current_session_output_dir() -> Optional[str]:
    return _session_ctx_var.get({}).get("output_dir")


def get_current_session_id() -> Optional[str]:
    return _session_ctx_var.get({}).get("session_id")
