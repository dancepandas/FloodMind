"""Tests for context compression."""

import json
from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.context_compressor import (
    COMPRESSION_PROMPT,
    SUMMARY_PREFIX,
    CompressionResult,
    ContextCompressor,
)


class TestContextCompressor:
    """Test ContextCompressor logic."""

    def test_should_compress_false_when_few_messages(self):
        """Messages below threshold should not compress."""
        c = ContextCompressor()
        messages = [{"role": "system", "content": "sys"}] * 5
        assert not c.should_compress(messages, 32000)

    def test_should_compress_true_when_large(self):
        """Many messages exceeding threshold should compress."""
        c = ContextCompressor()
        messages = [{"role": "user", "content": "x" * 1000}] * 50
        assert c.should_compress(messages, 10000)

    def test_compress_returns_unchanged_when_not_needed(self):
        """If should_compress is False, return identity."""
        c = ContextCompressor()
        messages = [{"role": "user", "content": "hi"}]
        result = c.compress(messages, 32000)
        assert result.compressed_messages is messages
        assert result.saved_tokens == 0

    def test_compress_structure(self):
        """Compressed result has head + summary + tail structure."""
        c = ContextCompressor(head_keep=2, tail_keep=2)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1" * 500},
            {"role": "assistant", "content": "a1" * 500},
            {"role": "user", "content": "q2" * 500},
            {"role": "assistant", "content": "a2" * 500},
            {"role": "user", "content": "q3" * 500},
            {"role": "assistant", "content": "a3" * 500},
        ]
        result = c.compress(messages, 100)
        assert len(result.compressed_messages) == 5  # 2 head + 1 summary + 2 tail
        assert result.compressed_messages[2]["role"] == "system"
        assert SUMMARY_PREFIX in result.compressed_messages[2]["content"]

    def test_trim_tool_outputs(self):
        """Long tool outputs are trimmed."""
        c = ContextCompressor()
        messages = [{"role": "tool", "content": "x" * 3000}]
        trimmed = c._trim_tool_outputs(messages)
        assert len(trimmed[0]["content"]) < 3000
        assert "已省略" in trimmed[0]["content"]

    def test_trim_short_outputs_unchanged(self):
        """Short tool outputs are not trimmed."""
        c = ContextCompressor()
        messages = [{"role": "tool", "content": "short"}]
        trimmed = c._trim_tool_outputs(messages)
        assert trimmed[0]["content"] == "short"

    def test_generate_summary_with_llm(self):
        """Summary generation delegates to model_client."""
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="## 已完成的任务\n- task1: done")
        c = ContextCompressor(model_client=llm)
        summary = c._generate_summary([{"role": "user", "content": "hello"}])
        assert "task1" in summary
        llm.invoke.assert_called_once()

    def test_generate_summary_fallback_on_llm_failure(self):
        """LLM failure falls back to simple concatenation."""
        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("down")
        c = ContextCompressor(model_client=llm)
        summary = c._generate_summary([{"role": "user", "content": "hello"}])
        assert "用户:" in summary

    def test_generate_summary_without_llm(self):
        """No LLM configured → fallback summary."""
        c = ContextCompressor(model_client=None)
        summary = c._generate_summary([{"role": "user", "content": "hello"}])
        assert "用户:" in summary

    def test_incremental_summary(self):
        """Incremental summary builds on previous."""
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="updated summary")
        c = ContextCompressor(model_client=llm)
        c._last_summary = "previous summary"
        summary = c._incremental_summary([{"role": "user", "content": "new"}], "previous summary")
        assert summary == "updated summary"

    def test_reset_clears_summary(self):
        """reset() clears last summary."""
        c = ContextCompressor()
        c._last_summary = "something"
        c.reset()
        assert c._last_summary is None

    def test_messages_to_text(self):
        """Multi-modal and structured tool data are preserved with identifiers."""
        c = ContextCompressor()
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-123",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{\"key\":\"value\"}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call-123", "name": "lookup", "content": {"ok": True}},
        ]
        text = c._messages_to_text(messages)
        assert "hello" in text
        assert '"id": "call-123"' in text
        assert '"tool_call_id": "call-123"' in text
        assert '"name": "lookup"' in text
        assert '"ok": true' in text

    def test_messages_to_text_bounds_large_structured_values(self):
        c = ContextCompressor()
        text = c._messages_to_text([{
            "role": "assistant",
            "tool_calls": [{"id": "tc", "function": {"name": "big", "arguments": "x" * 5000}}],
        }])
        assert "tc" in text and "big" in text
        assert "chars omitted" in text
        assert len(text) < 2500

    def test_estimate_tokens_includes_complete_message_envelope(self):
        """Tool metadata counts even when textual content is empty."""
        c = ContextCompressor()
        plain = [{"role": "assistant", "content": ""}]
        structured = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-" + "x" * 100,
                "type": "function",
                "function": {"name": "lookup", "arguments": "{\"query\":\"" + "y" * 500 + "\"}"},
            }],
        }]
        assert c._estimate_tokens(structured) > c._estimate_tokens(plain) + 200

    def test_incremental_summary_only_for_strict_continuation(self):
        c = ContextCompressor(head_keep=1, tail_keep=1, trigger_threshold=0)
        c._generate_summary = MagicMock(side_effect=["first", "rebuilt"])
        c._incremental_summary = MagicMock(return_value="continued")
        first = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
            {"role": "assistant", "content": "tail"},
        ]
        c.compress(first, 1)
        continuation = first[:-1] + [
            {"role": "assistant", "content": "d"},
            {"role": "user", "content": "new tail"},
        ]
        c.compress(continuation, 1)
        c._incremental_summary.assert_called_once()
        incremental_messages = c._incremental_summary.call_args.args[0]
        assert incremental_messages == [{"role": "assistant", "content": "d"}]

        rewritten = [dict(m) for m in continuation]
        rewritten[1] = {"role": "assistant", "content": "history was rewritten"}
        result = c.compress(rewritten, 1)
        assert result.summary == "rebuilt"
        assert c._generate_summary.call_count == 2

    def test_reset_clears_summary_coverage(self):
        c = ContextCompressor()
        c._last_summary = "something"
        c._summary_coverage = (1, 2, "digest")
        c.reset()
        assert c._last_summary is None
        assert c._summary_coverage is None

    def test_estimate_tokens(self):
        """Token estimation is positive."""
        c = ContextCompressor()
        tokens = c._estimate_tokens([{"role": "user", "content": "hello world"}])
        assert tokens > 0


