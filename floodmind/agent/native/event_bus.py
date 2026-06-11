"""
Native Agent Runtime - EventBus

输出 SSE 兼容事件，与现有 web_server.py 事件协议对齐。
"""

import logging
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._listeners: List[Callable[[dict], None]] = []
        self._queue: Optional[queue.Queue] = None
        self._lock = threading.RLock()
        self._persist_callback: Optional[Callable[[dict], None]] = None

    def set_queue(self, q: queue.Queue) -> None:
        with self._lock:
            self._queue = q

    def clear_queue(self) -> None:
        with self._lock:
            self._queue = None

    def set_persist_callback(self, cb: Optional[Callable[[dict], None]]) -> None:
        """Set a callback invoked on every emit() for event persistence (SyncEvent)."""
        with self._lock:
            self._persist_callback = cb

    def add_listener(self, listener: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[dict], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def clear_listeners(self) -> None:
        with self._lock:
            self._listeners.clear()

    def emit(self, event: dict) -> None:
        with self._lock:
            if self._queue is not None:
                self._queue.put(event)
            listeners = list(self._listeners)
            persist = self._persist_callback
        # Persist to sync log (outside lock to avoid DB contention in callback)
        if persist:
            try:
                persist(event)
            except Exception as e:
                logger.warning("EventBus persist callback error: %s", e)
        for listener in listeners:
            try:
                listener(event)
            except Exception as e:
                logger.warning("EventBus listener error: %s", e)

    def emit_reasoning(self, content: str) -> None:
        self.emit({"type": "thought_delta", "content": content})

    def emit_token(self, content: str) -> None:
        self.emit({"type": "answer_delta", "content": content})

    def emit_tool_status(self, tool_name: str, status: str, tool_input: str = "", call_id: str = "") -> None:
        event: Dict[str, Any] = {"type": "action_start", "tool_name": tool_name, "status": status}
        if call_id:
            event["call_id"] = call_id
        if tool_input:
            event["tool_input"] = tool_input
        self.emit(event)

    def emit_tool_result(self, tool_name: str, status: str, content: str, tool_input: str = "", call_id: str = "") -> None:
        event: Dict[str, Any] = {
            "type": "action_end",
            "tool_name": tool_name,
            "status": status,
            "content": content,
        }
        if call_id:
            event["call_id"] = call_id
        if tool_input:
            event["tool_input"] = tool_input
        self.emit(event)

    def emit_workflow_plan(self, title: str, steps: List[dict]) -> None:
        self.emit({
            "type": "workflow_plan",
            "title": title,
            "steps": steps,
        })

    def emit_workflow_step(self, step_key: str, status: str, title: str = "", detail: str = "", label: str = "", outcome: str = "") -> None:
        event: Dict[str, Any] = {
            "type": "workflow_step",
            "step_key": step_key,
            "status": status,
        }
        if title:
            event["title"] = title
        if detail:
            event["detail"] = detail
        if label:
            event["label"] = label
        if outcome:
            event["outcome"] = outcome
        self.emit(event)

    def emit_file_generated(self, file_name: str, download_url: str, size: int = 0) -> None:
        self.emit({
            "type": "file_generated",
            "filename": file_name,
            "file_name": file_name,
            "download_url": download_url,
            "size": size,
        })

    def emit_image_generated(self, file_name: str, download_url: str, size: int = 0) -> None:
        self.emit({
            "type": "image_generated",
            "filename": file_name,
            "file_name": file_name,
            "download_url": download_url,
            "image_url": download_url,
            "size": size,
        })

    def emit_heartbeat(self) -> None:
        self.emit({"type": "heartbeat"})

    def emit_error(self, message: str, code: str = "") -> None:
        event: Dict[str, Any] = {"type": "error", "content": message}
        if code:
            event["code"] = code
        self.emit(event)

    def emit_attachment_context(self, images: List[dict]) -> None:
        self.emit({"type": "attachment_context", "images": images})

    def emit_permission_ask(self, ask_id: str, tool_name: str, reason: str, tool_input: Dict[str, Any], session_id: str = "", call_id: str = "") -> None:
        event: Dict[str, Any] = {
            "type": "permission_ask",
            "ask_id": ask_id,
            "session_id": session_id,
            "call_id": call_id,
            "tool_name": tool_name,
            "reason": reason,
            "tool_input": tool_input,
        }
        self.emit(event)

    def emit_context_compress_start(self) -> None:
        """发送上下文压缩开始事件"""
        self.emit({"type": "context_compress_start", "content": "正在压缩历史对话..."})

    def emit_context_compress_done(self, summary: str) -> None:
        """发送上下文压缩完成事件，附带结构化摘要"""
        self.emit({"type": "context_compress_done", "content": summary})

    def emit_todo_updated(self, todos: List[dict]) -> None:
        """发送 Todo 列表更新事件"""
        logger.info("[EventBus] emit_todo_updated, items=%d, queue_set=%s", len(todos), self._queue is not None)
        self.emit({"type": "todo_updated", "todos": todos})

    def emit_token_usage(self, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0) -> None:
        """发送 token 用量统计事件"""
        self.emit({
            "type": "token_usage",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        })

    # ── Step 生命周期事件 ─────────────────

    def emit_llm_step_start(self, model_name: str = "", iteration: int = 0) -> None:
        """LLM 调用开始事件。前端可显示当前使用的模型和迭代序号。"""
        event: Dict[str, Any] = {
            "type": "llm_step_start",
            "iteration": iteration,
        }
        if model_name:
            event["model"] = model_name
        self.emit(event)

    def emit_llm_step_end(self, reason: str = "stop", tokens: Optional[Dict[str, int]] = None) -> None:
        """LLM 调用结束事件。reason: 'stop'|'tool_calls'|'length'|'content_filter'"""
        event: Dict[str, Any] = {
            "type": "llm_step_end",
            "finish_reason": reason,
            "tokens": tokens or {},
        }
        self.emit(event)


class StepEventBus:
    """EventBus 子通道：给所有事件附加 step_key，用于并行委派时区分不同步骤的事件"""

    def __init__(self, parent: EventBus, step_key: str):
        self._parent = parent
        self._step_key = step_key

    def emit(self, event: dict) -> None:
        if "step_key" not in event:
            event["step_key"] = self._step_key
        self._parent.emit(event)

    def emit_reasoning(self, content: str) -> None:
        self.emit({"type": "thought_delta", "content": content})

    def emit_token(self, content: str) -> None:
        self.emit({"type": "answer_delta", "content": content})

    def emit_tool_status(self, tool_name: str, status: str, tool_input: str = "", call_id: str = "") -> None:
        event: Dict[str, Any] = {"type": "action_start", "tool_name": tool_name, "status": status}
        if call_id:
            event["call_id"] = call_id
        if tool_input:
            event["tool_input"] = tool_input
        self.emit(event)

    def emit_tool_result(self, tool_name: str, status: str, content: str, tool_input: str = "", call_id: str = "") -> None:
        event: Dict[str, Any] = {
            "type": "action_end",
            "tool_name": tool_name,
            "status": status,
            "content": content,
        }
        if call_id:
            event["call_id"] = call_id
        if tool_input:
            event["tool_input"] = tool_input
        self.emit(event)

    def emit_workflow_plan(self, title: str, steps: List[dict]) -> None:
        self.emit({
            "type": "workflow_plan",
            "title": title,
            "steps": steps,
        })

    def emit_workflow_step(self, step_key: str, status: str, title: str = "", detail: str = "", label: str = "", outcome: str = "") -> None:
        event: Dict[str, Any] = {
            "type": "workflow_step",
            "step_key": step_key,
            "status": status,
        }
        if title:
            event["title"] = title
        if detail:
            event["detail"] = detail
        if label:
            event["label"] = label
        if outcome:
            event["outcome"] = outcome
        self.emit(event)

    def emit_file_generated(self, file_name: str, download_url: str, size: int = 0) -> None:
        self.emit({
            "type": "file_generated",
            "filename": file_name,
            "file_name": file_name,
            "download_url": download_url,
            "size": size,
        })

    def emit_image_generated(self, file_name: str, download_url: str, size: int = 0) -> None:
        self.emit({
            "type": "image_generated",
            "filename": file_name,
            "file_name": file_name,
            "download_url": download_url,
            "image_url": download_url,
            "size": size,
        })

    def emit_heartbeat(self) -> None:
        self.emit({"type": "heartbeat"})

    def emit_error(self, message: str, code: str = "") -> None:
        event: Dict[str, Any] = {"type": "error", "content": message}
        if code:
            event["code"] = code
        self.emit(event)

    def emit_attachment_context(self, images: List[dict]) -> None:
        self.emit({"type": "attachment_context", "images": images})

    def emit_permission_ask(self, ask_id: str, tool_name: str, reason: str, tool_input: Dict[str, Any], session_id: str = "", call_id: str = "") -> None:
        event: Dict[str, Any] = {
            "type": "permission_ask",
            "ask_id": ask_id,
            "session_id": session_id,
            "call_id": call_id,
            "tool_name": tool_name,
            "reason": reason,
            "tool_input": tool_input,
        }
        self.emit(event)

    def emit_context_compress_start(self) -> None:
        self.emit({"type": "context_compress_start", "content": "正在压缩历史对话..."})

    def emit_context_compress_done(self, summary: str) -> None:
        self.emit({"type": "context_compress_done", "content": summary})

    def emit_token_usage(self, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0) -> None:
        self.emit({
            "type": "token_usage",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        })

    def emit_llm_step_start(self, model_name: str = "", iteration: int = 0) -> None:
        event: Dict[str, Any] = {"type": "llm_step_start", "iteration": iteration}
        if model_name:
            event["model"] = model_name
        self.emit(event)

    def emit_llm_step_end(self, reason: str = "stop", tokens: Optional[Dict[str, int]] = None) -> None:
        event: Dict[str, Any] = {
            "type": "llm_step_end",
            "finish_reason": reason,
            "tokens": tokens or {},
        }
        self.emit(event)
