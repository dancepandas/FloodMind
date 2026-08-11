from floodmind.agent.native.transport import OpenAIChatTransport, TransportRetryAdvice


def test_classify_error_maps_retryable():
    t = OpenAIChatTransport(api_key="k", base_url="http://localhost:1", timeout=1)
    advice = t.classify_error(TimeoutError("timed out"))
    assert advice.retry_suggested is True and advice.response_started is False


def test_classify_error_refusal_not_retryable():
    t = OpenAIChatTransport(api_key="k", base_url="http://localhost:1", timeout=1)
    advice = t.classify_error(RuntimeError("content_filter"))
    assert advice.retry_suggested is False
