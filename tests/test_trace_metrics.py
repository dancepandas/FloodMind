"""G-13：trace 指标快照（OTel GenAI 语义命名）与持久化。"""

import json
from pathlib import Path

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.runtime.contracts.tracing import TraceEvent, TraceSpan
from floodmind.agent.runtime.services.tracing_service import (
    TracingService,
    aggregate_metrics,
)


def _event(name: str, output=None, status="ok") -> TraceEvent:
    return TraceEvent(type="llm", name=name, output=output or {}, status=status)


def _span(kind: str, duration: float, status="ok") -> TraceSpan:
    span = TraceSpan(type=kind, name=f"{kind}-span", status=status)
    span.duration_ms = duration
    return span


class TestAggregateMetrics:
    def test_empty_buffer_is_zeroed(self):
        metrics = aggregate_metrics([])
        assert metrics["gen_ai.client.operation.duration"]["count"] == 0
        assert metrics["gen_ai.client.token.usage"]["total"] == 0
        assert metrics["harness.error.count"] == 0

    def test_durations_and_counts(self):
        items = [
            _span("llm", 100.0),
            _span("llm", 300.0),
            _span("tool", 20.0),
            _span("tool", 40.0, status="error"),
            _event("token_usage", {"prompt_tokens": 10, "completion_tokens": 5}),
            _event("token_usage", {"prompt_tokens": 2, "completion_tokens": 3}),
        ]
        metrics = aggregate_metrics(items)

        llm = metrics["gen_ai.client.operation.duration"]
        assert llm["count"] == 2
        assert llm["p50_ms"] == 100.0
        assert llm["max_ms"] == 300.0
        assert metrics["gen_ai.client.token.usage"] == {
            "unit": "token", "input": 12, "output": 8, "total": 20,
        }
        assert metrics["harness.tool.call.count"] == {"completed": 1, "error": 1}
        assert metrics["harness.model.call.count"] == 2
        assert metrics["harness.error.count"] == 1

    def test_unfinished_spans_are_not_counted(self):
        span = TraceSpan(type="llm", name="running")
        metrics = aggregate_metrics([span])
        assert metrics["gen_ai.client.operation.duration"]["count"] == 0
        assert metrics["harness.tool.call.count"] == {"completed": 0, "error": 0}

    def test_tool_error_counts_once(self):
        metrics = aggregate_metrics([_span("tool", 10.0, status="error")])
        assert metrics["harness.error.count"] == 1
        assert metrics["harness.tool.call.count"]["error"] == 1


