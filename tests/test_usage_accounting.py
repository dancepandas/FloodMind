"""UsageAccounting — 价格归一化、成本估算与按 run/model/step/tool 下钻聚合。"""

import pytest

from floodmind.agent.runtime.contracts.tracing import TraceSpan
from floodmind.agent.runtime.services.usage_accounting import (
    UNATTRIBUTED,
    ModelPrice,
    aggregate_usage,
    estimate_cost,
    normalize_pricing,
)

PRICING = {
    "model-a": ModelPrice(input_per_1k=0.1, output_per_1k=0.3, currency="USD"),
    "model-b": ModelPrice(input_per_1k=1.0, output_per_1k=2.0, currency="CNY"),
}


def _llm_span(model, in_tok, out_tok, run_id="", step_key="", status="ok"):
    span = TraceSpan(
        type="llm",
        name="llm_step",
        input={"model": model},
        metadata={},
        status=status,
    )
    if run_id:
        span.metadata["harness.run_id"] = run_id
    if step_key:
        span.metadata["harness.step_key"] = step_key
    span.finalize(
        output={
            "tokens": {"prompt_tokens": in_tok, "completion_tokens": out_tok},
            "gen_ai.usage.input_tokens": in_tok,
            "gen_ai.usage.output_tokens": out_tok,
        },
        status=status,
    )
    return span


def _tool_span(name, duration=5.0, status="ok"):
    span = TraceSpan(type="tool", name=name, metadata={"gen_ai.tool.name": name}, status=status)
    span.finalize(output={}, status=status)
    # finalize 按墙钟计时，测试需固定时长以断言 duration 统计
    span.duration_ms = duration
    return span


class TestNormalizePricing:
    def test_valid_entries(self):
        pricing = normalize_pricing({
            "m1": {"input_per_1k": 0.1, "output_per_1k": 0.3, "currency": "CNY"},
            "m2": {"input_per_1k": 1, "output_per_1k": 2},
        })
        assert pricing["m1"] == ModelPrice(0.1, 0.3, "CNY")
        # currency 缺省为 USD
        assert pricing["m2"] == ModelPrice(1.0, 2.0, "USD")

    def test_none_returns_empty(self):
        assert normalize_pricing(None) == {}

    @pytest.mark.parametrize("entry", [
        "not-a-dict",
        {"input_per_1k": 0.1},
        {"output_per_1k": 0.3},
        {"input_per_1k": "abc", "output_per_1k": 0.3},
        {"input_per_1k": 0.1, "output_per_1k": 0.3, "currency": ""},
    ])
    def test_invalid_entry_raises(self, entry):
        with pytest.raises(ValueError):
            normalize_pricing({"m": entry})

    def test_non_mapping_root_raises(self):
        with pytest.raises(ValueError):
            normalize_pricing(["not-a-mapping"])


class TestEstimateCost:
    def test_priced_model(self):
        cost = estimate_cost("model-a", 2000, 1000, PRICING)
        assert cost == {"amount": pytest.approx(0.5), "currency": "USD"}

    def test_unpriced_model_returns_none(self):
        assert estimate_cost("unknown", 100, 100, PRICING) is None

    def test_no_pricing_returns_none(self):
        assert estimate_cost("model-a", 100, 100, {}) is None


class TestAggregateUsage:
    def test_empty_items(self):
        result = aggregate_usage([], PRICING)
        assert result["totals"]["total_tokens"] == 0
        assert result["totals"]["estimated_cost"] == 0.0
        assert result["by_run"] == {}
        assert result["by_tool"] == {}

    def test_no_pricing_cost_is_none(self):
        result = aggregate_usage([_llm_span("model-a", 100, 50)])
        assert result["totals"]["estimated_cost"] is None
        assert result["totals"]["input_tokens"] == 100
        # 无价格时未定价模型仍被列出
        assert result["totals"]["unpriced_models"] == ["model-a"]

    def test_by_run_and_unattributed_default(self):
        result = aggregate_usage(
            [
                _llm_span("model-a", 100, 50, run_id="run-1"),
                _llm_span("model-a", 100, 50),
            ],
            PRICING,
        )
        by_run = result["by_run"]
        assert by_run["run-1"]["total_tokens"] == 150
        assert by_run[UNATTRIBUTED]["total_tokens"] == 150

    def test_by_model(self):
        result = aggregate_usage(
            [
                _llm_span("model-a", 1000, 1000),
                _llm_span("model-b", 1000, 1000),
            ],
            PRICING,
        )
        assert result["by_model"]["model-a"]["estimated_cost"] == pytest.approx(0.4)
        assert result["by_model"]["model-a"]["currency"] == "USD"
        assert result["by_model"]["model-b"]["estimated_cost"] == pytest.approx(3.0)
        assert result["by_model"]["model-b"]["currency"] == "CNY"

    def test_by_step(self):
        result = aggregate_usage(
            [
                _llm_span("model-a", 100, 0, step_key="step-1"),
                _llm_span("model-a", 0, 100, step_key="step-2"),
            ],
            PRICING,
        )
        assert result["by_step"]["step-1"]["output_tokens"] == 0
        assert result["by_step"]["step-2"]["input_tokens"] == 0

    def test_by_tool(self):
        result = aggregate_usage(
            [
                _tool_span("Read", duration=10.0),
                _tool_span("Read", duration=30.0, status="error"),
                _tool_span("Bash", duration=20.0),
            ]
        )
        read = result["by_tool"]["Read"]
        assert read["calls"] == 2
        assert read["errors"] == 1
        assert read["duration_ms"]["avg_ms"] == 20.0
        assert read["duration_ms"]["max_ms"] == 30.0
        bash = result["by_tool"]["Bash"]
        assert bash["calls"] == 1
        assert bash["errors"] == 0

    def test_unfinished_spans_ignored(self):
        span = TraceSpan(type="llm", name="llm_step", input={"model": "model-a"})
        result = aggregate_usage([span], PRICING)
        assert result["totals"]["total_tokens"] == 0
        assert result["by_model"] == {}

    def test_unpriced_models_listed_but_cost_sums_priced(self):
        result = aggregate_usage(
            [
                _llm_span("model-a", 1000, 1000),
                _llm_span("model-x", 1000, 1000),
                _llm_span("model-y", 500, 500),
            ],
            PRICING,
        )
        assert result["totals"]["unpriced_models"] == ["model-x", "model-y"]
        assert result["totals"]["estimated_cost"] == pytest.approx(0.4)

    def test_tokens_from_gen_ai_usage_keys(self):
        span = TraceSpan(
            type="llm",
            name="llm_step",
            metadata={"gen_ai.request.model": "model-a"},
        )
        span.finalize(output={"gen_ai.usage.input_tokens": 42, "gen_ai.usage.output_tokens": 7})
        result = aggregate_usage([span], PRICING)
        assert result["totals"]["input_tokens"] == 42
        assert result["totals"]["output_tokens"] == 7
        assert result["by_model"]["model-a"]["total_tokens"] == 49