# ---------------------------------------------------------------------------
# 工具调用原子组对齐（Bug A：压缩不得拆散 assistant(tool_calls)+tool 组）
# ---------------------------------------------------------------------------

def _asst_tc(*ids):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "t", "arguments": "{}"}}
            for i in ids
        ],
    }


def _tool(tcid, content="r" * 300):
    return {"role": "tool", "tool_call_id": tcid, "content": content}


class TestToolCallAtomicGroup:
    def test_tail_does_not_orphan_tool_results(self):
        """尾部 4 条恰为 tool 结果时，应前移保留其声明的 assistant 消息。

        复现 MiniMax 2013：assistant(tool_calls) 被切进 middle，留下孤儿 tool。"""
        c = ContextCompressor(head_keep=2, tail_keep=4)
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "q"},
            _asst_tc("a", "b", "c", "d"),
            _tool("a"), _tool("b"), _tool("c"), _tool("d"),
            {"role": "assistant", "content": "done"},
        ] + [{"role": "user", "content": "x" * 2000}] * 40  # 撑大以触发压缩
        result = c.compress(messages, 1000)
        out = result.compressed_messages

        # 不变量：每个 tool 消息的 tool_call_id 都能在更早的 assistant.tool_calls 找到
        seen_call_ids = set()
        for m in out:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                seen_call_ids.update(tc["id"] for tc in m["tool_calls"])
            if m.get("role") == "tool":
                assert m["tool_call_id"] in seen_call_ids, (
                    f"孤儿 tool 消息 {m['tool_call_id']}：assistant 声明已被压缩切走"
                )

    def test_head_does_not_split_assistant_from_tools(self):
        """head 切点落在 assistant(tool_calls) 与其 tool 结果之间时，整组并入 middle。"""
        c = ContextCompressor(head_keep=3, tail_keep=2)
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "q"},
            _asst_tc("x"),
            _tool("x"),   # head_keep=3 会把这条留在 head、assistant 在 head、tool 跨界
            {"role": "assistant", "content": "a"},
        ] + [{"role": "user", "content": "y" * 2000}] * 40
        result = c.compress(messages, 1000)
        out = result.compressed_messages

        seen_call_ids = set()
        for m in out:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                seen_call_ids.update(tc["id"] for tc in m["tool_calls"])
            if m.get("role") == "tool":
                assert m["tool_call_id"] in seen_call_ids

    def test_head_keeps_first_user_message(self):
        """head 至少保留到首条 user 消息，不把用户最初需求切进摘要。"""
        c = ContextCompressor(head_keep=2, tail_keep=2)
        messages = [
            {"role": "system", "content": "s1"},
            {"role": "system", "content": "s2"},   # head_keep=2 只含两条 system
            {"role": "user", "content": "我的最初需求"},
            {"role": "assistant", "content": "a"},
        ] + [{"role": "user", "content": "z" * 2000}] * 40
        result = c.compress(messages, 1000)
        head = result.compressed_messages[: result.compressed_messages.index(
            next(m for m in result.compressed_messages if SUMMARY_PREFIX in m.get("content", ""))
        )]
        assert any(m.get("role") == "user" and "最初需求" in m.get("content", "") for m in head), \
            "首条 user 消息被切进了摘要"

    def test_aligned_split_returns_valid_group_boundaries(self):
        """_aligned_split_points 切点均落在原子组边界（组首）。"""
        c = ContextCompressor(head_keep=2, tail_keep=3)
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "q"},
            _asst_tc("a", "b"),
            _tool("a"), _tool("b"),
            {"role": "assistant", "content": "end"},
        ]
        head_end, tail_start = c._aligned_split_points(messages)
        assert 0 <= head_end <= tail_start <= len(messages)
        # tail_start 不应落在 tool 组中间
        if tail_start < len(messages) and messages[tail_start].get("role") == "tool":
            assert messages[tail_start - 1].get("role") != "tool"


