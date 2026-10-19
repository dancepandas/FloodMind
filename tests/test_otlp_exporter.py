"""C4：OTLP/HTTP JSON 导出器测试。"""

import hashlib
import json
import urllib.error

import pytest

from floodmind.agent.runtime.contracts.tracing import TraceSpan
from floodmind.agent.runtime.services.otlp_exporter import (
    OtlpExportError,
    OtlpHttpExporter,
    build_exporter_from_config,
    build_otlp_payload,
)


def _span(
    span_id: str = "span-1",
    trace_id: str = "trace-1",
    kind: str = "llm",
    name: str = "llm_step",
    status: str = "ok",
    parent_id=None,
) -> TraceSpan:
    span = TraceSpan(
        span_id=span_id,
        trace_id=trace_id,
        type=kind,
        name=name,
        parent_id=parent_id,
        input={"model": "deepseek-v4-flash", "iteration": 2},
        metadata={
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "deepseek-v4-flash",
            "harness.run_id": "run-1",
            "harness.step_key": "step-a",
        },
    )
    span.finalize(
        output={
            "finish_reason": "stop",
            "gen_ai.usage.input_tokens": 11,
            "gen_ai.usage.output_tokens": 4,
        },
        status=status,
    )
    return span


class _FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestPayload:
    def test_completed_spans_are_exported(self):
        payload = build_otlp_payload([_span()], session_id="s1", trace_id="trace-1")
        spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert len(spans) == 1
        span = spans[0]
        assert span["traceId"] == hashlib.sha256(b"trace-1").hexdigest()[:32]
        assert span["spanId"] == hashlib.sha256(b"span-1").hexdigest()[:16]
        assert span["kind"] == 3
        assert span["status"]["code"] == 1
        attrs = {item["key"]: item["value"] for item in span["attributes"]}
        assert attrs["gen_ai.operation.name"]["stringValue"] == "chat"
        assert attrs["gen_ai.usage.input_tokens"]["intValue"] == "11"
        assert attrs["harness.run_id"]["stringValue"] == "run-1"
        assert attrs["session.id"]["stringValue"] == "s1"

    def test_unfinished_spans_are_skipped(self):
        payload = build_otlp_payload([TraceSpan(type="llm", name="running")], session_id="s1")
        assert payload["resourceSpans"][0]["scopeSpans"][0]["spans"] == []

    def test_error_status_and_parent(self):
        parent = _span(span_id="parent", status="error")
        child = _span(span_id="child", kind="tool", name="Read", parent_id="parent")
        payload = build_otlp_payload([parent, child], session_id="s1")
        spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert spans[0]["status"]["code"] == 2
        assert spans[1]["parentSpanId"] == spans[0]["spanId"]

    def test_hex_ids_are_normalized(self):
        span = _span(span_id="a" * 16, trace_id="b" * 32)
        payload = build_otlp_payload([span], session_id="s1")
        encoded = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        assert encoded["traceId"] == "b" * 32
        assert encoded["spanId"] == "a" * 16


class TestHttpExporter:
    def test_posts_to_v1_traces(self, monkeypatch):
        captured = {}

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResponse(200)

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        exporter = OtlpHttpExporter("http://collector:4318", timeout=3.0)
        count = exporter.export([_span()], session_id="s1", trace_id="trace-1")

        assert count == 1
        assert captured["url"] == "http://collector:4318/v1/traces"
        assert captured["headers"]["Content-type"] == "application/json"
        assert captured["timeout"] == 3.0
        assert captured["body"]["resourceSpans"][0]["scopeSpans"][0]["spans"]

    def test_full_endpoint_is_not_duplicated(self):
        exporter = OtlpHttpExporter("http://collector:4318/v1/traces")
        assert exporter.spans_url == "http://collector:4318/v1/traces"

    def test_http_error_raises(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: _FakeResponse(500))
        exporter = OtlpHttpExporter("http://collector:4318")
        with pytest.raises(OtlpExportError, match="HTTP 500"):
            exporter.export([_span()], session_id="s1")

    def test_network_error_raises(self, monkeypatch):
        def _fail(request, timeout):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", _fail)
        exporter = OtlpHttpExporter("http://collector:4318")
        with pytest.raises(OtlpExportError, match="connection refused"):
            exporter.export([_span()], session_id="s1")

    def test_no_completed_spans_makes_no_request(self, monkeypatch):
        def _unexpected(request, timeout):
            raise AssertionError("不应该发起请求")

        monkeypatch.setattr("urllib.request.urlopen", _unexpected)
        exporter = OtlpHttpExporter("http://collector:4318")
        assert exporter.export([TraceSpan(type="llm")], session_id="s1") == 0

    def test_empty_endpoint_rejected(self):
        with pytest.raises(ValueError):
            OtlpHttpExporter("")
        with pytest.raises(ValueError):
            OtlpHttpExporter("http://collector:4318", timeout=0)


class TestConfig:
    def test_missing_endpoint_returns_none(self):
        assert build_exporter_from_config({}) is None
        assert build_exporter_from_config({"otlp_endpoint": ""}) is None

    def test_config_builds_exporter(self):
        exporter = build_exporter_from_config({
            "otlp_endpoint": "http://collector:4318",
            "otlp_headers": {"Authorization": "Bearer token"},
            "otlp_timeout_seconds": 2,
        })
        assert exporter is not None
        assert exporter.endpoint == "http://collector:4318"
        assert exporter.headers["Authorization"] == "Bearer token"
        assert exporter.timeout == 2.0

    def test_invalid_headers_rejected(self):
        with pytest.raises(ValueError):
            build_exporter_from_config({
                "otlp_endpoint": "http://collector:4318",
                "otlp_headers": "bad",
            })
