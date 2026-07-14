"""
WorkspaceService — 工作区注入与工厂

与 SESSION_CONTEXT 同模式（contextvars），主代理运行期通过 set_workspace 注入。
PathService / SandboxService 不再各自拼 data/sessions/<id>/outputs，统一从 get_workspace() 取。

网页版零回归保证：build_workspace(sid, session_root=session_manager.sessions_dir) 不传 user_dir 时，
工厂回退到 session_root / sid / outputs，与 session_manager.get_output_dir(sid) 字符串等价。
"""

import contextvars
import logging
from pathlib import Path
from typing import Optional

from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT

logger = logging.getLogger(__name__)

_workspace_var: contextvars.ContextVar[Optional[Workspace]] = contextvars.ContextVar(
    "floodmind_workspace", default=None
)


def get_workspace() -> Optional[Workspace]:
    """取当前 contextvar 中的 Workspace，未注入返回 None。"""
    return _workspace_var.get()


def set_workspace(ws: Optional[Workspace]) -> contextvars.Token:
    """注入 Workspace（传 None 清除）。返回 token 用于 reset。"""
    return _workspace_var.set(ws)


def reset_workspace(token: contextvars.Token) -> None:
    """恢复到 set_workspace 前的状态（测试清理用）。"""
    _workspace_var.reset(token)


def build_workspace(
    session_id: str,
    *,
    session_root: Optional[Path] = None,
    user_dir: Optional[Path] = None,
    sandbox_strategy: Optional[str] = None,
    writable_roots: tuple = (),
    readable_roots: tuple = (),
    overwrite_protection: Optional[bool] = None,
) -> Workspace:
    """构造 Workspace。

    显式参数优先；缺省时从 settings.workspace 取；再缺省回退到网页版默认布局。

    优先级：
    - session_root：显式 > settings.workspace.session_root > PROJECT_ROOT/data/sessions
    - user_dir：显式 > settings.workspace.default_user_dir > session_root/<sid>/outputs
      （最后这一步回退等价于 session_manager.get_output_dir → 网页版零回归）
    - sandbox_strategy：显式 > settings.workspace.sandbox_strategy > "session_root"
    - overwrite_protection：显式 > settings.workspace.overwrite_protection > False

    所有字段最终 resolve 成绝对路径。
    """
    # 从 settings 取缺省（惰性 import 避免循环）
    try:
        from floodmind.config.settings import settings
        ws_cfg = settings.workspace
    except Exception:
        ws_cfg = None

    if session_root is None and ws_cfg and ws_cfg.session_root:
        session_root = Path(ws_cfg.session_root)
    if session_root is None:
        session_root = PROJECT_ROOT / "data" / "sessions"
    session_root = Path(session_root).resolve()

    if user_dir is None and ws_cfg and ws_cfg.default_user_dir:
        user_dir = Path(ws_cfg.default_user_dir)
    if user_dir is None:
        # 网页版回退：等价于 session_manager.get_output_dir(sid)
        user_dir = session_root / session_id / "outputs"
    user_dir = Path(user_dir).resolve()

    if sandbox_strategy is None and ws_cfg:
        sandbox_strategy = ws_cfg.sandbox_strategy
    strategy = (sandbox_strategy or "session_root").lower()
    if strategy == "user_dir":
        sandbox_base = user_dir / ".floodmind" / "sandboxes"
    else:
        sandbox_base = session_root
    sandbox_base = Path(sandbox_base).resolve()

    if overwrite_protection is None:
        overwrite_protection = bool(ws_cfg.overwrite_protection) if ws_cfg else False

    return Workspace(
        user_dir=user_dir,
        session_root=session_root,
        sandbox_base=sandbox_base,
        writable_roots=tuple(Path(p).resolve() for p in writable_roots),
        readable_roots=tuple(Path(p).resolve() for p in readable_roots),
        overwrite_protection=overwrite_protection,
    )