import json

from floodmind.agent.native.providers.codec import ProviderCodec
from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


class _FakeChunk:
    """模拟一个 tool_calls arguments 跨多个 chunk 的 OpenAI 块。"""
    def __init__(self, delta, finish_reason=None, usage=None, choices=True):
        self.choices = [type("C", (), {"delta": delta, "finish_reason": finish_reason})()] if choices else []
        self.usage = usage


def test_codec_decodes_chunk_to_canonical_parts():
    codec = ProviderCodec(name="openai")
    chunk = _FakeChunk(delta=type("D", (), {"content": "hi", "reasoning_content": None, "tool_calls": None})())
    parts = list(codec.decode_chunk(chunk))
    assert any(p.event == "text_delta" and p.text == "hi" for p in parts)


def test_codec_preserves_raw_for_replay():
    codec = ProviderCodec(name="openai")
    chunk = _FakeChunk(delta=type("D", (), {"content": "x", "reasoning_content": None, "tool_calls": None})())
    parts = list(codec.decode_chunk(chunk))
    assert parts[0].raw != {}  # provider 原生块保留（§7.5 replay）


def test_codec_usage_only_tail_chunk_is_usage_not_error():
    """§25.2 usage-only final chunk：choices=[] + 顶层 usage → usage part，而非 error。

    标准 OpenAI usage 位置是末帧空 choices chunk 的顶层 usage；此帧不得被
    no-choices 守卫吞成 error（Task 4 ResponsePipeline 靠 json.loads(part.text) 读 usage）。
    """
    codec = ProviderCodec(name="openai")
    chunk = _FakeChunk(
        delta=type("D", (), {"content": None, "reasoning_content": None, "tool_calls": None})(),
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        choices=False,
    )
    parts = list(codec.decode_chunk(chunk))
    assert parts and parts[0].event == "usage"
    assert not any(p.event == "error" for p in parts)
    assert json.loads(parts[0].text)["total_tokens"] == 15
    assert parts[0].raw != {}
