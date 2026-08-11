"""P4 验收（§25.2 Provider 对应项）。用 Fake chunks 驱动 ResponsePipeline，验证不误判。"""

from floodmind.agent.native.response_pipeline import ResponsePipeline
from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


def _feed(rp, events):
    for e in events:
        rp.accumulate(e)


def test_tool_json_across_arbitrary_chunk_boundaries():
    rp = ResponsePipeline()
    _feed(rp, [
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Read", arguments='{"path":'),
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="", arguments='"/a/'),
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="", arguments='b"}'),
    ])
    calls, invalids = rp.finalize()
    assert len(calls) == 1 and calls[0].arguments == {"path": "/a/b"} and invalids == []


def test_reasoning_and_content_and_tool_in_same_chunk():
    rp = ResponsePipeline()
    _feed(rp, [
        CanonicalPart(event="reasoning_delta", kind="provider_reasoning", text="think"),
        CanonicalPart(event="text_delta", kind="text", text="answer"),
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Bash", arguments="{}"),
        CanonicalPart(event="response_end", kind="text", text="tool_calls"),
    ])
    calls, _ = rp.finalize()
    assert len(calls) == 1 and rp.assistant_snapshot()["content"] == "answer"


def test_parallel_tool_calls_indexed():
    rp = ResponsePipeline()
    _feed(rp, [
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=1, name="Write", arguments='{"x":1}'),
        CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Read", arguments='{"y":2}'),
    ])
    calls, _ = rp.finalize()
    assert [c.name for c in calls] == ["Read", "Write"]  # 按 index 排序


def test_usage_only_final_chunk_not_misjudged():
    rp = ResponsePipeline()
    _feed(rp, [CanonicalPart(event="usage", kind="text", text='{"total_tokens": 10}')])
    assert rp.terminal_reason().code != "completed"
    assert rp.cumulative_usage()["total_tokens"] == 10


def test_refusal_filter_max_tokens_not_completed():
    for reason in ("refusal", "content_filter", "length"):
        rp = ResponsePipeline()
        rp.accumulate(CanonicalPart(event="response_end", kind="text", text=reason))
        assert rp.terminal_reason().code != "completed"


def test_malformed_arguments_never_executed():
    rp = ResponsePipeline()
    _feed(rp, [CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Write", arguments="{oops")])
    calls, invalids = rp.finalize()
    assert calls == [] and len(invalids) == 1  # 只产生 InvalidToolCall，绝不执行
