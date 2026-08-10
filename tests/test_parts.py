from floodmind.agent.runtime.contracts.parts import (
    PART_EVENT_TYPES, PartDeltaEvent, PartEndEvent, PartStartEvent,
    PartType, ResponseEndEvent, UsageRecorded,
)


def test_part_event_names():
    for name in ("response_start", "part_start", "text_delta", "reasoning_delta",
                 "tool_call_delta", "part_end", "usage", "response_end", "error"):
        assert name in PART_EVENT_TYPES


def test_part_start_carries_part_type():
    e = PartStartEvent(part_id="p1", part_type=PartType.tool_call)
    assert e.type == "part_start"
    assert e.part_type == PartType.tool_call


def test_delta_and_end():
    assert PartDeltaEvent(part_id="p1", text="hi").text == "hi"
    assert PartEndEvent(part_id="p1").type == "part_end"


def test_usage_and_response_end():
    u = UsageRecorded(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    assert u.total_tokens == 3
    r = ResponseEndEvent(terminal_reason_code="max_tokens", terminal_reason_raw="length")
    assert r.terminal_reason_raw == "length"
