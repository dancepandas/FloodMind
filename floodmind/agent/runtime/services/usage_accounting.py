"""
UsageAccounting — 用量下钻与成本归因。

在 aggregate_metrics（session 级聚合）之上，提供按 run、模型、步骤、工具的
下钻视图，并在配置模型单价时估算成本。数据来源为已完成的 TraceSpan：

- LLM span：模型取自 span.input["model"] 或 metadata["gen_ai.request.model"]；
  token 取自 span.output["tokens"] 或 output["gen_ai.usage.input/output_tokens"]；
  run_id / step_key 取自 metadata["harness.run_id"] / metadata["harness.step_key"]。
- 工具 span：按 name 统计 calls / errors / duration。
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple, Union

from floodmind.agent.runtime.contracts.tracing import TraceEvent, TraceSpan

# run_id / step_key 缺失时的统一归入桶
UNATTRIBUTED = "unattributed"


@dataclass(frozen=True)
class ModelPrice:
    """单个模型的计价（每 1k token 单价，默认 USD）。"""

    input_per_1k: float
    output_per_1k: float
    currency: str = "USD"


def normalize_pricing(raw: Mapping[str, Any]) -> Dict[str, ModelPrice]:
    """把配置里的原始定价表归一化为 {model: ModelPrice}。

    配置形态::

        {"deepseek-v4-flash": {"input_per_1k": 0.1, "output_per_1k": 0.3, "currency": "USD"}}

    非法条目（缺字段、价格非数值、currency 非字符串）一律抛 ValueError，不静默跳过。
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"model_pricing 必须是对象，实际为 {type(raw).__name__}")
    pricing: Dict[str, ModelPrice] = {}
    for model, entry in raw.items():
        if not isinstance(model, str) or not model:
            raise ValueError(f"model_pricing 的模型名必须是非空字符串: {model!r}")
        if not isinstance(entry, Mapping):
            raise ValueError(f"model_pricing[{model!r}] 必须是对象，实际为 {type(entry).__name__}")
        try:
            input_per_1k = float(entry["input_per_1k"])
            output_per_1k = float(entry["output_per_1k"])
        except KeyError as exc:
            raise ValueError(f"model_pricing[{model!r}] 缺少字段 {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"model_pricing[{model!r}] 的价格必须是数值: {exc}") from exc
        currency = entry.get("currency", "USD")
        if not isinstance(currency, str) or not currency:
            raise ValueError(f"model_pricing[{model!r}].currency 必须是非空字符串")
        pricing[model] = ModelPrice(
            input_per_1k=input_per_1k,
            output_per_1k=output_per_1k,
            currency=currency,
        )
    return pricing


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: Mapping[str, ModelPrice],
) -> Optional[Dict[str, Any]]:
    """按单价估算单次调用的成本；模型未定价时返回 None。"""
    price = pricing.get(model)
    if price is None:
        return None
    amount = (input_tokens / 1000.0) * price.input_per_1k + (
        output_tokens / 1000.0
    ) * price.output_per_1k
    return {"amount": round(amount, 6), "currency": price.currency}