class TestTracingServiceMetrics:
    def test_metrics_reflects_live_buffer_then_flush_persists(self, tmp_path):
        svc = TracingService(base_dir=str(tmp_path))
        span = svc.start_span("s1", "llm", "llm_step")
        svc.end_span(span.span_id, status="ok")
        svc.record_event(
            "s1", "llm", "token_usage",
            output={"prompt_tokens": 30, "completion_tokens": 7},
        )

        live = svc.metrics("s1")
        assert live["harness.model.call.count"] == 1
        assert live["gen_ai.client.token.usage"]["total"] == 37

        svc.flush("s1")
        assert svc.metrics("s1")["harness.model.call.count"] == 0

        lines = (tmp_path / "s1" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        snapshot = json.loads(lines[0])
        assert snapshot["session_id"] == "s1"
        assert snapshot["metrics"]["gen_ai.client.token.usage"]["total"] == 37

    def test_event_bus_spans_carry_otel_genai_attributes(self, tmp_path):
        svc = TracingService(base_dir=str(tmp_path))
        bus = EventBus()
        svc.register_event_bus(bus, "s1")

        bus.emit_llm_step_start(model_name="deepseek-v4-flash", iteration=1)
        bus.emit_token_usage(prompt_tokens=11, completion_tokens=4, total_tokens=15)
        bus.emit_llm_step_end(
            reason="stop",
            tokens={"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
        )
        bus.emit_tool_status("Read", "running", call_id="c1")
        bus.emit_tool_result("Read", "completed", "ok", call_id="c1")
        svc.flush("s1")

        trace = [
            json.loads(line)
            for line in (tmp_path / "s1" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        llm = next(item for item in trace if item["type"] == "llm" and item["name"] == "llm_step")
        assert llm["metadata"]["gen_ai.operation.name"] == "chat"
        assert llm["metadata"]["gen_ai.request.model"] == "deepseek-v4-flash"
        assert llm["output"]["gen_ai.usage.input_tokens"] == 11
        assert llm["output"]["gen_ai.usage.output_tokens"] == 4

        tool = next(item for item in trace if item["type"] == "tool")
        assert tool["metadata"]["gen_ai.operation.name"] == "execute_tool"
        assert tool["metadata"]["gen_ai.tool.name"] == "Read"

        metrics = json.loads(
            (tmp_path / "s1" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )["metrics"]
        assert metrics["harness.model.call.count"] == 1
        assert metrics["harness.tool.call.count"]["completed"] == 1


class TestTracingServiceBreakdown:
    """C3/C7：EventBus 事件携带 run_id 后，breakdown 按 run/model 正确归因。"""

    PRICING = {"deepseek-v4-flash": {"input_per_1k": 0.1, "output_per_1k": 0.3}}

    def test_metrics_breakdown_present_without_changing_existing_keys(self, tmp_path):
        svc = TracingService(base_dir=str(tmp_path))
        span = svc.start_span("s1", "llm", "llm_step")
        svc.end_span(span.span_id, status="ok")

        live = svc.metrics("s1")
        # 现有键保持不变
        assert live["harness.model.call.count"] == 1
        # 新增 breakdown，无定价时成本为 None
        assert live["breakdown"]["totals"]["estimated_cost"] is None
        assert live["breakdown"]["by_model"]["unknown"]["llm_calls"] == 1

    def test_event_bus_run_id_flushed_breakdown(self, tmp_path):
        svc = TracingService(base_dir=str(tmp_path), pricing=self.PRICING)
        bus = EventBus()
        svc.register_event_bus(bus, "s1")

        bus.emit_llm_step_start(model_name="deepseek-v4-flash", iteration=1, run_id="run-9")
        bus.emit_token_usage(
            prompt_tokens=1000, completion_tokens=1000, total_tokens=2000, run_id="run-9"
        )
        bus.emit_llm_step_end(
            reason="stop",
            tokens={"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
        )
        bus.emit_tool_status("Read", "running", call_id="c1", run_id="run-9")
        bus.emit_tool_result("Read", "completed", "ok", call_id="c1", run_id="run-9")
        svc.flush("s1")

        snapshot = json.loads(
            (tmp_path / "s1" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )["metrics"]
        breakdown = snapshot["breakdown"]
        # by_run 归因：llm span metadata 带 harness.run_id
        assert breakdown["by_run"]["run-9"]["input_tokens"] == 1000
        assert breakdown["by_run"]["run-9"]["output_tokens"] == 1000
        # by_model 归因 + 成本（1000/1000 * 0.1/0.3 = 0.4）
        assert breakdown["by_model"]["deepseek-v4-flash"]["total_tokens"] == 2000
        assert breakdown["by_model"]["deepseek-v4-flash"]["estimated_cost"] == 0.4
        assert breakdown["totals"]["estimated_cost"] == 0.4
        assert breakdown["totals"]["currency"] == "USD"
        assert breakdown["totals"]["unpriced_models"] == []
        # by_tool：trace span 由 action_start/action_end 构建
        assert breakdown["by_tool"]["Read"]["calls"] == 1
        assert breakdown["by_tool"]["Read"]["errors"] == 0

        trace = [
            json.loads(line)
            for line in (tmp_path / "s1" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        llm = next(item for item in trace if item["type"] == "llm" and item["name"] == "llm_step")
        assert llm["metadata"]["harness.run_id"] == "run-9"
        assert llm["metadata"]["harness.iteration"] == "1"
        tool = next(item for item in trace if item["type"] == "tool")
        assert tool["metadata"]["harness.run_id"] == "run-9"
        token_evt = next(item for item in trace if item["name"] == "token_usage")
        assert token_evt["output"]["run_id"] == "run-9"

    def test_step_key_attributed_to_span_metadata(self, tmp_path):
        from floodmind.agent.native.event_bus import StepEventBus

        svc = TracingService(base_dir=str(tmp_path))
        parent = EventBus()
        svc.register_event_bus(parent, "s1")
        sub = StepEventBus(parent, step_key="step-A", trace_session_id="")

        sub.emit_llm_step_start(model_name="m", iteration=1, run_id="run-1")
        sub.emit_llm_step_end(reason="stop", tokens={"prompt_tokens": 3, "completion_tokens": 2})
        svc.flush("s1")

        trace = [
            json.loads(line)
            for line in (tmp_path / "s1" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        llm = next(item for item in trace if item["type"] == "llm" and item["name"] == "llm_step")
        assert llm["metadata"]["harness.step_key"] == "step-A"

        breakdown = json.loads(
            (tmp_path / "s1" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )["metrics"]["breakdown"]
        assert breakdown["by_step"]["step-A"]["total_tokens"] == 5
