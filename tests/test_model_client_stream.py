"""ModelClient 流式协议健全性：非 SSE 端点给出可行动诊断。"""
import types

from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.transport import RawResponse


class _FakeCodecPipeline:
    name = "fake"

    def prepare_messages(self, messages):
        return messages

    def prepare_request(self, params, **_kwargs):
        return params

    def new_stream_state(self):
        return {}

    def decode_chunk(self, _chunk):
        return []

    def extract_usage(self, _chunk):
        return None

    def build_assistant_message(self, accumulator, _completed):
        return dict(accumulator)


class _StaticTransport:
    def __init__(self, raw):
        self._raw = raw

    def send(self, _request):
        return self._raw


def _client(raw):
    client = ModelClient.__new__(ModelClient)
    client.api_key = "test-key"
    client.base_url = "http://127.0.0.1:3000"
    client.model_name = "test-model"
    client.temperature = 0.1
    client.max_tokens = 16
    client.timeout = 5
    client.enable_thinking = False
    client.provider = ""
    client.pipeline = _FakeCodecPipeline()
    client._transport = _StaticTransport(raw)
    return client


def _response_with(content_type: str):
    return types.SimpleNamespace(headers={"content-type": content_type})


def test_stream_chat_rejects_html_response_with_actionable_error():
    client = _client(RawResponse(
        iter(()), response=_response_with("text/html; charset=utf-8"),
    ))

    events = list(client.stream_chat([{"role": "user", "content": "hi"}]))

    assert len(events) == 1
    assert events[0].type == "error"
    assert "text/html" in events[0].content
    assert "/v1" in events[0].content
    assert "http://127.0.0.1:3000" in events[0].content


def test_stream_chat_rejects_json_response():
    client = _client(RawResponse(
        iter(()), response=_response_with("application/json"),
    ))

    events = list(client.stream_chat([{"role": "user", "content": "hi"}]))

    assert len(events) == 1
    assert events[0].type == "error"
    assert "application/json" in events[0].content


def test_stream_chat_accepts_sse_content_type():
    client = _client(RawResponse(
        iter(()), response=_response_with("text/event-stream; charset=utf-8"),
    ))

    events = list(client.stream_chat([{"role": "user", "content": "hi"}]))

    assert events, "SSE 响应应正常走完事件序列"
    assert events[-1].type == "done"
    assert all(event.type != "error" for event in events)


def test_raw_response_without_http_response_has_empty_content_type():
    raw = RawResponse(iter(()))

    assert raw.content_type() == ""
