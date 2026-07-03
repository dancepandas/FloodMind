"""Tests for TracingService and tracing contracts."""

import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.event_bus import EventBus, StepEventBus
from floodmind.agent.runtime.contracts.tracing import TraceEvent, TraceSpan
from floodmind.agent.runtime.services.tracing_service import TracingService


class TestTracingService:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.svc = TracingService(base_dir=self.tmp)

    def _trace_path(self, session_id: str) -> Path:
        return Path(self.tmp) / session_id / "trace.jsonl"

    def _read_lines(self, session_id: str):
        path = self._trace_path(session_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").strip().split("\n") if line.strip()]

    def test_start_and_end_span(self):
        span = self.svc.start_span("s1", "llm", "test_llm")
        assert span.status == "in_progress"
        assert span.span_id.startswith("span-")

        self.svc.end_span(span.span_id, output={"tokens": 10}, status="ok")
        self.svc.flush("s1")

        lines = self._read_lines("s1")
        assert len(lines) == 1
        assert lines[0]["type"] == "llm"
        assert lines[0]["name"] == "test_llm"
        assert lines[0]["status"] == "ok"
        assert lines[0]["output"]["tokens"] == 10
        assert lines[0]["duration_ms"] is not None
        assert lines[0]["duration_ms"] >= 0

    def test_record_event(self):
        self.svc.record_event(
            "s1",
            "state_transition",
            "created->awaiting_llm",
            input={"iteration": 0},
        )
        self.svc.flush("s1")

        lines = self._read_lines("s1")
        assert len(lines) == 1
        assert lines[0]["type"] == "state_transition"
        assert lines[0]["name"] == "created->awaiting_llm"
        assert lines[0]["input"]["iteration"] == 0
        assert lines[0]["event_id"].startswith("evt-")

    def test_parent_child_nesting(self):
        parent = self.svc.start_span("s1", "llm", "parent")
        child = self.svc.start_span("s1", "tool", "child")
        self.svc.end_span(child.span_id)
        self.svc.end_span(parent.span_id)
        self.svc.flush("s1")

        lines = self._read_lines("s1")
        assert len(lines) == 2
        assert lines[0]["span_id"] == parent.span_id
        assert lines[1]["parent_id"] == parent.span_id

    def test_append_to_existing_file(self):
        self.svc.record_event("s1", "other", "first")
        self.svc.flush("s1")
        self.svc.record_event("s1", "other", "second")
        self.svc.flush("s1")

        lines = self._read_lines("s1")
        assert len(lines) == 2
        assert lines[0]["name"] == "first"
        assert lines[1]["name"] == "second"

    def test_event_bus_integration(self):
        event_bus = EventBus()
        self.svc.register_event_bus(event_bus, "s1")

        event_bus.emit_llm_step_start(model_name="gpt", iteration=1)
        event_bus.emit_llm_step_end(reason="stop", tokens={"total_tokens": 7})
        event_bus.emit_tool_status("Read", "running", tool_input="{}", call_id="c1")
        event_bus.emit_tool_result("Read", "completed", content="hello", call_id="c1")
        # workflow_plan / workflow_step 经 callback 时被跳过（由 agent 手动记录，避免双写）
        event_bus.emit_workflow_plan(title="goal", steps=[{"key": "s1"}])
        event_bus.emit_workflow_step(step_key="s1", status="running")

        self.svc.flush("s1")

        lines = self._read_lines("s1")
        types = [line["type"] for line in lines]
        assert "llm" in types
        assert "tool" in types
        # workflow 事件不应经 callback 记录（防双写）
        assert "workflow" not in types

    def test_event_bus_chains_existing_callback(self):
        event_bus = EventBus()
        existing = MagicMock()
        event_bus.set_persist_callback(existing)

        self.svc.register_event_bus(event_bus, "s1")
        # 用 token_usage 验证 callback 链（workflow 事件已被 callback 跳过）
        event_bus.emit_token_usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        existing.assert_called_once()
        # trace buffer 应通过链式 callback 捕获到 token_usage
        self.svc.flush("s1")
        lines = self._read_lines("s1")
        assert any(line["name"] == "token_usage" for line in lines)

    def test_graceful_degradation_on_error(self, monkeypatch):
        self.svc.record_event("s1", "other", "should_not_crash")

        original_mkdir = Path.mkdir

        def _bad_mkdir(self, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _bad_mkdir)
        try:
            # flush should not raise even though mkdir fails
            self.svc.flush("s1")
        finally:
            monkeypatch.setattr(Path, "mkdir", original_mkdir)

    def test_set_trace_context(self):
        self.svc.set_trace_context("s1", "my-trace-123")
        span = self.svc.start_span("s1", "llm", "test")
        self.svc.end_span(span.span_id)
        self.svc.flush("s1")

        lines = self._read_lines("s1")
        assert len(lines) == 1
        assert lines[0]["trace_id"] == "my-trace-123"

    def test_thread_safety(self):
        def worker(i: int):
            span = self.svc.start_span("s1", "tool", f"tool-{i}")
            self.svc.record_event("s1", "other", f"event-{i}")
            self.svc.end_span(span.span_id)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.svc.flush("s1")
        lines = self._read_lines("s1")
        # 10 spans + 10 events = 20 lines
        assert len(lines) == 20
        assert sum(1 for line in lines if line["type"] == "tool") == 10
        assert sum(1 for line in lines if line["type"] == "other") == 10

    def test_register_event_bus_idempotent(self):
        event_bus = EventBus()
        self.svc.register_event_bus(event_bus, "s1")
        first = event_bus.get_persist_callback()
        self.svc.register_event_bus(event_bus, "s1")
        second = event_bus.get_persist_callback()
        assert first is second

    def test_parallel_step_event_bus_no_crosstalk(self):
        """并行子代理经 StepEventBus 注入 _trace_session，事件各自落入自己的 trace，不串台。"""
        parent_bus = EventBus()
        # 父 bus 注册到 tracing（默认 session = parent）
        self.svc.register_event_bus(parent_bus, "parent")

        # 两个并行子代理用各自的 StepEventBus 包装父 bus
        sub_a_bus = StepEventBus(parent_bus, "step-a", trace_session_id="sub-a")
        sub_b_bus = StepEventBus(parent_bus, "step-b", trace_session_id="sub-b")

        sub_a_bus.emit_tool_status("ToolA", "running", call_id="a")
        sub_b_bus.emit_tool_status("ToolB", "running", call_id="b")
        parent_bus.emit_tool_status("ParentTool", "running", call_id="p")

        self.svc.flush("parent")
        self.svc.flush("sub-a")
        self.svc.flush("sub-b")

        parent_lines = self._read_lines("parent")
        a_lines = self._read_lines("sub-a")
        b_lines = self._read_lines("sub-b")

        # 父事件落父 trace
        assert any(l["name"] == "ParentTool" for l in parent_lines)
        # 子代理事件各自落自己 trace，不串台
        assert any(l["name"] == "ToolA" for l in a_lines)
        assert not any(l["name"] == "ToolB" for l in a_lines)
        assert any(l["name"] == "ToolB" for l in b_lines)
        assert not any(l["name"] == "ToolA" for l in b_lines)

    def test_register_event_bus_updates_session_id(self):
        event_bus = EventBus()
        self.svc.register_event_bus(event_bus, "session-a")
        # 用 action_start（tool span）验证 session 映射切换（workflow 事件被 callback 跳过）
        event_bus.emit_tool_status("ToolA", "running", call_id="a")
        self.svc.register_event_bus(event_bus, "session-b")
        event_bus.emit_tool_status("ToolB", "running", call_id="b")

        self.svc.flush("session-a")
        self.svc.flush("session-b")

        a_lines = self._read_lines("session-a")
        b_lines = self._read_lines("session-b")
        assert len(a_lines) == 1
        assert a_lines[0]["name"] == "ToolA"
        assert len(b_lines) == 1
        assert b_lines[0]["name"] == "ToolB"

    def test_step_event_bus_does_not_mutate_original_event(self):
        """StepEventBus 应在修改 step_key/_trace_session 前复制事件字典。"""
        parent_bus = EventBus()
        step_bus = StepEventBus(parent_bus, "step-a", trace_session_id="sub-a")

        original = {"type": "action_start", "tool_name": "ToolA"}
        original_copy = dict(original)
        step_bus.emit(original)

        assert original == original_copy
        assert "step_key" not in original
        assert "_trace_session" not in original
