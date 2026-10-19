"""
metrics_report.py — 离线 trace 用量/成本报告。

读取 <base_dir>/<session_id>/trace.jsonl，按 run / 模型 / 步骤 / 工具下钻
聚合已完成 span 的 token 用量与成本，输出可读文本或 JSON。

用法::

    python scripts/metrics_report.py --session-id <sid> [--base-dir data/sessions]
                                     [--pricing-json path] [--json]

定价优先级：--pricing-json 指定文件 > floodmind 配置 settings.model_pricing。
trace 文件不存在或为空时明确报错并以退出码 2 退出，不伪造零值。
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# floodmind 包在运行时才导入（见 _load_default_pricing），避免脚本被其他
# 工具以 -I 隔离模式导入时产生配置加载副作用。

EXIT_ERROR = 2


class ReportError(Exception):
    """报告生成失败（文件缺失/为空/定价非法），main 捕获后以退出码 2 退出。"""


def _parse_trace(trace_path: Path) -> List[Any]:
    """逐行解析 trace.jsonl 为 TraceSpan / TraceEvent 对象。"""
    from floodmind.agent.runtime.contracts.tracing import TraceEvent, TraceSpan

    items: List[Any] = []
    for line_no, line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            if "span_id" in record:
                items.append(TraceSpan.model_validate(record))
            else:
                items.append(TraceEvent.model_validate(record))
        except Exception as exc:  # noqa: BLE001 — 单行坏数据不应中断整份报告
            print(f"警告：第 {line_no} 行解析失败，已跳过: {exc}", file=sys.stderr)
    return items


def _load_pricing(cli_pricing_json: Optional[str]) -> Dict[str, Any]:
    """加载定价表：优先 --pricing-json，否则取 floodmind 配置。"""
    if cli_pricing_json:
        path = Path(cli_pricing_json)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ReportError(f"错误：无法读取定价文件 {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ReportError(f"错误：定价文件 {path} 必须是 JSON 对象（{{model: {{...}}}}）")
        return raw
    # 运行时才导入配置，避免脚本导入期副作用
    from floodmind.config.settings import settings

    return dict(settings.model_pricing)


def _format_text(session_id: str, breakdown: Dict[str, Any]) -> str:
    totals = breakdown["totals"]
    lines = [f"会话 {session_id} 用量报告", ""]
    lines.append("== 汇总 ==")
    lines.append(f"  输入 token:   {totals['input_tokens']}")
    lines.append(f"  输出 token:   {totals['output_tokens']}")
    lines.append(f"  合计 token:   {totals['total_tokens']}")
    if totals["estimated_cost"] is None:
        lines.append("  估算成本:     未配置价格")
    else:
        lines.append(
            f"  估算成本:     {totals['estimated_cost']:.6f} {totals['currency'] or ''}".rstrip()
        )
    if totals["unpriced_models"]:
        lines.append(f"  未定价模型:   {', '.join(totals['unpriced_models'])}")

    def _bucket_block(title: str, table: Dict[str, Dict[str, Any]]) -> None:
        lines.append("")
        lines.append(f"== {title} ==")
        if not table:
            lines.append("  （无）")
            return
        for key, bucket in sorted(table.items()):
            cost = bucket.get("estimated_cost")
            cost_str = (
                f", 成本 {cost:.6f} {bucket['currency'] or ''}".rstrip()
                if bucket.get("currency") is not None
                else ""
            )
            lines.append(
                f"  {key}: 调用 {bucket['llm_calls']}, 输入 {bucket['input_tokens']}, "
                f"输出 {bucket['output_tokens']}, 合计 {bucket['total_tokens']}{cost_str}"
            )

    _bucket_block("按模型", breakdown["by_model"])
    _bucket_block("按 Run", breakdown["by_run"])
    _bucket_block("按步骤", breakdown["by_step"])

    lines.append("")
    lines.append("== 按工具 ==")
    if not breakdown["by_tool"]:
        lines.append("  （无）")
    else:
        for name, entry in sorted(breakdown["by_tool"].items()):
            d = entry["duration_ms"]
            lines.append(
                f"  {name}: 调用 {entry['calls']}, 错误 {entry['errors']}, "
                f"耗时 avg {d['avg_ms']}ms / p95 {d['p95_ms']}ms / max {d['max_ms']}ms"
            )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="离线 trace 用量/成本报告")
    parser.add_argument("--session-id", required=True, help="会话 ID")
    parser.add_argument("--base-dir", default="data/sessions", help="会话数据根目录")
    parser.add_argument("--pricing-json", default=None, help="定价 JSON 文件路径（优先于全局配置）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    trace_path = Path(args.base_dir) / args.session_id / "trace.jsonl"
    if not trace_path.exists():
        print(f"错误：trace 文件不存在: {trace_path}", file=sys.stderr)
        return EXIT_ERROR

    items = _parse_trace(trace_path)
    if not items:
        print(f"错误：trace 文件为空或全部为坏行: {trace_path}", file=sys.stderr)
        return EXIT_ERROR

    from floodmind.agent.runtime.services.usage_accounting import (
        aggregate_usage,
        normalize_pricing,
    )

    try:
        pricing = normalize_pricing(_load_pricing(args.pricing_json))
    except ValueError as exc:
        print(f"错误：定价配置非法: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except ReportError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    breakdown = aggregate_usage(items, pricing)

    if args.json:
        print(json.dumps(breakdown, ensure_ascii=False, indent=2))
    else:
        print(_format_text(args.session_id, breakdown))
    return 0


if __name__ == "__main__":
    sys.exit(main())
