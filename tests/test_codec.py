from floodmind.agent.native.providers.codec import ProviderCodec
from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


class _FakeChunk:
    """模拟一个 tool_calls arguments 跨多个 chunk 的 OpenAI 块。"""
    def __init__(self, delta, finish_reason=None, usage=None, choices=True):
        self.choices = [type("C", (), {"delta": delta, "finish_reason": finish_reason})()] if choices else []


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
