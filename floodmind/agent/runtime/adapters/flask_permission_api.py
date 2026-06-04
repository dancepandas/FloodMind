"""
Flask Permission API — 适配层

将 HTTP 请求转换为 AskService 调用，不包含业务逻辑。
"""

import logging
from typing import Any

from floodmind.agent.runtime.contracts.permissions import PermissionAskResponse
from floodmind.agent.runtime.services.ask_service import get_ask_service

logger = logging.getLogger(__name__)


def handle_permission_respond(data: dict) -> tuple[dict, int]:
    ask_id = data.get("ask_id", "")
    approved = data.get("approved", False)
    session_id = data.get("session_id", "default")

    if not ask_id:
        return {"status": "error", "message": "ask_id 不能为空"}, 400

    bridge = get_ask_service()
    response = PermissionAskResponse(
        session_id=_require_session_id(session_id),
        ask_id=ask_id,
        approved=bool(approved),
    )
    matched = bridge.respond(response)

    if matched:
        return {"status": "success", "ask_id": ask_id, "approved": bool(approved)}, 200
    return {"status": "error", "message": f"ask_id {ask_id} 不存在、session 不匹配或已超时"}, 404


def handle_permission_pending(session_id: str) -> tuple[dict, int]:
    bridge = get_ask_service()
    return {"status": "success", "pending": [p.model_dump() for p in bridge.pending(session_id=_require_session_id(session_id))]}, 200


def handle_permission_cancel_session(session_id: str) -> int:
    bridge = get_ask_service()
    return bridge.cancel_session(session_id=_require_session_id(session_id))


def _require_session_id(session_id: str) -> str:
    return session_id.strip() or "default"
