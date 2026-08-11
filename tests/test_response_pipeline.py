"""ResponsePipeline（目标 §7.4）—— Provider-neutral 状态化组装。

工具 delta 跨 chunk 累积；malformed JSON → InvalidToolCall（保留 raw arguments）；
raw 参数保留；assistant 完整 replay snapshot；最终累积 usage；终态判定。
"""

from floodmind.agent.native.response_pipeline import ResponsePipeline
from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


def test_tool_call_delta_accumulation_across_chunks():
    rp = ResponsePipeline()
    rp.accumulate(CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Read", arguments='{"path"'))
    rp.accumulate(CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="", arguments=': "/a"}'))
    calls, invalids = rp.finalize()
    assert len(calls) == 1 and calls[0].name == "Read" and calls[0].arguments == {"path": "/a"}


def test_malformed_json_is_invalid_tool_call_not_executable():
    rp = ResponsePipeline()
    rp.accumulate(CanonicalPart(event="tool_call_delta", kind="tool_call", index=0, name="Write", arguments="{not-json"))
    calls, invalids = rp.finalize()
    assert calls == [] and len(invalids) == 1 and invalids[0].raw_arguments == "{not-json"


def test_terminal_reason_refusal_not_completed():
    rp = ResponsePipeline()
    rp.accumulate(CanonicalPart(event="response_end", kind="text", text="content_filter"))
    assert rp.terminal_reason().code != "completed"