def _percentile(values: List[float], p: float) -> float:
    """Nearest-rank 百分位；空列表返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(p / 100.0 * len(ordered)) - 1))
    return round(ordered[index], 3)


def _duration_stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    return {
        "count": len(values),
        "avg_ms": round(sum(values) / len(values), 3),
        "p50_ms": _percentile(values, 50),
        "p95_ms": _percentile(values, 95),
        "max_ms": round(max(values), 3),
    }


def _extract_model(span: TraceSpan) -> str:
    model = span.input.get("model") or span.metadata.get("gen_ai.request.model")
    return model if isinstance(model, str) and model else "unknown"


def _extract_tokens(span: TraceSpan) -> Tuple[int, int]:
    """LLM span 的 token 数：优先 output["tokens"]，回退 gen_ai.usage.*。"""
    tokens = span.output.get("tokens")
    if isinstance(tokens, Mapping):
        try:
            return int(tokens.get("prompt_tokens") or 0), int(tokens.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            pass
    try:
        return (
            int(span.output.get("gen_ai.usage.input_tokens") or 0),
            int(span.output.get("gen_ai.usage.output_tokens") or 0),
        )
    except (TypeError, ValueError):
        return 0, 0


def _new_bucket() -> Dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "llm_calls": 0,
        "estimated_cost": 0.0,
        "currency": None,
    }


def aggregate_usage(
    items: List[Union[TraceSpan, TraceEvent]],
    pricing: Optional[Mapping[str, ModelPrice]] = None,
) -> Dict[str, Any]:
    """按 run / 模型 / 步骤 / 工具下钻聚合已完成 span 的用量与成本。

    只统计已完成的 span（``duration_ms`` 非空）。estimated_cost 只汇总已定价
    模型的成本；存在未定价模型时放入 totals["unpriced_models"]；完全未配置
    价格（pricing 为 None/空）时 estimated_cost 为 None。
    """
    pricing = pricing or {}
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": None,
        "currency": None,
        "unpriced_models": [],
    }
    by_run: Dict[str, Dict[str, Any]] = {}
    by_model: Dict[str, Dict[str, Any]] = {}
    by_step: Dict[str, Dict[str, Any]] = {}
    by_tool: Dict[str, Dict[str, Any]] = {}
    tool_durations: Dict[str, List[float]] = {}
    unpriced: Set[str] = set()
    cost_sum = 0.0
    currency: Optional[str] = None

    def _bucket(table: Dict[str, Dict[str, Any]], key: str) -> Dict[str, Any]:
        return table.setdefault(key, _new_bucket())

    for item in items:
        if not isinstance(item, TraceSpan) or item.duration_ms is None:
            continue
        if item.type == "llm":
            model = _extract_model(item)
            in_tok, out_tok = _extract_tokens(item)
            run_id = item.metadata.get("harness.run_id") or UNATTRIBUTED
            step_key = item.metadata.get("harness.step_key") or UNATTRIBUTED

            totals["input_tokens"] += in_tok
            totals["output_tokens"] += out_tok
            totals["total_tokens"] += in_tok + out_tok

            for bucket in (
                _bucket(by_run, run_id),
                _bucket(by_model, model),
                _bucket(by_step, step_key),
            ):
                bucket["input_tokens"] += in_tok
                bucket["output_tokens"] += out_tok
                bucket["total_tokens"] += in_tok + out_tok
                bucket["llm_calls"] += 1

            cost = estimate_cost(model, in_tok, out_tok, pricing)
            if cost is None:
                unpriced.add(model)
            else:
                cost_sum += cost["amount"]
                if currency is None:
                    currency = cost["currency"]
                for bucket in (
                    by_run[run_id],
                    by_model[model],
                    by_step[step_key],
                ):
                    bucket["estimated_cost"] = round(bucket["estimated_cost"] + cost["amount"], 6)
                    if bucket["currency"] is None:
                        bucket["currency"] = cost["currency"]
        elif item.type == "tool":
            name = item.name or item.metadata.get("gen_ai.tool.name") or "unknown"
            entry = by_tool.setdefault(
                name, {"calls": 0, "errors": 0, "duration_ms": _duration_stats([])}
            )
            entry["calls"] += 1
            if item.status == "error":
                entry["errors"] += 1
            tool_durations.setdefault(name, []).append(item.duration_ms)

    for name, durations in tool_durations.items():
        by_tool[name]["duration_ms"] = _duration_stats(durations)

    if pricing:
        totals["estimated_cost"] = round(cost_sum, 6)
        totals["currency"] = currency
    totals["unpriced_models"] = sorted(unpriced)

    return {
        "totals": totals,
        "by_run": by_run,
        "by_model": by_model,
        "by_step": by_step,
        "by_tool": by_tool,
    }
