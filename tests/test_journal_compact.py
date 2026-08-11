"""Journal-backed Compact tests (P5 Task 3, §9.5/§9.6)."""

import hashlib

import pytest

from floodmind.agent.native.context_compressor import (
    CompactionOverBudgetError,
    ContextCompressor,
)
from floodmind.agent.runtime.contracts.canonical_events import canonical_json
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def test_compact_emits_summary_event_and_keeps_user_message(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "user one", "turn_index": 0})
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "assistant one", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    auth.emit("thread.message.sent", {"content": "long user message that must never be truncated", "turn_index": 1})
    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "user one"},
        {"role": "assistant", "content": "assistant one"},
        {"role": "user", "content": "long user message that must never be truncated"},
    ]
    cc = ContextCompressor(head_keep=1, tail_keep=1)
    result = cc.compress_journal(messages, auth, capabilities=None,
                                 budget=None, max_context_tokens=1200)
    # 当前用户请求不静默截断
    assert any("long user message that must never be truncated" in m.get("content", "")
               for m in result.compressed_messages if m.get("role") == "user")
    # Summary Event 落 journal（journal-backed source）
    types = [e.event_type for e in auth.read_after(0)]
    assert "context.compaction.completed" in types
    # 原始 journal 不变（只新增 summary 事件，不修改原事件）
    first = auth.read_after(0)[0]
    assert first.event_type == "thread.message.sent" and first.payload["content"] == "user one"


