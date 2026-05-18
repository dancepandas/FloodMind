"""
AskService — 结构化 ASK 确认服务

将权限 ASK 从"永远自动拒绝"升级为"发射事件 → 阻塞等待用户响应"。
独立于 Flask / EventBus 实现，可替换为 Redis / 消息队列。

设计原则：
- 模块独立、可迁移、无散落修改
- ask_id 是全局唯一标识，跨 session 隔离
- session_id 绑定，防止跨会话授权
- request() 保证先发 action_start 再发 permission_ask
"""

import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from agent.runtime.contracts.permissions import (
    PermissionAskRequest,
    PermissionAskResponse,
    PermissionAskSnapshot,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = None


class _PendingAsk:
    __slots__ = ("ask_id", "session_id", "call_id", "tool_name", "reason", "tool_input", "event", "result", "created_at")

    def __init__(self, ask_id: str, session_id: str, call_id: str, tool_name: str, reason: str, tool_input: Dict[str, Any]):
        self.ask_id = ask_id
        self.session_id = session_id
        self.call_id = call_id
        self.tool_name = tool_name
        self.reason = reason
        self.tool_input = tool_input
        self.event = threading.Event()
        self.result: Optional[bool] = None
        self.created_at = time.time()


class AskService:
    def __init__(self, timeout: Optional[float] = _DEFAULT_TIMEOUT):
        self._timeout = timeout
        self._pending: Dict[str, _PendingAsk] = {}
        self._lock = threading.RLock()
        self._emit_fn: Optional[Callable[[dict], None]] = None
        self._emit_fns: Dict[str, Callable[[dict], None]] = {}

    def set_emit_fn(self, fn: Callable[[dict], None], session_id: str = "") -> None:
        with self._lock:
            if session_id:
                self._emit_fns[session_id] = fn
            else:
                self._emit_fn = fn

    def clear_emit_fn(self, session_id: str = "") -> None:
        with self._lock:
            if session_id:
                self._emit_fns.pop(session_id, None)
            else:
                self._emit_fn = None

    def request(self, ask: PermissionAskRequest) -> bool:
        ask_id = f"ask-{uuid.uuid4().hex[:12]}"
        pending = _PendingAsk(ask_id, ask.session_id, ask.call_id, ask.tool_name, ask.reason, ask.tool_input)

        with self._lock:
            self._pending[ask_id] = pending

        with self._lock:
            emit_fn = self._emit_fns.get(ask.session_id) or self._emit_fn

        if emit_fn is None:
            logger.warning("AskService: emit_fn 未设置，自动拒绝 ASK %s", ask_id)
            with self._lock:
                self._pending.pop(ask_id, None)
            return False

        if ask.call_id:
            emit_fn({
                "type": "action_start",
                "call_id": ask.call_id,
                "tool_name": ask.tool_name,
                "status": "pending_confirmation",
            })

        emit_fn({
            "type": "permission_ask",
            "ask_id": ask_id,
            "session_id": ask.session_id,
            "call_id": ask.call_id,
            "tool_name": ask.tool_name,
            "reason": ask.reason,
            "tool_input": ask.tool_input,
        })

        if self._timeout is None:
            logger.info("AskService: ASK %s 已发射，等待用户响应", ask_id)
        else:
            logger.info("AskService: ASK %s 已发射，等待用户响应（超时 %ds）", ask_id, int(self._timeout))

        pending.event.wait(timeout=self._timeout)

        with self._lock:
            self._pending.pop(ask_id, None)

        if self._timeout is not None and pending.result is None:
            logger.warning("AskService: ASK %s 超时，自动拒绝", ask_id)
            return False

        approved = pending.result

        emit_fn({
            "type": "permission_resolved",
            "session_id": ask.session_id,
            "call_id": ask.call_id,
            "ask_id": ask_id,
            "approved": approved,
        })

        logger.info("AskService: ASK %s 用户响应: %s", ask_id, "允许" if approved else "拒绝")
        return approved

    def respond(self, response: PermissionAskResponse) -> bool:
        with self._lock:
            pending = self._pending.get(response.ask_id)

            if pending is None:
                logger.warning("AskService: respond 收到未知 ask_id %s", response.ask_id)
                return False

            if response.session_id and pending.session_id and response.session_id != pending.session_id:
                logger.warning("AskService: respond session 不匹配，ask_id=%s, expected=%s, got=%s", response.ask_id, pending.session_id, response.session_id)
                return False

            pending.result = response.approved
            pending.event.set()
            return True

    def pending(self, session_id: str = "") -> List[PermissionAskSnapshot]:
        with self._lock:
            items = self._pending.values()
            if session_id:
                items = [p for p in items if p.session_id == session_id]
            return [
                PermissionAskSnapshot(
                    ask_id=p.ask_id,
                    session_id=p.session_id,
                    call_id=p.call_id,
                    tool_name=p.tool_name,
                    reason=p.reason,
                    created_at=p.created_at,
                )
                for p in items
            ]

    def cancel_session(self, session_id: str) -> int:
        with self._lock:
            to_cancel = {k: v for k, v in self._pending.items() if v.session_id == session_id}
            count = len(to_cancel)
            for p in to_cancel.values():
                p.result = False
                p.event.set()
            for k in to_cancel:
                self._pending.pop(k, None)
        return count

    def cancel_all(self) -> int:
        with self._lock:
            count = len(self._pending)
            for p in self._pending.values():
                p.result = False
                p.event.set()
            self._pending.clear()
        return count


_global_ask_service: Optional[AskService] = None


def get_ask_service() -> AskService:
    global _global_ask_service
    if _global_ask_service is None:
        _global_ask_service = AskService()
    return _global_ask_service


def set_ask_service(svc: AskService) -> None:
    global _global_ask_service
    _global_ask_service = svc
    logger.info("AskService 已接入")
