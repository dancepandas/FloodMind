"""内部评测集执行器（G-14）。

四层常规集合 + held-out 校准集：

- ``smoke``：每次提交跑，验证主循环、工具往返、终态等基本行为；
- ``tool``：真实 ``ToolExecutionService`` + 真实文件工具，验证工具层与 workspace 状态；
- ``regression``：安全与契约回归（预算、中断、迭代边界、工具失败恢复）；
- ``safety``：真实工具层安全边界（路径越界、planning 硬门、只读对照）；
- held-out（``--heldout``）：默认不加载的校准用例，用于定期换血。

用例来源：``evals/cases/*.json`` 手工高价值场景 + ``evals.case_packs`` 确定性
参数化家族（见 ``evals/case_packs.py``）。两侧 id 必须全局唯一。

设计约束：

- 确定性：模型轨迹由 JSON 脚本给出，不调用真实模型，判定器是状态/轨迹断言；
- 走真实主循环：直接构造 ``NativeAgentExecutor``，只替换模型客户端与工具执行器，
  因此评测覆盖的是执行器本身，而不是另写一套模拟循环；
- 真实工具层仍走生产 ``ToolExecutionService``，只把路径根限定在评测临时 workspace；
- 可扩展：新增手工用例只需在 ``evals/cases/`` 放一个 JSON 文件；系统性扩展
  优先新增 case pack 家族，不改执行器。

用法::

    python -m evals.runner                    # 全部常规用例（CI 门禁）
    python -m evals.runner --tier smoke       # 只跑冒烟集
    python -m evals.runner --heldout          # 常规集 + held-out 校准集
    python -m evals.runner --json report.json # 额外输出机器可读报告

任一用例失败时进程退出码为 1（CI 门禁）。
"""

from __future__ import annotations

import argparse
import atexit
import glob
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 在导入 floodmind 之前把状态根重定向到临时目录：评测不得读写用户真实配置/记忆。
_EVAL_HOME = tempfile.mkdtemp(prefix="floodmind-eval-home-")
atexit.register(shutil.rmtree, _EVAL_HOME, True)
os.environ["FLOODMIND_HOME"] = _EVAL_HOME

CASES_DIR = Path(__file__).resolve().parent / "cases"
SUPPORTED_TIERS = ("smoke", "tool", "regression", "safety")


# ── 脚本化模型与工具 ────────────────────────────────────────────


class ScriptedModelClient:
    """按用例脚本逐轮返回 ``ModelEvent``，并记录模型实际看到的上下文。"""

    model_name = "scripted-eval"

    def __init__(self, rounds: List[List[Any]]):
        self._rounds = rounds
        self.calls: List[Dict[str, Any]] = []

    def stream_chat(self, messages=None, tools=None, **kwargs):
        index = len(self.calls)
        self.calls.append({"messages": messages or [], "tools": tools or []})
        if index >= len(self._rounds):
            raise AssertionError(
                f"评测脚本只提供了 {len(self._rounds)} 轮模型输出，"
                f"执行器发起了第 {index + 1} 次调用"
            )
        return iter(self._rounds[index])

    def classify_error(self, error: Exception):
        # 评测用例里的模型错误是确定性的终态（不重试），避免脚本轮次被隐式耗尽。
        from floodmind.agent.native.transport import TransportRetryAdvice

        return TransportRetryAdvice(
            retry_suggested=False,
            response_started=False,
            replay_safe=False,
            normalized_error=str(error),
        )


class _EvalToolRegistry:
    def __init__(self, specs: Dict[str, Any]):
        self._specs = dict(specs)

    def get(self, name: str):
        return self._specs.get(name)

    def all(self):
        return list(self._specs.values())

    def tools_schema(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._specs.values()
        ]


class ScriptedToolExecutor:
    """按用例声明的结果返回工具结果，不执行任何真实副作用。

    结果查找顺序：调用 id（``tool_results`` 声明）优先于工具名
    （``tools.<name>.result`` 声明），因此同名工具可以在不同调用上
    返回不同结果（连续失败/恢复家族的确定性前提）。
    """

    def __init__(self, outputs: Dict[str, Dict[str, Any]]):
        self._outputs = outputs
        self.calls: List[Any] = []

    def execute(self, call, context, **kwargs):
        from floodmind.agent.runtime.contracts.tools import ToolResult

        self.calls.append(call)
        declared = self._outputs.get(call.id)
        if declared is None:
            declared = self._outputs.get(call.name, {})
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            content=str(declared.get("content", "")),
            status=declared.get("status", "completed"),
            artifacts=list(declared.get("artifacts", [])),
            metadata=dict(declared.get("metadata", {})),
        )