def test_compact_summary_is_journal_backed(tmp_path):
    """§9.5/§9.6：summary 事件可追溯 journal。

    source_event_ids 非空且引用实际 journal 事件；covered_sequence_* 落在 journal
    sequence 空间（非消息索引）；source_sha256 可从 replay 复现；system prompt 与
    当前用户请求绝不进摘要源（可压缩段排除保留头）。
    """
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    # 7 个轮次事件（seq 1..7），最后一个是当前用户请求
    auth.emit("thread.message.sent", {"content": "user message one", "turn_index": 0})
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "assistant one", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    auth.emit("thread.message.sent", {"content": "user message two", "turn_index": 1})
    auth.emit("model.attempt.completed", {"attempt_id": "a2", "terminal_reason": "completed",
        "content": "assistant two", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    auth.emit("thread.message.sent", {"content": "user message three", "turn_index": 2})
    auth.emit("model.attempt.completed", {"attempt_id": "a3", "terminal_reason": "completed",
        "content": "assistant three", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    auth.emit("thread.message.sent", {"content": "current user request", "turn_index": 3})

    messages = [
        {"role": "system", "content": "SYSTEM"},            # 0 保留头
        {"role": "user", "content": "user message one"},     # 1 保留头
        {"role": "assistant", "content": "assistant one"},   # 2 可压缩段
        {"role": "user", "content": "user message two"},     # 3 可压缩段
        {"role": "assistant", "content": "assistant two"},   # 4 保留尾
        {"role": "user", "content": "user message three"},   # 5 保留尾
        {"role": "assistant", "content": "assistant three"}, # 6 保留尾
        {"role": "user", "content": "current user request"}, # 7 保留尾
    ]
    cc = ContextCompressor(head_keep=2, tail_keep=4)
    result = cc.compress_journal(messages, auth, max_context_tokens=1200)

    completed = [e for e in auth.read_after(0) if e.event_type == "context.compaction.completed"]
    assert len(completed) == 1
    payload = completed[0].payload

    # 1) source_event_ids 非空，且引用实际 journal 事件
    all_ids = {e.event_id for e in auth.read_after(0)}
    assert payload["source_event_ids"], "source_event_ids 必须非空"
    assert set(payload["source_event_ids"]) <= all_ids

    # 2) covered_sequence_* 落在 journal sequence 空间，且非平凡
    assert payload["covered_sequence_start"] >= 0
    assert payload["covered_sequence_start"] < payload["covered_sequence_end"]

    # 3) source_sha256 可从 journal 复现：被覆盖区间 = 第 2、3 个 turn 事件（seq 2..3）
    turn_events = [e for e in auth.read_after(0) if e.event_type in {
        "thread.message.sent", "model.attempt.completed",
        "tool.execution.completed", "tool.execution.failed"}]
    assert len(turn_events) == 7
    covered = turn_events[1:3]  # assistant one + user message two
    assert covered[0].sequence == 2 and covered[-1].sequence == 3
    assert payload["covered_sequence_start"] == covered[0].sequence
    assert payload["covered_sequence_end"] == covered[-1].sequence
    assert payload["source_event_ids"] == [e.event_id for e in covered]
    expected_hash = hashlib.sha256(
        canonical_json([e.payload for e in covered]).encode("utf-8")).hexdigest()
    assert payload["source_sha256"] == expected_hash

    # 4) system prompt + 当前用户请求不进摘要源（可压缩段排除保留头）
    covered_contents = [str(e.payload.get("content", "")) for e in covered]
    assert "SYSTEM" not in covered_contents
    assert all("current user request" not in c for c in covered_contents)
    assert "SYSTEM" not in result.summary
    assert "current user request" not in result.summary


def test_repeated_compaction_retains_prior_compact_block(tmp_path):
    """F1：二次压缩必须保留第一次压缩产生的 [compact] 摘要块（绝不静默丢失）。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    for i, (u, a) in enumerate([
        ("user one", "assistant one"),
        ("user two", "assistant two"),
    ]):
        auth.emit("thread.message.sent", {"content": u, "turn_index": i})
        auth.emit("model.attempt.completed", {"attempt_id": f"a{i}", "terminal_reason": "completed",
            "content": a, "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    auth.emit("thread.message.sent", {"content": "user three", "turn_index": 2})
    auth.emit("model.attempt.completed", {"attempt_id": "a2", "terminal_reason": "completed",
        "content": "assistant three", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    auth.emit("thread.message.sent", {"content": "user current", "turn_index": 3})

    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "user one"},
        {"role": "assistant", "content": "assistant one"},
        {"role": "user", "content": "user two"},
        {"role": "assistant", "content": "assistant two"},
        {"role": "user", "content": "user three"},
        {"role": "assistant", "content": "assistant three"},
        {"role": "user", "content": "user current"},
    ]
    cc = ContextCompressor(head_keep=1, tail_keep=2)
    first = cc.compress_journal(messages, auth, max_context_tokens=1200)
    first_compacts = [m for m in first.compressed_messages if m.get("compaction")]
    assert len(first_compacts) == 1
    prior_summary = first_compacts[0]["content"]

    # 第二次压缩：第一次输出 + 两个新轮次
    auth.emit("model.attempt.completed", {"attempt_id": "a3", "terminal_reason": "completed",
        "content": "assistant four", "reasoning": "", "tool_calls": [], "is_final": True, "usage": {}})
    auth.emit("thread.message.sent", {"content": "user five", "turn_index": 4})
    second_input = first.compressed_messages + [
        {"role": "assistant", "content": "assistant four"},
        {"role": "user", "content": "user five"},
    ]
    second = cc.compress_journal(second_input, auth, max_context_tokens=1200)
    second_compacts = [m for m in second.compressed_messages if m.get("compaction")]
    # 第一次的 [compact] 摘要块必须仍在第二次输出中
    assert any(prior_summary in m["content"] for m in second_compacts), (
        "二次压缩丢失了首次压缩的 [compact] 摘要块"
    )


def test_no_wire_fallback_when_no_covered_events(tmp_path):
    """F5：覆盖区无 journal-backed 事件时不 emit compaction.completed（不退回 wire 哈希）。"""
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "user current", "turn_index": 0})
    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "system", "content": "[compact] prior history", "compaction": True},
        {"role": "user", "content": "user current"},
    ]
    cc = ContextCompressor(head_keep=1, tail_keep=1)
    result = cc.compress_journal(messages, auth, max_context_tokens=1200)
    types = [e.event_type for e in auth.read_after(0)]
    assert "context.compaction.completed" not in types
    # 既有 compaction 块仍保留（F1 不变量），且用户消息不被截断
    assert any(m.get("compaction") and "prior history" in str(m.get("content", ""))
               for m in result.compressed_messages)


def test_compress_over_budget_fails_closed(tmp_path):
    """F4：仍超限且无工具输出可裁剪时 fail-closed 抛 CompactionOverBudgetError。

    绝不静默返回超预算投影，也绝不静默截断当前用户请求。
    """
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    current = "current user request " + "x" * 50000
    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": current},
    ]
    cc = ContextCompressor(head_keep=1, tail_keep=1)
    with pytest.raises(CompactionOverBudgetError):
        cc.compress_journal(messages, auth, max_context_tokens=100)
    # fail-closed：不产生 compaction.completed（未发生有效压缩）
    types = [e.event_type for e in auth.read_after(0)]
    assert "context.compaction.completed" not in types