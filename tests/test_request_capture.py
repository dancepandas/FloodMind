"""Opt-in diagnostics preserve each outgoing request without recording HTTP credentials."""

import json

import pytest

from tools.request_capture import RequestCaptureTransport


class _Transport:
    def send(self, request):
        return request

    def classify_error(self, exc):
        return str(exc)


def test_capture_preserves_separate_contracts_history_and_each_send(tmp_path):
    capture = RequestCaptureTransport(_Transport(), tmp_path / "requests.jsonl")
    request = {
        "model": "offline-model", "stream": True,
        "messages": [
            {"role": "system", "content": "host\nskill instructions\nfull catalog"},
            {"role": "user", "content": "Calculate a total"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "Total", "description": "Full description " * 200,
            "parameters": {"type": "object", "properties": {
                "values": {"type": "array", "items": {"type": "number"},
                           "description": "Values in metres"}}, "required": ["values"]},
        }}],
        "tool_choice": "auto", "extra_body": {"enable_thinking": False},
        "extra_headers": {"Authorization": "Bearer do-not-record"},
    }
    assert capture.send(request) is request
    request["messages"].extend([
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-1", "type": "function", "function": {
                "name": "Total", "arguments": '{"values":[12,30]}'}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": '{"total":42}'},
    ])
    capture.send(request)
    rows = [json.loads(line) for line in capture.path.read_text(encoding="utf-8").splitlines()]
    assert [r["sequence"] for r in rows] == [1, 2]
    assert len(rows[0]["request"]["messages"]) == 2
    assert rows[1]["request"]["messages"][-1] == {
        "role": "tool", "tool_call_id": "call-1", "content": '{"total":42}'}
    assert rows[0]["request"]["tools"] == request["tools"]
    assert rows[0]["request"]["extra_body"] == {"enable_thinking": False}
    assert "do-not-record" not in capture.path.read_text(encoding="utf-8")
    assert capture.classify_error(ValueError("test")) == "test"


def test_real_agent_requests_preserve_catalog_tool_results_and_later_history(tmp_path):
    from types import SimpleNamespace as NS

    from floodmind.agent.api import Agent
    from floodmind.agent.native.model_client import ModelClient
    from floodmind.agent.native.tool_loading import ToolLoadingConfig
    from floodmind.agent.native.transport import RawResponse
    from floodmind.agent.runtime.contracts.tools import ToolSpec
    from floodmind.agent.runtime.contracts.workspace import Workspace

    class OfflineTransport(_Transport):
        def __init__(self):
            self.count = 0

        def send(self, request):
            self.count += 1
            if self.count == 1:
                delta = NS(content=None, tool_calls=[NS(
                    index=0, id="call-total", type="function",
                    function=NS(name="Total", arguments='{"values":[12,30]}'),
                )])
            else:
                delta = NS(content="Verified total is 42.", tool_calls=None)
            from openai.types.chat import ChatCompletionChunk
            chunk = ChatCompletionChunk(
                id="offline", created=0, model="mock-model", object="chat.completion.chunk",
                choices=[{"index": 0, "delta": {
                    "content": delta.content,
                    "tool_calls": [{"index": 0, "id": "call-total", "type": "function",
                                    "function": {"name": "Total", "arguments": '{"values":[12,30]}'}}]
                                  if self.count == 1 else None,
                }, "finish_reason": "tool_calls" if self.count == 1 else "stop"}],
            )
            return RawResponse(iter([chunk]))

    client = ModelClient(api_key="offline-only", base_url="https://mock.api/v1",
                         model_name="mock-model", context_window=65536)
    capture = RequestCaptureTransport(OfflineTransport(), tmp_path / "requests.jsonl")
    client._transport = capture
    tool = ToolSpec(
        name="Total", description="Sum values in metres without rounding.",
        parameters={"type": "object", "properties": {
            "values": {"type": "array", "items": {"type": "number"},
                       "description": "Exact measurements in metres"}},
            "required": ["values"]},
        func=lambda values: json.dumps({"total": sum(values)}),
        is_readonly=True,
    )
    agent = Agent(llm=client, bare=True, tools=[tool],
                  system_prompt="HOST-CONTRACT: verify the returned total.",
                  tool_loading=ToolLoadingConfig(mode="catalog"), max_iterations=4,
                  workspace=Workspace.from_folder(tmp_path / "workspace").ensure())
    agent.run("Sum 12 and 30 metres")
    agent.run("What was the previous total?")
    rows = [json.loads(line)["request"] for line in capture.path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    for request in rows:
        prompt = json.dumps(request["messages"], ensure_ascii=False)
        assert "HOST-CONTRACT" in prompt
        assert tool.description in prompt
        assert "Exact measurements in metres" in prompt
        contract = next(t["function"] for t in request["tools"] if t["function"]["name"] == "Total")
        assert contract["parameters"] == tool.parameters
    results = [m for m in rows[1]["messages"] if m["role"] == "tool"]
    assert results[0]["tool_call_id"] == "call-total"
    assert json.loads(results[0]["content"])["total"] == 42
    history = json.dumps(rows[2]["messages"])
    assert "Sum 12 and 30 metres" in history
    assert "Verified total is 42." in history
    assert "What was the previous total?" in history


def test_capture_labels_sdk_arguments_distinct_from_merged_http_body(tmp_path):
    import httpx
    from openai import OpenAI

    received = []

    def handle(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "offline", "object": "chat.completion", "created": 0,
            "model": "effective-model", "choices": [],
        })

    client = OpenAI(api_key="offline-only", base_url="https://mock.api/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(handle)))

    class SDKTransport(_Transport):
        def send(self, request):
            return client.chat.completions.create(**request)

    capture = RequestCaptureTransport(SDKTransport(), tmp_path / "requests.jsonl")
    try:
        capture.send({
            "model": "original-model", "messages": [{"role": "user", "content": "original"}],
            "extra_body": {"model": "effective-model", "enable_thinking": False},
        })
    finally:
        client.close()
    row = json.loads(capture.path.read_text(encoding="utf-8"))
    assert received[0]["model"] == "effective-model"
    assert received[0]["enable_thinking"] is False
    assert "extra_body" not in received[0]
    assert row["representation"] == "sdk_arguments"
    assert row["request"]["model"] == "original-model"
    assert row["request"]["extra_body"]["model"] == received[0]["model"]
    assert row["request"]["messages"] == received[0]["messages"]


def test_capture_refuses_to_overwrite_existing_diagnostics(tmp_path):
    path = tmp_path / "requests.jsonl"
    path.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        RequestCaptureTransport(_Transport(), path)
    assert path.read_text(encoding="utf-8") == "existing"
