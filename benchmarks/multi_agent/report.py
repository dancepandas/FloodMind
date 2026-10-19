"""G-22 报告渲染与 JSON 序列化。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from benchmarks.multi_agent.metrics import BenchmarkReport, CategorySummary, TaskComparison, TaskRun


def _ratio_text(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}x"


def _run_text(run: TaskRun) -> str:
    return "PASS" if run.success else "FAIL"


def _comparison_row(item: TaskComparison) -> str:
    tokens = f"{item.single.total_tokens}->{item.delegated.total_tokens} ({_ratio_text(item.token_ratio)})"
    elapsed = (
        f"{item.single.elapsed_seconds:.1f}s->{item.delegated.elapsed_seconds:.1f}s "
        f"({_ratio_text(item.time_ratio)})"
    )
    tools = f"{len(item.single.tool_calls)}->{len(item.delegated.tool_calls)}"
    verdict = "SPLIT" if item.worth_splitting else "KEEP-SINGLE"
    return (
        f"{item.task_id:<22} {item.category:<20} {_run_text(item.single):<4} "
        f"{_run_text(item.delegated):<4} {tokens:<26} {elapsed:<28} {tools:<8} "
        f"{item.delegated.delegation_calls:<6} {verdict}"
    )


def _category_row(item: CategorySummary) -> str:
    verdict = "SPLIT" if item.worth_splitting else "KEEP-SINGLE"
    return (
        f"{item.category:<20} {item.task_count:<4} "
        f"{item.single_success_rate:.2f}->{item.delegated_success_rate:.2f}       "
        f"{_ratio_text(item.token_ratio):<8} {_ratio_text(item.time_ratio):<8} "
        f"{item.regressions:<4} {item.delegated_delegation_calls:<4} {verdict}"
    )


def render_table(report: BenchmarkReport) -> str:
    lines: List[str] = [
        f"模型: {report.model}",
        f"判定阈值: tokens <= {report.token_ratio_threshold:.2f}x 或 耗时 <= {report.time_ratio_threshold:.2f}x，"
        "且成功率不下降。",
        "",
        "任务                    类别                 single delegated tokens(single->delegated)   "
        "耗时(single->delegated)     工具(s->d) 委派   判定",
        "-" * 150,
    ]
    lines.extend(_comparison_row(item) for item in report.comparisons)
    lines.extend(["", "分类汇总", "-" * 80])
    lines.append("类别                 任务数 成功率(single->delegated) tokens   耗时     退化 委派 判定")
    lines.extend(_category_row(item) for item in report.categories)
    for item in report.categories:
        lines.append(f"  {item.category}: {item.reason}")
    return "\n".join(lines)


def _run_dict(run: TaskRun) -> Dict[str, Any]:
    return {
        "strategy": run.strategy,
        "success": run.success,
        "failures": list(run.failures),
        "error": run.error,
        "elapsed_seconds": round(run.elapsed_seconds, 4),
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "total_tokens": run.total_tokens,
        "tool_calls": list(run.tool_calls),
        "delegation_calls": run.delegation_calls,
        "answer": run.answer,
    }


def to_dict(report: BenchmarkReport) -> Dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "model": report.model,
        "thresholds": {
            "token_ratio": report.token_ratio_threshold,
            "time_ratio": report.time_ratio_threshold,
        },
        "tasks": [
            {
                "id": item.task_id,
                "title": item.title,
                "category": item.category,
                "single": _run_dict(item.single),
                "delegated": _run_dict(item.delegated),
                "token_ratio": None if item.token_ratio is None else round(item.token_ratio, 4),
                "time_ratio": None if item.time_ratio is None else round(item.time_ratio, 4),
                "worth_splitting": item.worth_splitting,
                "reason": item.reason,
            }
            for item in report.comparisons
        ],
        "categories": [
            {
                "category": item.category,
                "task_count": item.task_count,
                "single_success_rate": round(item.single_success_rate, 4),
                "delegated_success_rate": round(item.delegated_success_rate, 4),
                "token_ratio": None if item.token_ratio is None else round(item.token_ratio, 4),
                "time_ratio": None if item.time_ratio is None else round(item.time_ratio, 4),
                "regressions": item.regressions,
                "delegated_delegation_calls": item.delegated_delegation_calls,
                "worth_splitting": item.worth_splitting,
                "reason": item.reason,
            }
            for item in report.categories
        ],
    }


def write_json(report: BenchmarkReport, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(to_dict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
