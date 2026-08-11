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
    # 1) build() 必须产出单个 parallel_tool 组，覆盖 assistant + 两个 tool result 全部三条
    groups = AtomicGroups().build(msgs)
    parallel = [g for g in groups if g.kind == "parallel_tool"]
    assert len(parallel) == 1
    assert parallel[0].indices == [1, 2, 3]  # assistant + tool c1 + tool c2
    assert parallel[0].required_together is True

    # 2) aligned_ranges() 不得把并行组拆散：任何包含 assistant 的 range 必须同时含两个 tool result
    ranges = AtomicGroups().aligned_ranges(msgs)
    for start, end in ranges:
        sliced = msgs[start:end]
        if any(m.get("tool_calls") for m in sliced):
            tool_ids = [m.get("tool_call_id") for m in sliced if m.get("role") == "tool"]
            assert tool_ids == ["c1", "c2"]  # 两个 result 必须整体保留在同一 range
    # 等价断言：不存在落在索引 1..3 之间的切点（assistant 与任一 result 分离）
    boundaries = {start for start, _ in ranges} | {end for _, end in ranges}
    for b in boundaries:
        assert not (1 < b < 3), f"range 边界 {b} 落在并行工具组内部，拆散了 assistant 与其 tool results"