# ── 用例加载与执行 ──────────────────────────────────────────────


@dataclass
class CaseOutcome:
    case_id: str
    tier: str
    ok: bool
    duration: float
    model_calls: int = 0
    tool_calls: List[str] = field(default_factory=list)
    invalid_calls: int = 0
    tool_accuracy: Optional[float] = None
    error: str = ""
    generated: bool = False
    heldout: bool = False


def _load_cases(
    selected: Optional[Iterable[str]] = None,
    tier: str = "all",
    include_heldout: bool = False,
) -> List[Dict[str, Any]]:
    """合并加载 JSON 用例与 case pack 生成用例。

    - ID 全局唯一：JSON 与 case pack 之间撞 id 直接报错；
    - ``heldout=true`` 的用例默认不加载，仅 ``include_heldout=True`` 时进入选集，
      用于定期换血/校准，不参与常规门禁。
    """
    wanted = set(selected or [])
    cases: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}

    def _register(case: Dict[str, Any], source: str) -> None:
        case_id = str(case.get("id", ""))
        if not case_id:
            raise SystemExit(f"评测用例缺少 id（来源: {source}）")
        if case_id in seen:
            raise SystemExit(
                f"评测用例 id 重复: {case_id}（{seen[case_id]} 与 {source}）"
            )
        seen[case_id] = source

    for path in sorted(glob.glob(str(CASES_DIR / "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            case = json.load(f)
        case["_path"] = path
        _register(case, path)
        cases.append(case)

    from evals import case_packs

    for case in case_packs.load_cases():
        case = dict(case)
        case["_generated"] = True
        _register(case, "case_packs")
        cases.append(case)

    filtered: List[Dict[str, Any]] = []
    for case in cases:
        if case.get("heldout") and not include_heldout:
            continue
        if wanted and case.get("id") not in wanted:
            continue
        if tier != "all" and case.get("tier") != tier:
            continue
        filtered.append(case)
    if wanted:
        found = {c.get("id") for c in filtered}
        missing = wanted - found
        if missing:
            raise SystemExit(f"未找到指定用例: {', '.join(sorted(missing))}")
    return filtered


def _to_event(spec: Dict[str, Any]):
    from floodmind.agent.native.types import InvalidToolCall, ModelEvent, TerminalReason
    from floodmind.agent.runtime.contracts.tools import ToolCall

    event_type = spec["type"]
    kwargs: Dict[str, Any] = {
        "type": event_type,
        "content": str(spec.get("content", "")),
    }
    if event_type == "usage" and isinstance(spec.get("usage"), dict):
        kwargs["content"] = json.dumps(spec["usage"])
    if event_type == "tool_call_done":
        raw_call = spec["tool_call"]
        kwargs["tool_call"] = ToolCall(
            id=raw_call["id"],
            name=raw_call["name"],
            arguments=raw_call.get("arguments", {}),
        )
    if event_type == "invalid_tool_call":
        raw_call = spec["invalid_tool_call"]
        kwargs["invalid_tool_call"] = InvalidToolCall(
            id=raw_call["id"],
            name=raw_call["name"],
            raw_arguments=str(raw_call.get("raw_arguments", "")),
            error=str(raw_call.get("error", "")),
        )
    if spec.get("terminal_reason"):
        kwargs["terminal_reason"] = TerminalReason.from_raw(spec["terminal_reason"])
    return ModelEvent(**kwargs)


def _flatten_messages(messages: List[Dict[str, Any]]) -> str:
    return json.dumps(messages, ensure_ascii=False, default=str)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check_expectations(
    expect: Dict[str, Any],
    result: Any,
    model_client: ScriptedModelClient,
    tool_executor: Any,
    workspace: Optional[Path] = None,
) -> None:
    for text in expect.get("final_output_contains", []):
        _assert(str(text) in result.final_output, f"最终答案缺少 {text!r}；实际: {result.final_output!r}")
    for text in expect.get("final_output_not_contains", []):
        _assert(str(text) not in result.final_output, f"最终答案不应包含 {text!r}；实际: {result.final_output!r}")
    if "final_output_equals" in expect:
        _assert(
            result.final_output == expect["final_output_equals"],
            f"最终答案不匹配；期望: {expect['final_output_equals']!r}；实际: {result.final_output!r}",
        )
    if "model_calls" in expect:
        _assert(
            len(model_client.calls) == expect["model_calls"],
            f"模型调用次数期望 {expect['model_calls']}，实际 {len(model_client.calls)}",
        )
    if "tool_calls" in expect:
        actual_names = [call.name for call in tool_executor.calls]
        _assert(
            actual_names == expect["tool_calls"],
            f"工具调用序列期望 {expect['tool_calls']}，实际 {actual_names}",
        )
    if "tool_statuses" in expect:
        actual_statuses = [r.status for r in result.tool_results]
        _assert(
            actual_statuses == expect["tool_statuses"],
            f"工具结果状态期望 {expect['tool_statuses']}，实际 {actual_statuses}",
        )
    if "invalid_calls" in expect:
        actual_invalid = sum(1 for r in result.tool_results if r.status == "error")
        _assert(
            actual_invalid == expect["invalid_calls"],
            f"失败工具调用数期望 {expect['invalid_calls']}，实际 {actual_invalid}",
        )
    if "is_timeout" in expect:
        _assert(bool(result.is_timeout) == bool(expect["is_timeout"]), "is_timeout 与期望不一致")
    for rel_path, expected_text in expect.get("workspace_contains", {}).items():
        _assert(workspace is not None, "workspace_contains 只能用于真实工具用例")
        target = (workspace / rel_path).resolve()
        _assert(
            target == workspace.resolve() or workspace.resolve() in target.parents,
            f"workspace_contains 路径越界: {rel_path}",
        )
        _assert(target.is_file(), f"workspace 中缺少文件: {rel_path}")
        actual_text = target.read_text(encoding="utf-8")
        _assert(
            str(expected_text) in actual_text,
            f"workspace 文件 {rel_path} 缺少期望内容 {expected_text!r}；实际: {actual_text!r}",
        )
    for rel_path in expect.get("workspace_not_contains", []):
        _assert(workspace is not None, "workspace_not_contains 只能用于真实工具用例")
        target = (workspace / rel_path).resolve()
        _assert(
            target == workspace.resolve() or workspace.resolve() in target.parents,
            f"workspace_not_contains 路径越界: {rel_path}",
        )
        _assert(not target.exists(), f"workspace 不应存在文件却存在: {rel_path}")
    for item in expect.get("prompt_contains", []):
        index = int(item.get("call_index", 0))
        _assert(
            0 <= index < len(model_client.calls),
            f"prompt_contains 引用了第 {index + 1} 次模型调用，实际只有 {len(model_client.calls)} 次",
        )
        actual = _flatten_messages(model_client.calls[index]["messages"])
        _assert(str(item["contains"]) in actual, f"第 {index + 1} 次模型调用的上下文缺少 {item['contains']!r}")
    for item in expect.get("prompt_not_contains", []):
        index = int(item.get("call_index", 0))
        _assert(
            0 <= index < len(model_client.calls),
            f"prompt_not_contains 引用了第 {index + 1} 次模型调用，实际只有 {len(model_client.calls)} 次",
        )
        actual = _flatten_messages(model_client.calls[index]["messages"])
        _assert(str(item["contains"]) not in actual, f"第 {index + 1} 次模型调用的上下文不应包含 {item['contains']!r}")


def _write_workspace_files(workspace: Path, files: Dict[str, str]) -> None:
    """把用例声明的确定性 fixture 写进临时 workspace（拒绝路径越界）。"""
    root = workspace.resolve()
    for rel_path, content in files.items():
        target = (workspace / rel_path).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"workspace_files 路径越界: {rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")


def _tool_metrics(case: Dict[str, Any], result: Any, tool_executor: Any) -> tuple[int, Optional[float]]:
    """轨迹指标：失败调用数 + 工具准确率（期望序列与实际序列一致）。"""
    invalid_calls = sum(1 for item in result.tool_results if item.status == "error")
    expected = case.get("expect", {}).get("tool_calls")
    if expected is None:
        return invalid_calls, None
    actual = [call.name for call in tool_executor.calls]
    return invalid_calls, 1.0 if actual == expected else 0.0


def run_case(case: Dict[str, Any]) -> CaseOutcome:
    from floodmind.agent.native.event_bus import EventBus
    from floodmind.agent.native.executor import NativeAgentExecutor
    from floodmind.agent.native.message_builder import MessageBuilder
    from floodmind.agent.native.types import RunContext
    from floodmind.agent.runtime.contracts.tools import ToolSpec

    case_id = str(case["id"])
    tier = str(case.get("tier", "regression"))
    real_tools = str(case.get("tool_mode", "scripted")) == "real"
    # 运行模式（阶段E）：planning=只读硬门；默认 execution。
    mode = str(case.get("mode", "execution") or "execution")
    started = time.monotonic()

    rounds = [[_to_event(event) for event in r.get("events", [])] for r in case["rounds"]]
    model_client = ScriptedModelClient(rounds)

    tool_defs = case.get("tools", {})
    if real_tools:
        from evals.real_tools import RealToolExecutor, WorkspacePathService, load_real_tools

        specs = load_real_tools(list(tool_defs))
        tool_executor: Any = RealToolExecutor()
    else:
        specs = {
            name: ToolSpec(
                name=name,
                description=str(definition.get("description", name)),
                parameters=definition.get("parameters", {"type": "object", "properties": {}}),
                func=lambda **_: "",
            )
            for name, definition in tool_defs.items()
        }
        tool_outputs = {name: definition.get("result", {}) for name, definition in tool_defs.items()}
        # 按调用 id 声明的结果优先于按工具名声明的结果（同名工具不同结果）。
        tool_outputs.update(case.get("tool_results", {}))
        tool_executor = ScriptedToolExecutor(tool_outputs)
    registry = _EvalToolRegistry(specs)

    workdir = tempfile.mkdtemp(prefix=f"floodmind-eval-{case_id}-")
    workspace = Path(workdir)
    try:
        _write_workspace_files(workspace, case.get("workspace_files", {}))
        context = RunContext(
            session_id=f"eval-{case_id}",
            user_text=str(case.get("user_text", "")),
            output_dir=workdir,
            upload_dir=workdir,
            cwd=workdir,
            workspace_dir=workdir,
            state_dir=str(workspace / "state"),
            max_total_tokens=int(case.get("max_total_tokens", 0) or 0),
            mode=mode,
            runtime_context=(
                SimpleNamespace(path_service=WorkspacePathService(workdir))
                if real_tools else None
            ),
        )
        if "abort_after_model_calls" in case:
            limit = int(case["abort_after_model_calls"])
            context.abort_check = lambda: len(model_client.calls) >= limit

        executor = NativeAgentExecutor(
            model_client=model_client,
            tool_executor=tool_executor,
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=int(case.get("max_iterations", 10)),
            system_prompt=str(case.get("system_prompt", "FloodMind internal eval")),
            tools_schema=registry.tools_schema(),
            tool_registry=registry,
        )
        if mode == "execution":
            result = executor.run(context, context.user_text)
        else:
            # planning 用例：执行器在工具阶段会用 AgentLoopState.mode 覆盖 context.mode，
            # 因此必须从初始状态驱动并显式声明 mode，planning 硬门才会在真实工具层生效。
            initial_state = executor._build_initial_state(
                context=context,
                user_text=context.user_text,
                attachments=None,
                memory_messages=None,
                abort_check=None,
            )
            initial_state.mode = mode
            result = executor.run_from_state(context, initial_state)
        _check_expectations(case.get("expect", {}), result, model_client, tool_executor, workspace)
        invalid_calls, tool_accuracy = _tool_metrics(case, result, tool_executor)
    except Exception as exc:  # 用例失败必须报告，而不是中断整个评测
        return CaseOutcome(
            case_id=case_id,
            tier=tier,
            ok=False,
            duration=time.monotonic() - started,
            model_calls=len(model_client.calls),
            tool_calls=[call.name for call in tool_executor.calls],
            error=f"{type(exc).__name__}: {exc}",
            generated=bool(case.get("_generated")),
            heldout=bool(case.get("heldout")),
        )
    finally:
        if real_tools:
            # SESSION_CONTEXT 是 contextvar；清理避免泄漏到下一个用例。
            from floodmind.tools.session_context import set_session_context

            set_session_context("")
        shutil.rmtree(workdir, ignore_errors=True)

    return CaseOutcome(
        case_id=case_id,
        tier=tier,
        ok=True,
        duration=time.monotonic() - started,
        model_calls=len(model_client.calls),
        tool_calls=[call.name for call in tool_executor.calls],
        invalid_calls=invalid_calls,
        tool_accuracy=tool_accuracy,
        generated=bool(case.get("_generated")),
        heldout=bool(case.get("heldout")),
    )


# ── CLI ─────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    # Windows 控制台默认可能是 cp936；评测报告统一按 UTF-8 输出，避免中文乱码。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="FloodMind 内部评测集执行器（G-14）")
    parser.add_argument("--tier", choices=(*SUPPORTED_TIERS, "all"), default="all", help="只跑指定层级")
    parser.add_argument("--case", action="append", dest="cases", help="只跑指定用例 ID（可重复）")
    parser.add_argument(
        "--heldout",
        action="store_true",
        help="加载并运行 held-out 校准用例（默认不加载；与 --tier 过滤叠加）",
    )
    parser.add_argument("--json", dest="json_path", help="把机器可读报告写到指定路径")
    args = parser.parse_args(argv)

    cases = _load_cases(args.cases, args.tier, include_heldout=args.heldout)
    if not cases:
        print("没有匹配的评测用例。", file=sys.stderr)
        return 2
    # 全量门禁必须同时覆盖四个常规层：删空任一层都不应让门禁悄悄变绿。
    # held-out 集是校准用例，不参与常规非空门禁。
    if not args.cases and args.tier == "all":
        for tier in SUPPORTED_TIERS:
            if not any(case.get("tier") == tier for case in cases):
                print(f"评测集缺少 {tier} 层用例，门禁拒绝通过。", file=sys.stderr)
                return 2

    outcomes = [run_case(case) for case in cases]

    for outcome in outcomes:
        status = "PASS" if outcome.ok else "FAIL"
        tool_text = ",".join(outcome.tool_calls) or "-"
        accuracy_text = (
            f"  tool_accuracy={outcome.tool_accuracy:.2f}"
            if outcome.tool_accuracy is not None else ""
        )
        print(
            f"[{outcome.tier:<10}] {outcome.case_id:<36} {status}  "
            f"{outcome.duration:.2f}s  model_calls={outcome.model_calls} "
            f"tools={tool_text} invalid_calls={outcome.invalid_calls}{accuracy_text}"
        )
        if not outcome.ok:
            print(f"    {outcome.error}")

    passed = sum(1 for outcome in outcomes if outcome.ok)
    failed = len(outcomes) - passed
    print(f"\nSummary: {passed}/{len(outcomes)} passed, {failed} failed")
    for tier in SUPPORTED_TIERS:
        tier_outcomes = [o for o in outcomes if o.tier == tier]
        if tier_outcomes:
            tier_passed = sum(1 for o in tier_outcomes if o.ok)
            print(f"  {tier}: {tier_passed}/{len(tier_outcomes)}")

    if args.json_path:
        scored = [o for o in outcomes if o.tool_accuracy is not None]
        report = {
            "total": len(outcomes),
            "passed": passed,
            "failed": failed,
            "metrics": {
                "model_calls": sum(o.model_calls for o in outcomes),
                "tool_calls": sum(len(o.tool_calls) for o in outcomes),
                "invalid_calls": sum(o.invalid_calls for o in outcomes),
                "tool_accuracy": (
                    round(sum(o.tool_accuracy for o in scored) / len(scored), 4)
                    if scored else None
                ),
            },
            "tiers": {
                tier: {
                    "total": len([o for o in outcomes if o.tier == tier]),
                    "passed": len([o for o in outcomes if o.tier == tier and o.ok]),
                }
                for tier in SUPPORTED_TIERS
            },
            "cases": [
                {
                    "id": o.case_id,
                    "tier": o.tier,
                    "ok": o.ok,
                    "generated": o.generated,
                    "heldout": o.heldout,
                    "duration_seconds": round(o.duration, 4),
                    "model_calls": o.model_calls,
                    "tool_calls": o.tool_calls,
                    "invalid_calls": o.invalid_calls,
                    "tool_accuracy": o.tool_accuracy,
                    "error": o.error,
                }
                for o in outcomes
            ],
        }
        Path(args.json_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
