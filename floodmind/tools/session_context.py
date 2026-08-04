"""
会话上下文管理 — 独立的工具层模块

提供跨工具的会话状态访问，避免循环导入。该模块是 legacy bridge：
Harness/RunContext 会在每次工具调用前注入 session/workspace/cwd 信息，旧工具仍可
通过 SESSION_CONTEXT 读取。
"""

import contextvars
import os
from typing import Any, Dict, Optional

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
    *,
    cwd: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    state_dir: Optional[str] = None,
    artifact_dir: Optional[str] = None,
    tmp_dir: Optional[str] = None,
    scripts_dir: Optional[str] = None,
):
    """注入会话/工作区上下文。

    ``output_dir`` 保持旧语义：主代理产物/默认写目录，子代理 sandbox outputs。
    新增 ``cwd``/``workspace_dir`` 等字段用于 folder-first harness；未传时回退
    ``output_dir``，保证旧调用不变。

    ``delegate_cwd``：子代理被指定的工作目录。None=不写入键；""=显式清除。
    """
    effective_cwd = cwd or delegate_cwd or output_dir or ""
    effective_workspace = workspace_dir or effective_cwd
    effective_artifact = artifact_dir or output_dir or ""

    ctx = {
        "session_id": session_id,
        "output_dir": output_dir or "",
        "cwd": effective_cwd,
        "workspace_dir": effective_workspace or "",
        "primary_dir": effective_workspace or "",
        "state_dir": state_dir or "",
        "artifact_dir": effective_artifact,
        "tmp_dir": tmp_dir or "",
        "scripts_dir": scripts_dir or "",
    }
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    for d in (state_dir, artifact_dir, tmp_dir, scripts_dir):
        if d:
            os.makedirs(d, exist_ok=True)
    if delegate_cwd is not None:
        ctx["delegate_cwd"] = delegate_cwd
    _session_ctx_var.set(ctx)


def get_current_delegate_cwd() -> Optional[str]:
    """当前子代理被指定的工作目录（主代理委派时设）。"""
    return _session_ctx_var.get({}).get("delegate_cwd", "")


def get_current_session_output_dir() -> Optional[str]:
    return _session_ctx_var.get({}).get("output_dir")


def get_current_cwd() -> Optional[str]:
    return _session_ctx_var.get({}).get("cwd")


def get_current_workspace_dir() -> Optional[str]:
    return _session_ctx_var.get({}).get("workspace_dir")


def get_current_artifact_dir() -> Optional[str]:
    return _session_ctx_var.get({}).get("artifact_dir")


def get_current_session_id() -> Optional[str]:
    return _session_ctx_var.get({}).get("session_id")
