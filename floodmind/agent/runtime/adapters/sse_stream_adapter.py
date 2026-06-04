"""
SSE Stream Adapter — 适配层

将 AskService / PermissionService 的事件转换为 SSE 兼容格式。
web_server.py 只调用此适配器，不直接操作 AskService 内部。
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from floodmind.agent.runtime.contracts.events import VALID_EVENT_TYPES
from floodmind.agent.runtime.services.ask_service import get_ask_service

logger = logging.getLogger(__name__)


def setup_ask_service_emit(emit_fn: Callable[[dict], None]) -> None:
    ask_service = get_ask_service()
    ask_service.set_emit_fn(emit_fn)


def teardown_ask_service_emit() -> None:
    ask_service = get_ask_service()
    ask_service.clear_emit_fn()


def validate_sse_event(event: dict) -> bool:
    event_type = event.get("type", "")
    if event_type.startswith("__"):
        return False
    if event_type not in VALID_EVENT_TYPES:
        logger.debug("Unknown SSE event type: %s", event_type)
    return True


def sanitize_event_for_client(event: dict) -> dict:
    safe = {k: v for k, v in event.items() if not k.startswith("__")}
    return safe
