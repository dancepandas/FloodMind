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
