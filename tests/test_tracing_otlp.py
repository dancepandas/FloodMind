"""C4：TracingService 的 OTLP 导出接线测试。"""

import logging
from types import SimpleNamespace

import pytest

from floodmind.agent.runtime.services.otlp_exporter import OtlpHttpExporter
from floodmind.agent.runtime.services.tracing_service import TracingService


class _RecordingExporter:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def export(self, spans, session_id, trace_id=""):
        self.calls.append(
            {"spans": list(spans), "session_id": session_id, "trace_id": trace_id}
        )
        if self.error is not None:
            raise self.error
        return len(self.calls[-1]["spans"])


def _flush_once(service: TracingService, session_id: str = "s1"):
    span = service.start_span(session_id, "llm", "llm_step")
    finished = service.end_span(span.span_id, output={"finish_reason": "stop"})
    assert finished is not None
    return service.flush(session_id)


class TestFlushExport:
    def test_flush_exports_completed_spans(self, tmp_path):
        exporter = _RecordingExporter()
        service = TracingService(base_dir=str(tmp_path), otlp_exporter=exporter)
        service.set_trace_context("s1", trace_id="trace-1")

        _flush_once(service)

        assert len(exporter.calls) == 1
        call = exporter.calls[0]
        assert call["session_id"] == "s1"
        assert call["trace_id"] == "trace-1"
        assert len(call["spans"]) == 1
        assert call["spans"][0].duration_ms is not None

    def test_second_flush_does_not_reexport(self, tmp_path):
        exporter = _RecordingExporter()
        service = TracingService(base_dir=str(tmp_path), otlp_exporter=exporter)
        _flush_once(service)
        service.flush("s1")
        assert len(exporter.calls) == 1

    def test_export_failure_does_not_break_local_flush(self, tmp_path, caplog):
        exporter = _RecordingExporter(error=RuntimeError("collector down"))
        service = TracingService(base_dir=str(tmp_path), otlp_exporter=exporter)

        with caplog.at_level(logging.WARNING):
            path = _flush_once(service)

        assert path.exists()
        assert "OTLP export failed" in caplog.text
        # 缓冲已清理，下次 flush 不再重复导出失败批次
        service.flush("s1")
        assert len(exporter.calls) == 1

    def test_no_exporter_only_writes_local(self, tmp_path):
        service = TracingService(base_dir=str(tmp_path))
        path = _flush_once(service)
        assert path.exists()
        assert not service._buffers.get("s1")


class TestFromSettings:
    def test_builds_exporter_and_pricing(self, tmp_path):
        settings_obj = SimpleNamespace(
            model_pricing={
                "m1": {"input_per_1k": 1.0, "output_per_1k": 2.0}
            },
            observability={
                "otlp_endpoint": "http://collector:4318",
                "otlp_headers": {"Authorization": "Bearer token"},
                "otlp_timeout_seconds": 2,
            },
        )
        service = TracingService.from_settings(
            base_dir=str(tmp_path), settings_obj=settings_obj
        )
        assert isinstance(service._otlp_exporter, OtlpHttpExporter)
        assert service._otlp_exporter.endpoint == "http://collector:4318"
        assert service._otlp_exporter.headers["Authorization"] == "Bearer token"
        assert service._otlp_exporter.timeout == 2.0
        assert service._pricing["m1"].input_per_1k == 1.0

    def test_empty_endpoint_disables_export(self, tmp_path):
        settings_obj = SimpleNamespace(model_pricing={}, observability={})
        service = TracingService.from_settings(
            base_dir=str(tmp_path), settings_obj=settings_obj
        )
        assert service._otlp_exporter is None

    def test_invalid_headers_fail_fast(self, tmp_path):
        settings_obj = SimpleNamespace(
            model_pricing={},
            observability={"otlp_endpoint": "http://collector:4318", "otlp_headers": "bad"},
        )
        with pytest.raises(ValueError):
            TracingService.from_settings(base_dir=str(tmp_path), settings_obj=settings_obj)
