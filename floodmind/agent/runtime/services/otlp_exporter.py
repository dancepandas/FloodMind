"""C4：OTLP/HTTP JSON 导出器。

把本地 ``TraceSpan`` 转成 OpenTelemetry Protocol 的 JSON 载荷，POST 到
``/v1/traces``。只依赖标准库，不引入 OpenTelemetry SDK；属性沿用已有的
``gen_ai.*`` / ``harness.*`` 语义命名。

设计约束：

- 只导出已完成的 span（``duration_ms`` 非空且 ``end_time`` 存在）；
- trace_id / span_id 必须是 OTLP 要求的十六进制；本地 ID 不是十六进制时用
  SHA-256 稳定派生，保证同一条 trace 内父子关系仍然一致；
- 导出失败抛出 ``OtlpExportError``，由调用方决定是否记录告警；模块自身不吞错。
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from floodmind.agent.runtime.contracts.tracing import TraceSpan

_SCOPE_NAME = "floodmind.agent.harness"
_SCOPE_VERSION = "1"


class OtlpExportError(RuntimeError):
    """OTLP 导出失败。"""


def _otlp_id(value: str, length: int) -> str:
    """把任意本地 ID 稳定映射为 OTLP 十六进制 ID。"""
    text = str(value or "")
    if text and all(ch in "0123456789abcdefABCDEF" for ch in text) and len(text) == length:
        return text.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _attr_value(value: Any) -> Dict[str, Any]:
    """把 Python 值编码为 OTLP JSON 的 AnyValue。"""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_attr_value(item) for item in value]}}
    return {"stringValue": str(value)}


def _attributes(span: TraceSpan, session_id: str) -> List[Dict[str, Any]]:
    attributes: Dict[str, Any] = {}
    attributes["session.id"] = session_id
    attributes["harness.span.type"] = span.type
    attributes["harness.status"] = span.status
    if span.duration_ms is not None:
        attributes["harness.duration_ms"] = float(span.duration_ms)

    for key, value in (span.metadata or {}).items():
        if key.startswith(("gen_ai.", "harness.")):
            attributes[key] = value

    input_data = span.input or {}
    if input_data.get("model"):
        attributes.setdefault("gen_ai.request.model", input_data["model"])
    if input_data.get("iteration") is not None:
        attributes.setdefault("harness.iteration", input_data["iteration"])
    if input_data.get("tool_name"):
        attributes.setdefault("gen_ai.tool.name", input_data["tool_name"])

    output_data = span.output or {}
    for key, value in output_data.items():
        if key.startswith(("gen_ai.", "harness.")):
            attributes[key] = value
    if output_data.get("finish_reason"):
        attributes["gen_ai.response.finish_reasons"] = [str(output_data["finish_reason"])]

    return [
        {"key": key, "value": _attr_value(value)}
        for key, value in attributes.items()
    ]


def _unix_nano(value: Any) -> str:
    if value is None:
        return "0"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return str(int(value.timestamp() * 1_000_000_000))


def _span_payload(span: TraceSpan, session_id: str) -> Optional[Dict[str, Any]]:
    if span.duration_ms is None or span.end_time is None:
        return None
    return {
        "traceId": _otlp_id(span.trace_id, 32),
        "spanId": _otlp_id(span.span_id, 16),
        "parentSpanId": _otlp_id(span.parent_id, 16) if span.parent_id else "",
        "name": span.name or span.type,
        "kind": 3 if span.type in ("llm", "tool") else 1,
        "startTimeUnixNano": _unix_nano(span.start_time),
        "endTimeUnixNano": _unix_nano(span.end_time),
        "attributes": _attributes(span, session_id),
        "status": {"code": 2 if span.status == "error" else 1},
    }


def build_otlp_payload(
    spans: Iterable[TraceSpan],
    session_id: str,
    trace_id: str = "",
    service_name: str = "floodmind",
) -> Dict[str, Any]:
    """构造 ResourceSpans JSON 载荷；无已完成 span 时 spans 为空。"""
    encoded = [
        payload
        for span in spans
        if (payload := _span_payload(span, session_id)) is not None
    ]
    resource_attributes: List[Dict[str, Any]] = [
        {"key": "service.name", "value": {"stringValue": service_name}},
        {"key": "telemetry.sdk.name", "value": {"stringValue": _SCOPE_NAME}},
        {"key": "telemetry.sdk.version", "value": {"stringValue": _SCOPE_VERSION}},
    ]
    if trace_id:
        resource_attributes.append(
            {"key": "floodmind.trace_id", "value": {"stringValue": trace_id}}
        )
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attributes},
                "scopeSpans": [
                    {
                        "scope": {"name": _SCOPE_NAME, "version": _SCOPE_VERSION},
                        "spans": encoded,
                    }
                ],
            }
        ]
    }


@dataclass
class OtlpHttpExporter:
    """OTLP/HTTP JSON exporter。endpoint 可为 collector 根地址或完整的 /v1/traces。"""

    endpoint: str
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float = 5.0
    service_name: str = "floodmind"

    def __post_init__(self) -> None:
        if not str(self.endpoint or "").strip():
            raise ValueError("OTLP endpoint 不能为空")
        if self.timeout <= 0:
            raise ValueError("OTLP timeout 必须大于 0")

    @property
    def spans_url(self) -> str:
        endpoint = str(self.endpoint).rstrip("/")
        if endpoint.endswith("/v1/traces"):
            return endpoint
        return f"{endpoint}/v1/traces"

    def export(self, spans: Iterable[TraceSpan], session_id: str, trace_id: str = "") -> int:
        payload = build_otlp_payload(
            spans,
            session_id=session_id,
            trace_id=trace_id,
            service_name=self.service_name,
        )
        span_count = len(payload["resourceSpans"][0]["scopeSpans"][0]["spans"])
        if span_count == 0:
            return 0

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = {
            "Content-Type": "application/json",
            "User-Agent": "floodmind-otlp-exporter/1",
        }
        request_headers.update({str(k): str(v) for k, v in self.headers.items()})
        request = urllib.request.Request(
            self.spans_url,
            data=body,
            headers=request_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = int(getattr(response, "status", 0) or 0)
                if status >= 300:
                    raise OtlpExportError(f"OTLP exporter 返回 HTTP {status}")
        except OtlpExportError:
            raise
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise OtlpExportError(f"OTLP 导出失败: {exc}") from exc
        return span_count


def build_exporter_from_config(config: Optional[Mapping[str, Any]]) -> Optional[OtlpHttpExporter]:
    """从 observability 配置构造 exporter；未配置 endpoint 时返回 None。"""
    if not config:
        return None
    endpoint = str(config.get("otlp_endpoint", "") or "").strip()
    if not endpoint:
        return None
    headers = config.get("otlp_headers") or {}
    if not isinstance(headers, Mapping):
        raise ValueError("observability.otlp_headers 必须是对象")
    timeout = float(config.get("otlp_timeout_seconds", 5.0))
    return OtlpHttpExporter(
        endpoint=endpoint,
        headers=headers,
        timeout=timeout,
    )
