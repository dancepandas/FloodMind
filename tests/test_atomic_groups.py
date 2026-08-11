from floodmind.agent.native.atomic_groups import AtomicGroups


def _tool_result(tool_call_id):
    return {"role": "tool", "tool_call_id": tool_call_id, "content": "ok"}


def test_tool_pair_never_split():
    msgs = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "Read", "arguments": {}}]},
        _tool_result("c1"),
    ]
    groups = AtomicGroups().build(msgs)
    ranges = AtomicGroups().aligned_ranges(msgs)
    # 任何 range 不得只含 assistant tool_call 而不含其 tool result
    for start, end in ranges:
        sliced = msgs[start:end]
        assert not (any(m.get("tool_calls") for m in sliced) and not any(m.get("role") == "tool" for m in sliced))


def test_parallel_tool_group_kept():
    msgs = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "A", "arguments": {}}, {"id": "c2", "name": "B", "arguments": {}}]},
        _tool_result("c1"), _tool_result("c2"),
    ]
    ranges = AtomicGroups().aligned_ranges(msgs)
    for start, end in ranges:
        sliced = msgs[start:end]
        tc = [m for m in sliced if m.get("tool_calls")]
        if tc:
            assert len(tc[0]["tool_calls"]) == 2  # 并行组整体保留
