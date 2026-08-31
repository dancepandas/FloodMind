"""FloodMind Gateway — 把 SDK Agent Runtime 暴露为 HTTP 服务 + Web 界面。

设计（参考 OpenHands SDK conversation 服务与 Claude Code 权限交互模式）：
- 会话层复用 SessionManager（列表/删除/历史投影/LRU 淘汰），Agent 由工厂按会话惰性创建；
- 对话走 `Agent.stream()` 的 SSE 流：answer/thought/tool/permission_ask 等事件原样转发；
- 权限 ASK 是非阻塞的：前端收到 `permission_ask` 事件后调
  `POST /api/permission/respond` 应答，executor 在 awaiting_permission 轮询处续跑；
- 鉴权：所有 /api 路由要求 `Authorization: Bearer <token>`。token 来源优先级：
  显式参数 > 环境变量 FLOODMIND_GATEWAY_TOKEN > settings.json `gateway.auth_token`
  > 首次启动自动生成并持久化（控制台打印）。
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from floodmind.agent.api import Agent
from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.services.ask_service import get_ask_service
from floodmind.memory.session_manager import SessionManager
from floodmind.common.filelock import FileLock

logger = logging.getLogger(__name__)

_SETTINGS_FILE = Path.home() / ".floodmind" / "settings.json"


class GatewayState:
    """Gateway 运行时状态：SessionManager、每会话 abort 事件、鉴权 token。"""

    def __init__(
        self,
        workspace_root: Path,
        auth_token: str,
        max_active_sessions: int = 20,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.auth_token = auth_token
        self._abort_events: Dict[str, threading.Event] = {}
        self._abort_lock = threading.Lock()
        data_dir = self.workspace_root / "data"
        # 注意：SessionManager 的清理线程（retention/idle 淘汰）在此刻意不启动——
        # gateway 会话由用户显式删除，自动 rmtree 与运行中的 journal 写入存在竞争。
        # 以下 retention 配置仅作为数据落盘布局参考，不生效（P2-11）。
        self.sessions = SessionManager(config={
            "data_dir": str(data_dir),
            "max_active_sessions": max_active_sessions,
            "idle_timeout_minutes": 24 * 60,
            "session_retention_days": 90,
        })

    # ── Agent 工厂 ────────────────────────────────────────────

    def _build_llm(self):
        from floodmind.config.model_resolver import resolve_model
        from floodmind.agent.native.model_client import ModelClient

        rm = resolve_model()
        return ModelClient(
            rm.api_key,
            rm.base_url,
            rm.id,
            temperature=rm.temperature,
            max_tokens=rm.max_tokens,
            provider=rm.provider,
        )

    def agent_factory(self, session_id: str) -> Agent:
        """完整 runtime Agent：内置工具 + Skill + MCP + 权限 ASK + folder-first 工作区。"""
        workspace = Workspace.from_folder(
            str(self.workspace_root), session_id=session_id
        ).ensure()
        return Agent(
            llm=self._build_llm(),
            workspace=workspace,
            session_id=session_id,
            bare=False,
        )

    # ── abort 管理 ────────────────────────────────────────────

    def register_abort(self, session_id: str) -> threading.Event:
        event = threading.Event()
        with self._abort_lock:
            self._abort_events[session_id] = event
        return event

    def release_abort(self, session_id: str, event: threading.Event) -> None:
        with self._abort_lock:
            if self._abort_events.get(session_id) is event:
                self._abort_events.pop(session_id, None)

    def trigger_abort(self, session_id: str) -> bool:
        with self._abort_lock:
            event = self._abort_events.get(session_id)
        if event is not None:
            event.set()
            return True
        return False


def resolve_auth_token(explicit: Optional[str] = None) -> str:
    """解析/生成鉴权 token，并保证持久化（重启后不变）。"""
    import os

    token = (explicit or os.getenv("FLOODMIND_GATEWAY_TOKEN") or "").strip()
    if token:
        return token

    with FileLock(_SETTINGS_FILE.with_suffix(".json.lock"), timeout=10.0):
        cfg: Dict[str, Any] = {}
        if _SETTINGS_FILE.exists():
            try:
                cfg = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        token = str((cfg.get("gateway") or {}).get("auth_token") or "").strip()
        if token:
            return token
        token = f"fmgt-{secrets.token_hex(20)}"
        cfg.setdefault("gateway", {})["auth_token"] = token
        _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_SETTINGS_FILE)
        logger.info("Gateway 鉴权 token 已生成并写入 %s", _SETTINGS_FILE)
        return token
