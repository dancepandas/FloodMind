"""Tests for tool guardrails and error classification."""

import json
from unittest.mock import MagicMock, patch

import pytest

from floodmind.agent.native.error_classifier import (
    ClassifiedError,
    ErrorClassifier,
    HydroErrorType,
    RecoveryAction,
)
from floodmind.agent.native.tool_guardrails import (
    GuardrailConfig,
    GuardrailDecision,
    ToolGuardrail,
)


class TestToolGuardrail:
    """Test ToolGuardrail state machine."""

    def test_exact_failure_warns_after_threshold(self):
        """Same args fail 3 times → warn (default threshold=2)."""
        g = ToolGuardrail(GuardrailConfig(exact_failure_warn_after=2))
        args = {"path": "/data/test.csv"}

        # 1st fail → allow
        d1 = g.check("read_csv", args, _error_result())
        assert d1.action == "allow"

        # 2nd fail → warn
        d2 = g.check("read_csv", args, _error_result())
        assert d2.action == "warn"
        assert d2.code == "exact_failure_warn"

    def test_exact_failure_blocks_after_threshold(self):
        """Same args fail 3 times with hard_stop → block."""
        g = ToolGuardrail(GuardrailConfig(exact_failure_block_after=3, hard_stop_enabled=True))
        args = {"path": "/data/test.csv"}

        for _ in range(2):
            g.check("read_csv", args, _error_result())
        d = g.check("read_csv", args, _error_result())
        assert d.should_block
        assert d.code == "exact_failure_block"

    def test_failure_spiral_detected(self):
        """Same tool, different args, all fail → spiral warning."""
        g = ToolGuardrail(GuardrailConfig(same_tool_failure_warn_after=2))

        g.check("read_csv", {"path": "a.csv"}, _error_result())
        g.check("read_csv", {"path": "b.csv"}, _error_result())

        d = g.check("read_csv", {"path": "c.csv"}, _error_result())
        assert d.action == "warn"
        assert d.code == "same_tool_failure_warn"

    def test_idempotent_no_progress_detection(self):
        """Same tool, same args, same result → no-progress warning."""
        g = ToolGuardrail(GuardrailConfig(no_progress_warn_after=1))
        args = {"path": "a.csv"}
        ok_result = {"status": "completed", "content": "same"}

        g.check("preview_data", args, ok_result)
        d = g.check("preview_data", args, ok_result)
        assert d.action == "warn"
        assert d.code == "no_progress_warn"

    def test_success_resets_spiral_counter(self):
        """Successful call resets failure-spiral counter."""
        g = ToolGuardrail(GuardrailConfig(same_tool_failure_warn_after=3))
        g.check("read_csv", {"path": "a.csv"}, _error_result())
        g.check("read_csv", {"path": "a.csv"}, _ok_result())  # success resets spiral
        g.check("read_csv", {"path": "b.csv"}, _error_result())
        d = g.check("read_csv", {"path": "c.csv"}, _error_result())
        # Without reset, 3 errors would warn; with reset only 2 errors → allow
        assert d.action == "allow"  # spiral counter was reset by success

    def test_block_only_for_exact_and_spiral(self):
        """NO_PROGRESS never blocks, only warns."""
        g = ToolGuardrail(GuardrailConfig(no_progress_block_after=1))
        args = {"path": "a.csv"}
        ok_result = {"status": "completed", "content": "same"}

        g.check("preview_data", args, ok_result)
        d = g.check("preview_data", args, ok_result)
        assert not d.should_block  # no_progress only warns

    def test_reset(self):
        """reset() clears all state."""
        g = ToolGuardrail()
        g.check("t", {"a": 1}, _error_result())
        g.reset()
        d = g.check("t", {"a": 1}, _error_result())
        assert d.action == "allow"


def _error_result():
    return {"status": "error", "content": "file not found"}


def _ok_result():
    return {"status": "completed", "content": "ok"}


class TestErrorClassifier:
    """Test ErrorClassifier taxonomy and recovery actions."""

    def test_classify_json_decode_error(self):
        """JSON decode error → data_format with retry+fix."""
        err = json.JSONDecodeError("test", "doc", 0)
        classified = ErrorClassifier.classify(err)
        assert classified.error_type == HydroErrorType.data_format
        assert classified.recovery.action == "retry_with_fix"

    def test_classify_timeout_error(self):
        """TimeoutError → api_timeout with retry."""
        err = TimeoutError("connection timed out")
        classified = ErrorClassifier.classify(err)
        assert classified.error_type == HydroErrorType.api_timeout
        assert classified.recovery.action == "retry"

    def test_classify_connection_error(self):
        """ConnectionError → network with retry."""
        err = ConnectionError("refused")
        classified = ErrorClassifier.classify(err)
        assert classified.error_type == HydroErrorType.network
        assert classified.recovery.action == "retry"

    def test_classify_file_not_found(self):
        """FileNotFoundError → data_missing with ask_user."""
        err = FileNotFoundError("no such file")
        classified = ErrorClassifier.classify(err)
        assert classified.error_type == HydroErrorType.data_missing
        assert classified.recovery.action == "ask_user"

    def test_classify_permission_error(self):
        """PermissionError → file_permission with ask_user."""
        err = PermissionError("access denied")
        classified = ErrorClassifier.classify(err)
        assert classified.error_type == HydroErrorType.file_permission
        assert classified.recovery.action == "ask_user"

    def test_classify_memory_error(self):
        """MemoryError → resource_exhausted with abort."""
        err = MemoryError("out of memory")
        classified = ErrorClassifier.classify(err)
        assert classified.error_type == HydroErrorType.resource_exhausted
        assert classified.recovery.action == "abort"

    def test_classify_value_error(self):
        """ValueError → data_range with retry+fix."""
        err = ValueError("invalid value")
        classified = ErrorClassifier.classify(err)
        assert classified.error_type == HydroErrorType.data_range

    def test_classify_tool_error_timeout(self):
        """Tool error classification with tool context."""
        err = TimeoutError("timed out")
        classified = ErrorClassifier.classify_tool_error("run_hydro_model", err)
        assert classified.error_type == HydroErrorType.api_timeout
        assert classified.recovery.action == "retry"

    def test_classify_model_error_fallback(self):
        """Model error gets fallback suggestion for known model."""
        err = TimeoutError("timed out")
        classified = ErrorClassifier.classify_model_error(err, "deepseek_v4_pro")
        assert classified.error_type == HydroErrorType.api_timeout
        assert classified.recovery.fallback_model == "deepseek_v4_flash"

    def test_classify_model_error_default_fallback(self):
        """Unknown model gets default fallback suggestion."""
        err = TimeoutError("timed out")
        classified = ErrorClassifier.classify_model_error(err, "unknown_model")
        assert classified.recovery.fallback_model == "deepseek_v4_flash"

    def test_classify_unknown_error(self):
        """Generic Exception → unknown with retry."""
        err = Exception("something weird")
        classified = ErrorClassifier.classify(err)
        assert classified.error_type == HydroErrorType.unknown
        assert classified.recovery.action == "retry"
