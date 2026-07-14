"""Tests for model router, fallback chains, and token usage tracking."""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.model_router import (
    SMART_TIMEOUTS,
    ModelCallConfig,
    ModelRouter,
    TokenUsageRecord,
    TokenUsageTracker,
    _build_fallback_chain,
)


class TestFallbackChain:
    """Test model fallback chain resolution."""

    def test_get_fallback_exists(self, monkeypatch):
        """Known model returns its first fallback candidate."""
        monkeypatch.setattr(
            "floodmind.agent.native.model_router._build_fallback_chain",
            lambda: {"deepseek_v4_pro": ["deepseek_v4_flash", "glm_51"]},
        )
        router = ModelRouter()
        fb = router.get_fallback("deepseek_v4_pro")
        assert fb == "deepseek_v4_flash"

    def test_get_fallback_missing(self):
        """Unknown model returns None."""
        router = ModelRouter()
        assert router.get_fallback("unknown_model_xyz") is None

    def test_fallback_chain_completeness(self, monkeypatch):
        """All entries in fallback chain have at least one candidate."""
        monkeypatch.setattr(
            "floodmind.agent.native.model_router._build_fallback_chain",
            lambda: {"a": ["b"], "b": ["c"]},
        )
        chain = _build_fallback_chain()
        for model, fallbacks in chain.items():
            assert len(fallbacks) > 0, f"{model} has empty fallback chain"


class TestSmartTimeouts:
    """Test per-tool timeout configuration."""

    def test_default_timeout(self):
        """Unknown tool gets default timeout."""
        router = ModelRouter()
        assert router.get_timeout_for_tool("nonexistent_tool") == SMART_TIMEOUTS["default"]

    def test_hydro_model_timeout(self):
        """Hydro model tools get extended timeout."""
        router = ModelRouter()
        assert router.get_timeout_for_tool("run_hydro_model") == 300.0

    def test_preview_data_timeout(self):
        """Fast tools get short timeout."""
        router = ModelRouter()
        assert router.get_timeout_for_tool("preview_data") == 15.0

    def test_none_tool_name(self):
        """None tool name gets default timeout."""
        router = ModelRouter()
        assert router.get_timeout_for_tool(None) == SMART_TIMEOUTS["default"]


class TestModelCallConfig:
    """Test ModelCallConfig factory."""

    def test_for_tool_sets_timeout(self):
        """for_tool factory reads SMART_TIMEOUTS."""
        cfg = ModelCallConfig.for_tool("deepseek_v4_pro", "run_hydro_model")
        assert cfg.timeout == 300.0
        assert cfg.model_key == "deepseek_v4_pro"

    def test_defaults(self):
        """Default values are sensible."""
        cfg = ModelCallConfig(model_key="test")
        assert cfg.timeout == 90.0
        assert cfg.max_retries == 2
        assert cfg.enable_fallback is True


class TestTokenUsageTracker:
    """Test TokenUsageTracker aggregation."""

    def test_empty_tracker(self):
        """Empty tracker returns zero summary."""
        tracker = TokenUsageTracker()
        summary = tracker.get_session_summary()
        assert summary["total_calls"] == 0
        assert summary["total_tokens"] == 0

    def test_single_record(self):
        """One record is reflected in summary."""
        tracker = TokenUsageTracker()
        tracker.record(
            TokenUsageRecord(
                timestamp=1.0,
                model_key="test",
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
            )
        )
        summary = tracker.get_session_summary()
        assert summary["total_calls"] == 1
        assert summary["total_tokens"] == 150
        assert summary["input_tokens"] == 100
        assert summary["output_tokens"] == 50

    def test_multi_model_aggregation(self):
        """Records from different models are grouped."""
        tracker = TokenUsageTracker()
        for _ in range(3):
            tracker.record(
                TokenUsageRecord(timestamp=1.0, model_key="model_a", total_tokens=100)
            )
        tracker.record(
            TokenUsageRecord(timestamp=1.0, model_key="model_b", total_tokens=200)
        )

        summary = tracker.get_session_summary()
        assert summary["by_model"]["model_a"]["calls"] == 3
        assert summary["by_model"]["model_b"]["calls"] == 1
        assert summary["total_tokens"] == 500

    def test_reset_clears_records(self):
        """reset() empties all records."""
        tracker = TokenUsageTracker()
        tracker.record(TokenUsageRecord(timestamp=1.0, model_key="t", total_tokens=10))
        tracker.reset()
        assert tracker.get_session_summary()["total_calls"] == 0

    def test_get_records_returns_copy(self):
        """get_records returns a defensive copy."""
        tracker = TokenUsageTracker()
        tracker.record(TokenUsageRecord(timestamp=1.0, model_key="t", total_tokens=10))
        records = tracker.get_records()
        records.clear()
        assert tracker.get_session_summary()["total_calls"] == 1


class TestModelRouterIntegration:
    """Test ModelRouter end-to-end."""

    def test_record_usage_from_event(self):
        """record_usage_from_event parses usage dict correctly."""
        router = ModelRouter()
        router.record_usage_from_event(
            "test_model",
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "reasoning_tokens": 20,
                "cache_read_tokens": 10,
                "cache_write_tokens": 5,
                "total_tokens": 150,
            },
        )
        summary = router.tracker.get_session_summary()
        assert summary["total_tokens"] == 150
        assert summary["input_tokens"] == 100
        assert summary["output_tokens"] == 50
        assert summary["reasoning_tokens"] == 20
        assert summary["cache_read_tokens"] == 10
        assert summary["cache_write_tokens"] == 5

    def test_record_usage_from_event_graceful_on_missing(self):
        """Missing optional fields default to 0."""
        router = ModelRouter()
        router.record_usage_from_event("test_model", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        summary = router.tracker.get_session_summary()
        assert summary["reasoning_tokens"] == 0

    def test_record_usage_skips_on_exception(self):
        """Malformed event is silently skipped."""
        router = ModelRouter()
        router.record_usage_from_event("test_model", None)  # type: ignore
        assert router.tracker.get_session_summary()["total_calls"] == 0
