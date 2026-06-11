"""
Todo 任务管理工具

提供全量写入和列出功能，LLM 通过 TodoWrite 维护任务列表，
通过 TodoList 查看当前任务状态。
"""

import json
import logging
import time
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
from floodmind.tools.agent_tool import build_agent_tool, make_readonly_permission_fn

logger = logging.getLogger(__name__)

# per-session todo storage — module-level dict keyed by session_id
# (contextvar doesn't work here because tool execution may span threads/contexts)
_todo_store: Dict[str, List[Dict[str, Any]]] = {}

# event bus reference — module-level variable, set once by native_flood_agent._init_tools
_event_bus: Any = None

# session_id resolver — set once during _init_tools
_get_session_id: Any = None


def _now() -> float:
    return time.time()


class TodoItemInput(BaseModel):
    id: str = Field(description="任务唯一标识")
    content: str = Field(description="任务内容")
    status: str = Field(default="pending", description="状态: pending / in_progress / completed / cancelled")
    priority: str = Field(default="normal", description="优先级: high / normal / low")


class TodoWriteInput(BaseModel):
    todos: List[TodoItemInput] = Field(description="完整的任务列表（全量替换）")


class TodoListInput(BaseModel):
    pass


def set_todo_event_bus(event_bus: Any) -> None:
    """注入 EventBus 引用和 session_id 获取函数，供 TodoWrite 发出事件。"""
    global _event_bus, _get_session_id
    _event_bus = event_bus
    from floodmind.tools.session_context import get_current_session_id
    _get_session_id = get_current_session_id
    logger.info("[TodoWrite] set_todo_event_bus called, _event_bus=%s", event_bus is not None)


def _normalize_todo(raw: dict) -> dict:
    """将原始 todo 数据规范化为标准字典。"""
    status = str(raw.get("status", "pending")).lower().strip()
    if status not in ("pending", "in_progress", "completed", "cancelled"):
        status = "pending"
    priority = str(raw.get("priority", "normal")).lower().strip()
    if priority not in ("high", "normal", "low"):
        priority = "normal"
    return {
        "id": str(raw.get("id", "")).strip() or f"todo_{int(_now() * 1000)}",
        "content": str(raw.get("content", "")).strip(),
        "status": status,
        "priority": priority,
        "created_at": raw.get("created_at", _now()),
        "updated_at": _now(),
    }


def _format_todo_response(todos: List[dict]) -> str:
    """格式化任务列表供 LLM 阅读。"""
    if not todos:
        return "当前没有待办任务。"
    lines = ["=== 任务列表 ==="]
    for t in todos:
        icon = {
            "pending": "⬜",
            "in_progress": "🔄",
            "completed": "✅",
            "cancelled": "❌",
        }.get(t["status"], "⬜")
        prio = {
            "high": "【高】",
            "normal": "",
            "low": "【低】",
        }.get(t["priority"], "")
        lines.append(f"{icon} {prio}{t['content']} (id: {t['id']})")
    return "\n".join(lines)


def _impl_todo_write(todos=None) -> str:
    """全量替换 todo 列表。"""
    if todos is None:
        todos = []

    # 防御：LLM 有时传入 JSON 字符串
    if isinstance(todos, str):
        try:
            todos = json.loads(todos)
        except json.JSONDecodeError:
            return "错误：todos 参数格式不正确，应为任务列表"

    if not isinstance(todos, list):
        return "错误：todos 参数应为列表"

    todo_items = [_normalize_todo(t) for t in todos]

    # 按 session_id 隔离存储
    sid = _get_session_id() if _get_session_id else "default"
    sid = sid or "default"
    _todo_store[sid] = todo_items

    # 发出前端事件
    logger.info("[TodoWrite] emit todo_updated, items=%d, _event_bus=%s, _get_session_id=%s",
                len(todo_items), _event_bus is not None, _get_session_id is not None)
    if _event_bus is not None:
        try:
            _event_bus.emit_todo_updated(todo_items)
            logger.info("[TodoWrite] todo_updated emitted successfully")
        except Exception as e:
            logger.warning("[TodoWrite] emit_todo_updated failed: %s", e)
    else:
        logger.warning("[TodoWrite] _event_bus is None — todo_updated will NOT be sent to frontend")

    return _format_todo_response(todo_items)


def _impl_todo_list() -> str:
    """列出当前所有任务。"""
    sid = _get_session_id() if _get_session_id else "default"
    sid = sid or "default"
    todos = _todo_store.get(sid, [])
    return _format_todo_response(todos)


todo_write = build_agent_tool(
    name="TodoWrite",
    description=(
        "全量写入或更新任务列表。每次调用时必须提供完整的任务列表，"
        "系统会用新列表完全替换旧列表。支持的状态：pending(待办)、in_progress(进行中)、"
        "completed(已完成)、cancelled(已取消)。"
    ),
    args_schema=TodoWriteInput,
    func=_impl_todo_write,
    is_readonly=False,
    is_destructive=False,
    is_concurrency_safe=False,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="state_write"),
)


todo_list = build_agent_tool(
    name="TodoList",
    description="列出当前会话中的所有任务及其状态。",
    args_schema=TodoListInput,
    func=_impl_todo_list,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)
