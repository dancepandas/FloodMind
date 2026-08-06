"""Tests for context compression."""

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
        """Multi-modal content is handled."""
        c = ContextCompressor()
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ]
        text = c._messages_to_text(messages)
        assert "hello" in text

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

