from floodmind.agent.native import transport as transport_module
from floodmind.agent.native.transport import OpenAIChatTransport, TransportRetryAdvice, should_trust_env


def test_should_trust_env_bypasses_private_and_loopback():
    assert should_trust_env("http://10.0.0.5:3000/v1") is False
    assert should_trust_env("http://10.0.0.5:8000/v1") is False
    assert should_trust_env("http://172.16.1.1/v1") is False
    assert should_trust_env("http://127.0.0.1:11434/v1") is False
    assert should_trust_env("http://localhost:11434/v1") is False
    assert should_trust_env("http://[::1]:8000/v1") is False


def test_should_trust_env_keeps_public_hosts():
    assert should_trust_env("https://api.openai.com/v1") is True
    assert should_trust_env("https://dashscope.aliyuncs.com/compatible-mode/v1") is True


def test_transport_passes_trust_env_to_httpx(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["httpx"] = kwargs

    class FakeOpenAI:
        def __init__(self, **kwargs):
            seen["openai"] = kwargs

    monkeypatch.setattr(transport_module.httpx, "Client", FakeClient)
    monkeypatch.setattr(transport_module.openai, "OpenAI", FakeOpenAI)

    OpenAIChatTransport(api_key="k", base_url="http://10.0.0.5:3000/v1", timeout=1)
    assert seen["httpx"]["trust_env"] is False
    assert isinstance(seen["openai"]["http_client"], FakeClient)

    OpenAIChatTransport(
        api_key="k", base_url="http://10.0.0.5:3000/v1", timeout=1, trust_env=True
    )
    assert seen["httpx"]["trust_env"] is True


def test_classify_error_maps_retryable():
    t = OpenAIChatTransport(api_key="k", base_url="http://localhost:1", timeout=1)
    advice = t.classify_error(TimeoutError("timed out"))
    assert advice.retry_suggested is True and advice.response_started is False


def test_classify_error_refusal_not_retryable():
    t = OpenAIChatTransport(api_key="k", base_url="http://localhost:1", timeout=1)
    advice = t.classify_error(RuntimeError("content_filter"))
    assert advice.retry_suggested is False
