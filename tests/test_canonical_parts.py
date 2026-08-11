"""CanonicalPart 模型形状（目标 §7.5）：纯数据层。"""

from floodmind.agent.runtime.contracts.canonical_parts import (
    PART_EVENT_TYPES,
    PART_TYPES,
    CanonicalPart,
)


def test_part_event_types_complete():
    assert PART_EVENT_TYPES == [
        "response_start", "part_start", "text_delta", "reasoning_delta",
        "tool_call_delta", "part_end", "usage", "response_end", "error",
    ]
    assert len(PART_EVENT_TYPES) == 9


def test_part_types_complete():
    assert PART_TYPES == [
        "text", "reasoning_summary", "provider_reasoning", "tool_call",
        "refusal", "compaction", "provider_extension",
    ]
    assert len(PART_TYPES) == 7


def test_canonical_part_defaults():
    p = CanonicalPart(event="text_delta")
    assert p.kind == ""
    assert p.index == 0
    assert p.text == ""
    assert p.name == ""
    assert p.arguments == ""
    assert p.arguments_sha256 == ""
    assert p.raw == {}


def test_canonical_part_constructs_with_fields():
    p = CanonicalPart(
        event="tool_call_delta",
        kind="tool_call",
        index=2,
        name="Read",
        arguments='{"path": "/a"}',
        arguments_sha256="abc",
        raw={"id": "x"},
    )
    assert p.event == "tool_call_delta"
    assert p.kind == "tool_call"
    assert p.index == 2
    assert p.name == "Read"
    assert p.arguments == '{"path": "/a"}'
    assert p.arguments_sha256 == "abc"
    assert p.raw == {"id": "x"}


def test_raw_preserves_provider_native_block():
    """§7.5：provider 原生块保留在 raw，供 provider_extension / replay。"""
    p = CanonicalPart(event="text_delta", kind="text", text="hi", raw={"choices": [1]})
    assert p.raw["choices"] == [1]
