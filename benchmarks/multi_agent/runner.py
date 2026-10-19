"""G-22 多代理收益基线运行器。

两种策略：

- ``single``：完整 runtime，但移除 ``SubAgent`` / ``ParallelTask``，模型只能自己做；
- ``delegated``：完整 runtime，保留委派工具，模型可自行决定是否拆分。

只有显式 ``--live`` 才会构造真实模型并执行任务；默认命令只列出任务与运行说明，
因此 CI 与本地单元测试不会发起网络调用。策略执行器可注入，离线测试用假执行器
验证任务隔离、成功判定、指标聚合与判定规则。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from benchmarks.multi_agent.metrics import (
    BenchmarkReport,
    DELEGATION_TOOLS,
    Observation,
    build_task_run,
    compare_task,
    summarize_categories,
)
from benchmarks.multi_agent.report import render_table, write_json
from benchmarks.multi_agent.tasks import TASKS, TASKS_BY_ID, BenchmarkTask

STRATEGIES = ("single", "delegated")

DEFAULT_SYSTEM_PROMPT = (
    "你是 FloodMind 基准测试中的执行代理。使用工作区中的文件完成任务，"
    "完成后给出简洁、可核对的结论。不要编造文件之外的事实。"
)


class StrategyExecutor(Protocol):
    """策略执行器：在给定 workspace 中执行任务并返回可观测结果。"""

    def execute(self, task: BenchmarkTask, workspace: Path, strategy: str) -> Observation:
        ...


# ── 工作区执行与聚合 ────────────────────────────────────────────


def _read_workspace_file(workspace: Path, rel_path: str) -> Optional[str]:
    root = workspace.resolve()
    target = (workspace / rel_path).resolve()
    if target != root and root not in target.parents:
        return None
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="replace")


def _execute_in_workspace(
    task: BenchmarkTask,
    executor: StrategyExecutor,
    strategy: str,
) -> Tuple[Observation, Tuple[str, ...]]:
    root = Path(tempfile.mkdtemp(prefix=f"fm-bench-{task.id}-{strategy}-"))
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        task.setup(workspace)
        started = time.perf_counter()
        try:
            observation = executor.execute(task, workspace, strategy)
        except Exception as exc:  # 单次执行失败必须记录，不能中断整轮基准
            observation = Observation(
                error=f"{type(exc).__name__}: {exc}",
                elapsed_seconds=time.perf_counter() - started,
            )
        files = {
            rel_path: _read_workspace_file(workspace, rel_path)
            for rel_path in task.success.required_paths()
        }
        failures = task.success.evaluate(observation.answer, files)
        return observation, failures
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_benchmark(
    tasks: Sequence[BenchmarkTask],
    executor: StrategyExecutor,
    *,
    token_ratio_threshold: float = 0.9,
    time_ratio_threshold: float = 0.9,
    model: str = "injected",
    generated_at: Optional[str] = None,
) -> BenchmarkReport:
    if token_ratio_threshold <= 0 or time_ratio_threshold <= 0:
        raise ValueError("收益阈值必须是正数（表示 delegated/single 的最大比值）")
    comparisons = []
    for task in tasks:
        runs = {}
        for strategy in STRATEGIES:
            observation, failures = _execute_in_workspace(task, executor, strategy)
            runs[strategy] = build_task_run(strategy, observation, failures)
        comparisons.append(
            compare_task(
                task_id=task.id,
                title=task.title,
                category=task.category,
                single=runs["single"],
                delegated=runs["delegated"],
                token_ratio_threshold=token_ratio_threshold,
                time_ratio_threshold=time_ratio_threshold,
            )
        )
    summary = summarize_categories(
        comparisons,
        token_ratio_threshold=token_ratio_threshold,
        time_ratio_threshold=time_ratio_threshold,
    )
    return BenchmarkReport(
        generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
        model=model,
        token_ratio_threshold=token_ratio_threshold,
        time_ratio_threshold=time_ratio_threshold,
        comparisons=tuple(comparisons),
        categories=summary,
    )


# ── live 执行 ──────────────────────────────────────────────────


class LiveStrategyExecutor:
    """用真实 ``floodmind.Agent`` 执行策略。

    - 两种策略都使用 ``bare=False`` 的完整 runtime 与相同系统提示词；
    - ``single`` 只移除委派工具，不改变其他工具；
    - 每个任务/策略使用独立 workspace，memory 持久化到 workspace 之外的临时目录；
    - 权限处理器直接放行，使任务在临时工作区内无人值守执行。
    """

    def __init__(
        self,
        model_key: str = "",
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        llm_factory: Optional[Callable[[], Any]] = None,
    ):
        self._model_key = model_key.strip()
        self._system_prompt = system_prompt
        self._llm_factory = llm_factory

    def execute(self, task: BenchmarkTask, workspace: Path, strategy: str) -> Observation:
        if strategy not in STRATEGIES:
            raise ValueError(f"未知策略: {strategy}")
        tool_calls: List[str] = []

        def _on_event(event: Dict[str, Any]) -> None:
            if event.get("type") == "action_start":
                name = str(event.get("tool_name", ""))
                if name:
                    tool_calls.append(name)

        agent = self._build_agent(task, workspace, strategy, _on_event)
        started = time.perf_counter()
        answer = ""
        error = ""
        try:
            answer = agent.run(task.prompt)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        usage = agent.last_usage
        try:
            agent.cleanup()
        except Exception:
            pass
        return Observation(
            answer=answer,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            tool_calls=tuple(tool_calls),
            elapsed_seconds=elapsed,
            error=error,
        )

    def _build_agent(
        self,
        task: BenchmarkTask,
        workspace: Path,
        strategy: str,
        on_event: Callable[[Dict[str, Any]], None],
    ):
        from floodmind import Agent
        from floodmind.agent.runtime.contracts.workspace import Workspace
        from floodmind.memory.dual_memory import DualMemory

        if self._llm_factory is not None:
            llm = self._llm_factory()
        else:
            from floodmind import ModelClient

            llm = (
                ModelClient.from_settings_with_preset(self._model_key)
                if self._model_key
                else ModelClient.from_settings()
            )
        session_id = f"bench-{task.id}-{strategy}-{uuid.uuid4().hex[:8]}"
        state_dir = workspace.parent / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        agent = Agent(
            llm=llm,
            memory=DualMemory(session_id=session_id, persist_dir=str(state_dir)),
            session_id=session_id,
            workspace=Workspace.from_folder(workspace, session_id=session_id).ensure(),
            bare=False,
            system_prompt=self._prompt_for(workspace),
            permission_handler=lambda *_: True,
            max_iterations=task.max_iterations,
            tool_loading=False,
            on_event=on_event,
        )
        if strategy == "single":
            removed = agent.raw._orchestrator_registry.unregister_names(DELEGATION_TOOLS)
            if removed != len(DELEGATION_TOOLS):
                raise RuntimeError(
                    f"single 策略应移除 {len(DELEGATION_TOOLS)} 个委派工具，实际移除 {removed} 个；"
                    "工具布局已变化，基准结果不可比"
                )
            executor = getattr(agent.raw, "_orchestrator_executor", None)
            if executor is None:
                raise RuntimeError("single 策略无法刷新工具 schema：orchestrator executor 缺失")
            executor.set_tools_schema(agent.raw._orchestrator_registry.tools_schema())
            agent.raw._rebuild_system_prompts()
        else:
            names = set(agent.raw._orchestrator_registry.names())
            missing = [name for name in DELEGATION_TOOLS if name not in names]
            if missing:
                raise RuntimeError(f"delegated 策略缺少委派工具: {', '.join(missing)}")
        return agent

    def _prompt_for(self, workspace: Path) -> str:
        """两种策略共用同一提示词，只补充工作区绝对路径这一环境事实。

        子代理默认在独立 sandbox 中运行；若任务文件位于父工作区，子任务的工作目录
        必须显式指向该路径，否则看不到 fixture。这是环境信息而不是策略引导。
        """
        root = str(workspace.resolve())
        return (
            f"{self._system_prompt}\n"
            f"当前工作区绝对路径: {root}。\n"
            "子任务若需要读写工作区文件，其工作目录应指向该路径。"
        )


# ── CLI ─────────────────────────────────────────────────────────


def select_tasks(task_ids: Optional[Iterable[str]]) -> List[BenchmarkTask]:
    wanted = [task_id for task_id in (task_ids or []) if task_id]
    if not wanted:
        return list(TASKS)
    missing = [task_id for task_id in wanted if task_id not in TASKS_BY_ID]
    if missing:
        raise SystemExit("未知任务: " + ", ".join(missing))
    return [TASKS_BY_ID[task_id] for task_id in wanted]


def _print_task_list() -> None:
    print("可用对比任务:")
    for task in TASKS:
        print(f"- {task.id:<22} [{task.category}] {task.title}")
        print(f"  {task.rationale}")


def main(argv: Optional[List[str]] = None) -> int:
    # Windows 控制台默认可能是 cp936；报告统一按 UTF-8 输出。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="FloodMind 多代理收益基线（G-22）")
    parser.add_argument("--list", action="store_true", help="列出任务后退出")
    parser.add_argument("--live", action="store_true", help="调用真实模型执行对比（默认不执行）")
    parser.add_argument("--task", action="append", dest="tasks", help="只跑指定任务 ID（可重复）")
    parser.add_argument("--model", default="", help="模型目录中的模型 key；缺省用当前激活模型")
    parser.add_argument("--json", dest="json_path", help="把机器可读报告写到指定路径")
    parser.add_argument("--token-ratio", type=float, default=0.9, help="token 收益阈值（默认 0.9）")
    parser.add_argument("--time-ratio", type=float, default=0.9, help="耗时收益阈值（默认 0.9）")
    args = parser.parse_args(argv)

    tasks = select_tasks(args.tasks)
    if args.list or not args.live:
        _print_task_list()
        print("")
        print("默认不执行任务，也不会调用模型。")
        print("运行真实对比: python -m benchmarks.multi_agent.runner --live [--task ID] [--json report.json]")
        return 0

    executor = LiveStrategyExecutor(model_key=args.model)
    report = run_benchmark(
        tasks,
        executor,
        token_ratio_threshold=args.token_ratio,
        time_ratio_threshold=args.time_ratio,
        model=args.model or "settings-default",
    )
    print(render_table(report))
    if args.json_path:
        write_json(report, args.json_path)
        print(f"\nJSON 报告: {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
