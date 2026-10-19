"""G-22 指标聚合与拆分判定。

纯数据层：不读文件、不调用模型，便于离线测试。输入是每个任务两种策略的
``Observation``，输出可渲染、可序列化的对比报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

DELEGATION_TOOLS = ("SubAgent", "ParallelTask")


@dataclass(frozen=True)
class Observation:
    """一次策略执行的可观测结果。"""

    answer: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tool_calls: Tuple[str, ...] = ()
    elapsed_seconds: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class TaskRun:
    strategy: str
    success: bool
    failures: Tuple[str, ...]
    error: str
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tool_calls: Tuple[str, ...]
    delegation_calls: int
    answer: str


def delegation_calls(tool_calls: Iterable[str]) -> int:
    return sum(1 for name in tool_calls if name in DELEGATION_TOOLS)


def build_task_run(strategy: str, observation: Observation, failures: Sequence[str]) -> TaskRun:
    return TaskRun(
        strategy=strategy,
        success=not failures and not observation.error,
        failures=tuple(failures),
        error=observation.error,
        elapsed_seconds=float(observation.elapsed_seconds),
        prompt_tokens=int(observation.prompt_tokens),
        completion_tokens=int(observation.completion_tokens),
        total_tokens=int(observation.total_tokens),
        tool_calls=tuple(observation.tool_calls),
        delegation_calls=delegation_calls(observation.tool_calls),
        answer=observation.answer,
    )


@dataclass
class TaskComparison:
    task_id: str
    title: str
    category: str
    single: TaskRun
    delegated: TaskRun
    token_ratio: Optional[float]
    time_ratio: Optional[float]
    worth_splitting: bool
    reason: str


@dataclass
class CategorySummary:
    category: str
    task_count: int
    single_success_rate: float
    delegated_success_rate: float
    token_ratio: Optional[float]
    time_ratio: Optional[float]
    regressions: int
    delegated_delegation_calls: int
    worth_splitting: bool
    reason: str


@dataclass
class BenchmarkReport:
    generated_at: str
    model: str
    token_ratio_threshold: float
    time_ratio_threshold: float
    comparisons: Tuple[TaskComparison, ...]
    categories: Tuple[CategorySummary, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Verdict:
    success_ok: bool
    efficiency_ok: bool
    worthwhile: bool
    reason: str


def _ratio(delegated: float, single: float) -> Optional[float]:
    if single <= 0:
        return None
    return delegated / single


def _success_rate(runs: Sequence[TaskRun]) -> float:
    if not runs:
        return 0.0
    return sum(1 for run in runs if run.success) / len(runs)


def judge(
    *,
    single_success: float,
    delegated_success: float,
    token_ratio: Optional[float],
    time_ratio: Optional[float],
    token_threshold: float,
    time_threshold: float,
) -> Verdict:
    """拆分判定：成功率不降，且 token 或耗时至少一项达到收益阈值。"""
    success_ok = delegated_success >= single_success
    efficiency_ok = (
        (token_ratio is not None and token_ratio <= token_threshold)
        or (time_ratio is not None and time_ratio <= time_threshold)
    )
    token_text = "n/a" if token_ratio is None else f"{token_ratio:.2f}x"
    time_text = "n/a" if time_ratio is None else f"{time_ratio:.2f}x"
    if not success_ok:
        reason = (
            f"成功率下降（single={single_success:.2f}, delegated={delegated_success:.2f}），"
            "不建议拆分"
        )
    elif not efficiency_ok:
        reason = (
            f"成功率不降但无成本/时延收益（tokens {token_text}, 耗时 {time_text}），"
            "不建议拆分"
        )
    else:
        reason = (
            f"成功率不降，收益达到阈值（tokens {token_text}, 耗时 {time_text}），"
            "建议拆分"
        )
    # 全零成功率不能因为比值好看而被判为有收益。
    return Verdict(
        success_ok=success_ok,
        efficiency_ok=efficiency_ok,
        worthwhile=success_ok and efficiency_ok and delegated_success > 0,
        reason=reason,
    )


def compare_task(
    *,
    task_id: str,
    title: str,
    category: str,
    single: TaskRun,
    delegated: TaskRun,
    token_ratio_threshold: float,
    time_ratio_threshold: float,
) -> TaskComparison:
    token_ratio = _ratio(delegated.total_tokens, single.total_tokens)
    time_ratio = _ratio(delegated.elapsed_seconds, single.elapsed_seconds)
    verdict = judge(
        single_success=1.0 if single.success else 0.0,
        delegated_success=1.0 if delegated.success else 0.0,
        token_ratio=token_ratio,
        time_ratio=time_ratio,
        token_threshold=token_ratio_threshold,
        time_threshold=time_ratio_threshold,
    )
    if delegated.delegation_calls == 0:
        # 没有实际委派时，成本差异只能归因于运行波动，不能作为拆分收益。
        worthwhile = False
        reason = "delegated 未实际使用委派，收益无法归因于拆分；" + verdict.reason
    else:
        worthwhile = verdict.worthwhile
        reason = verdict.reason
    return TaskComparison(
        task_id=task_id,
        title=title,
        category=category,
        single=single,
        delegated=delegated,
        token_ratio=token_ratio,
        time_ratio=time_ratio,
        worth_splitting=worthwhile,
        reason=reason,
    )


def summarize_category(
    category: str,
    comparisons: Sequence[TaskComparison],
    *,
    token_ratio_threshold: float,
    time_ratio_threshold: float,
) -> CategorySummary:
    runs = [comparison for comparison in comparisons if comparison.category == category]
    singles = [item.single for item in runs]
    delegated = [item.delegated for item in runs]
    single_success = _success_rate(singles)
    delegated_success = _success_rate(delegated)
    token_ratio = _ratio(
        sum(run.total_tokens for run in delegated),
        sum(run.total_tokens for run in singles),
    )
    time_ratio = _ratio(
        sum(run.elapsed_seconds for run in delegated),
        sum(run.elapsed_seconds for run in singles),
    )
    verdict = judge(
        single_success=single_success,
        delegated_success=delegated_success,
        token_ratio=token_ratio,
        time_ratio=time_ratio,
        token_threshold=token_ratio_threshold,
        time_threshold=time_ratio_threshold,
    )
    # 聚合成功率可能掩盖“A 任务成功换成 B 任务成功”的交换；逐任务回退必须单独计数。
    regressions = sum(
        1 for item in runs if item.single.success and not item.delegated.success
    )
    delegated_calls = sum(run.delegation_calls for run in delegated)
    if regressions:
        reason = f"存在 {regressions} 个任务从 single 成功变为 delegated 失败；" + verdict.reason
        worthwhile = False
    elif delegated_calls == 0:
        reason = "delegated 未实际使用委派，收益无法归因于拆分；" + verdict.reason
        worthwhile = False
    else:
        reason = verdict.reason
        worthwhile = verdict.worthwhile
    return CategorySummary(
        category=category,
        task_count=len(runs),
        single_success_rate=single_success,
        delegated_success_rate=delegated_success,
        token_ratio=token_ratio,
        time_ratio=time_ratio,
        regressions=regressions,
        delegated_delegation_calls=delegated_calls,
        worth_splitting=worthwhile,
        reason=reason,
    )


def summarize_categories(
    comparisons: Sequence[TaskComparison],
    *,
    token_ratio_threshold: float,
    time_ratio_threshold: float,
) -> Tuple[CategorySummary, ...]:
    ordered: List[str] = []
    for comparison in comparisons:
        if comparison.category not in ordered:
            ordered.append(comparison.category)
    return tuple(
        summarize_category(
            category,
            comparisons,
            token_ratio_threshold=token_ratio_threshold,
            time_ratio_threshold=time_ratio_threshold,
        )
        for category in ordered
    )