# ---------------------------------------------------------------------------
# Journal-backed Compact（P0 回归：窗口交集过滤不得静默丢组）
# ---------------------------------------------------------------------------

class TestCompressJournalWindowCoverage:
    def test_group_straddling_head_boundary_enters_summary_source(self):
        """head 边界落在 assistant(tool_calls) 组首时，AtomicGroups 段因 compaction
        块连锁向前延伸越过头边界；旧的整段包含过滤会把该组排除——组内消息不进
        head、不进摘要源、不进 tail，静默丢失。修复后按窗口交集纳入摘要源。"""
        c = ContextCompressor(head_keep=2, tail_keep=2)
        tool_marker = "TOOLCALL-X-UNIQUE-MARKER"
        middle_marker = "ASSISTANT-MIDDLE-UNIQUE-MARKER"
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "system", "content": "[compact] 旧摘要", "compaction": True},
            _asst_tc("x"),
            _tool("x", content=tool_marker + "r" * 200),
            {"role": "assistant", "content": middle_marker},
            {"role": "user", "content": "tail question"},
            {"role": "assistant", "content": "tail answer"},
        ]
        # head_end=2 恰落在 assistant(tool_calls) 组首；AtomicGroups 段为
        # (0,5)（compaction 连锁），旧过滤下 messages[2:5] 会被整段排除
        assert c._aligned_split_points(messages) == (2, 5)

        result = c.compress_journal(messages, max_context_tokens=100000)

        # 组内消息必须被覆盖：进入压缩摘要（head/tail 均不含它们）
        assert tool_marker in result.summary, "assistant(tool_calls)+tool 组被静默丢弃"
        assert middle_marker in result.summary, "窗口内 assistant 消息被静默丢弃"
        # 输出结构不变：head(2) + 摘要 + tail(2)
        assert len(result.compressed_messages) == 5
        tail_text = json.dumps(result.compressed_messages[3:], ensure_ascii=False)
        assert tool_marker not in tail_text  # 覆盖来自摘要而非 tail

    def test_window_outside_ranges_not_duplicated_into_summary(self):
        """与窗口无交集的段（纯 head/tail 段）不进入摘要源。"""
        c = ContextCompressor(head_keep=2, tail_keep=2)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "system", "content": "[compact] 旧摘要", "compaction": True},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        result = c.compress_journal(messages, max_context_tokens=100000)
        # head 段（system/旧摘要）不出现在摘要里
        assert "旧摘要" not in result.summary


# ---------------------------------------------------------------------------
# fail-closed 渐进收紧（P2：3 轮阈值 2000→1000→500 均有实际压缩效果）
# ---------------------------------------------------------------------------

class TestProgressiveTrim:
    def test_trim_thresholds_progressively_shrink(self):
        """同一长内容依次按 2000→1000→500 阈值裁剪，每轮都严格缩短。"""
        c = ContextCompressor()
        messages = [{"role": "tool", "content": "x" * 5000}]
        lengths = []
        for limit in (2000, 1000, 500):
            messages = c._trim_tool_outputs(messages, max_len=limit)
            assert "已省略" in messages[0]["content"]
            lengths.append(len(messages[0]["content"]))
        assert lengths[0] <= 2000
        assert lengths[1] < lengths[0]
        assert lengths[2] < lengths[1]
        assert lengths[2] <= 500

    def test_trim_result_not_longer_than_threshold(self):
        """裁剪结果（含省略标记）不得长于阈值，避免渐进轮空转。"""
        c = ContextCompressor()
        for limit in (2000, 1000, 500):
            out = c._trim_tool_outputs([{"role": "tool", "content": "y" * 9000}], max_len=limit)
            assert len(out[0]["content"]) <= limit

    def test_compress_journal_progressive_rounds_all_effective(self):
        """compress_journal 的 Offload 收紧真实生效：尾部大工具组被逐轮收紧，
        而非首轮裁到 ~2000 字符后第 2、3 轮空转（旧实现此时会 fail-closed 抛错）。"""
        c = ContextCompressor(head_keep=1, tail_keep=2)
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u" * 200},
            _asst_tc("a"),
            _tool("a", content="t" * 6000),
            _asst_tc("b"),
            _tool("b", content="t" * 6000),
            {"role": "assistant", "content": "wrap"},
            _asst_tc("z"),
            _tool("z", content="t" * 6000),
            {"role": "assistant", "content": "done"},
        ]
        # 尾部保留的工具组（组 z）初始被裁到 ~2000 阈值仍超预算；
        # 渐进收紧（1000→500）后才能通过最终校验
        result = c.compress_journal(messages, max_context_tokens=1200)
        est = ContextCompressor._estimate_tokens(result.compressed_messages)
        assert est <= 1200
        tail_tools = [
            m for m in result.compressed_messages
            if m.get("role") == "tool"
        ]
        assert tail_tools, "尾部工具组不应被丢弃"
        assert max(len(m["content"]) for m in tail_tools) <= 500

