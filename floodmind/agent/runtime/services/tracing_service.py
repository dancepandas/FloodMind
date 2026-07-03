"""
TracingService — local JSONL observability for the Agent Harness.

Collects TraceSpan / TraceEvent records in memory and flushes them to
`data/sessions/<session_id>/trace.jsonl` on demand. Integrates with the
existing EventBus via its persist_callback hook.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from floodmind.agent.runtime.contracts.tracing import TraceEvent, TraceSpan, TraceStatus, TraceType

logger = logging.getLogger(__name__)


class TracingService:
    """Service that records spans/events and appends them to a per-session JSONL trace file."""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self._base_dir = Path(base_dir)
        else:
            self._base_dir = Path.cwd() / "data" / "sessions"
        self._lock = threading.RLock()
        self._buffers: Dict[str, List[Union[TraceSpan, TraceEvent]]] = {}
        self._active_spans: Dict[str, List[TraceSpan]] = {}
        self._trace_ids: Dict[str, str] = {}
        self._wrapped_callbacks: Dict[int, Callable[[dict], None]] = {}
        self._bus_session_ids: Dict[int, str] = {}

    # ── public API ──────────────────────────────────────────────────────────

    def set_trace_context(self, session_id: str, trace_id: Optional[str] = None) -> str:
        """Set or generate the root trace_id for a session."""
        with self._lock:
            tid = trace_id or f"trace-{session_id}-{self._short_uuid()}"
            self._trace_ids[session_id] = tid
            return tid

    def start_span(
        self,
        session_id: str,
        type: TraceType,
        name: str,
        parent_id: Optional[str] = None,
        input: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceSpan:
        """Start a new span. Auto-assigns parent_id from active stack if not provided."""
        try:
            with self._lock:
                trace_id = self._trace_ids.get(session_id) or self.set_trace_context(session_id)
                if parent_id is None:
                    stack = self._active_spans.get(session_id, [])
                    if stack:
                        parent_id = stack[-1].span_id
                span = TraceSpan(
                    trace_id=trace_id,
                    type=type,
                    name=name,
                    parent_id=parent_id,
                    input=input or {},
                    metadata=metadata or {},
                )
                self._buffers.setdefault(session_id, []).append(span)
                self._active_spans.setdefault(session_id, []).append(span)
                return span
        except Exception as e:
            logger.warning("TracingService.start_span failed: %s", e)
            # Return a no-op span so callers don't crash.
            return TraceSpan(trace_id="", type=type, name=name)

    def end_span(
        self,
        span_id: str,
        output: Optional[Dict[str, Any]] = None,
        status: TraceStatus = "ok",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[TraceSpan]:
        """Finalize a span by span_id."""
        try:
            with self._lock:
                for session_id, items in self._buffers.items():
                    for item in items:
                        if isinstance(item, TraceSpan) and item.span_id == span_id:
                            item.finalize(output=output, status=status, metadata=metadata)
                            stack = self._active_spans.get(session_id, [])
                            if item in stack:
                                idx = stack.index(item)
                                self._active_spans[session_id] = stack[:idx]
                            return item
        except Exception as e:
            logger.warning("TracingService.end_span failed: %s", e)
        return None

    def record_event(
        self,
        session_id: str,
        type: TraceType,
        name: str,
        input: Optional[Dict[str, Any]] = None,
        output: Optional[Dict[str, Any]] = None,
        status: TraceStatus = "ok",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        """Record an instantaneous event."""
        try:
            with self._lock:
                trace_id = self._trace_ids.get(session_id) or self.set_trace_context(session_id)
                event = TraceEvent(
                    trace_id=trace_id,
                    type=type,
                    name=name,
                    input=input or {},
                    output=output or {},
                    status=status,
                    metadata=metadata or {},
                )
                self._buffers.setdefault(session_id, []).append(event)
                return event
        except Exception as e:
            logger.warning("TracingService.record_event failed: %s", e)
            return TraceEvent(trace_id="", type=type, name=name)

    def flush(self, session_id: str) -> Path:
        """Append all buffered spans/events for session_id to trace.jsonl."""
        try:
            with self._lock:
                buffer = self._buffers.get(session_id, [])
                trace_path = self._trace_path(session_id)
                if not buffer:
                    return trace_path
                trace_path.parent.mkdir(parents=True, exist_ok=True)
                with trace_path.open("a", encoding="utf-8") as f:
                    for item in buffer:
                        f.write(item.model_dump_json() + "\n")
                self._buffers[session_id] = []
                return trace_path
        except Exception as e:
            logger.warning("TracingService.flush failed for session %s: %s", session_id, e)
            return self._trace_path(session_id)

    def register_event_bus(self, event_bus: Any, session_id: str) -> None:
        """Register as a persist_callback on event_bus to auto-capture events."""
        try:
            bus_id = id(event_bus)
            if bus_id in self._wrapped_callbacks:
                # Update session_id for an already-registered bus (e.g. resume).
                self._bus_session_ids[bus_id] = session_id
                return

            existing = event_bus.get_persist_callback()
            self._bus_session_ids[bus_id] = session_id

            def _wrapped(event: dict) -> None:
                current_session_id = self._bus_session_ids.get(bus_id, session_id)
                self._on_event_bus_event(current_session_id, event)
                if existing is not None:
                    try:
                        existing(event)
                    except Exception as e:
                        logger.warning("TracingService: existing persist callback error: %s", e)

            event_bus.set_persist_callback(_wrapped)
            self._wrapped_callbacks[bus_id] = _wrapped
        except Exception as e:
            logger.warning("TracingService.register_event_bus failed: %s", e)

    # ── internal helpers ────────────────────────────────────────────────────

    def _trace_path(self, session_id: str) -> Path:
        return self._base_dir / session_id / "trace.jsonl"

    def _on_event_bus_event(self, session_id: str, event: dict) -> None:
        try:
            # 优先使用事件携带的 _trace_session（并行子代理经 StepEventBus 注入），
            # 避免 register_event_bus 的单 session 映射导致串台。
            session_id = event.get("_trace_session") or session_id
            etype = event.get("type", "")

            # 以下事件由 agent 显式 record_event 记录（避免双写），这里跳过：
            # - workflow_plan / workflow_step：_emit_plan_full / _record_workflow_step_event 已记录
            # - permission_ask：无 permission_resolved 配对事件会留下孤儿 span；
            #   权限决策已由 ToolExecutionService 的 permission_decision 事件覆盖
            if etype in ("workflow_plan", "workflow_step", "permission_ask", "permission_resolved"):
                return

            if etype == "llm_step_start":
                self.start_span(
                    session_id,
                    "llm",
                    "llm_step",
                    input={"model": event.get("model"), "iteration": event.get("iteration")},
                )
            elif etype == "llm_step_end":
                self._end_last_span_of_type(
                    session_id,
                    "llm",
                    output={"finish_reason": event.get("finish_reason"), "tokens": event.get("tokens")},
                    status="error" if event.get("finish_reason") in ("error", "timeout") else "ok",
                )
            elif etype == "action_start":
                self.start_span(
                    session_id,
                    "tool",
                    event.get("tool_name", "tool"),
                    input={"tool_input": event.get("tool_input"), "call_id": event.get("call_id")},
                )
            elif etype == "action_end":
                content = event.get("content", "")
                self._end_last_span_of_type(
                    session_id,
                    "tool",
                    output={
                        "status": event.get("status"),
                        "content_length": len(content) if isinstance(content, str) else 0,
                    },
                    status="error" if event.get("status") == "error" else "ok",
                )
            elif etype == "token_usage":
                self.record_event(
                    session_id,
                    "llm",
                    "token_usage",
                    output={
                        "prompt_tokens": event.get("prompt_tokens"),
                        "completion_tokens": event.get("completion_tokens"),
                        "total_tokens": event.get("total_tokens"),
                    },
                )
            elif etype == "error":
                self.record_event(
                    session_id,
                    "other",
                    "error",
                    input={"message": event.get("content"), "code": event.get("code")},
                    status="error",
                )
            elif etype in ("context_compress_start", "context_compress_done"):
                self.record_event(
                    session_id,
                    "other",
                    etype,
                    input={"content": event.get("content")},
                )
            else:
                # Catch-all for unknown events. Skip high-frequency stream deltas
                # to keep trace files compact; LLM/tool spans already cover them.
                if etype in ("answer_delta", "thought_delta", "heartbeat", "attachment_context"):
                    return
                self.record_event(session_id, "other", etype, input=event)
        except Exception as e:
            logger.warning("TracingService._on_event_bus_event failed: %s", e)

    def _end_last_span_of_type(
        self,
        session_id: str,
        type: TraceType,
        output: Optional[Dict[str, Any]] = None,
        status: TraceStatus = "ok",
    ) -> None:
        with self._lock:
            stack = self._active_spans.get(session_id, [])
            for span in reversed(stack):
                if span.type == type:
                    span.finalize(output=output, status=status)
                    idx = stack.index(span)
                    self._active_spans[session_id] = stack[:idx]
                    return

    @staticmethod
    def _short_uuid() -> str:
        import uuid

        return uuid.uuid4().hex[:8]
