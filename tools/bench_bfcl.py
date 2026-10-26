"""BFCL（Berkeley Function Calling Leaderboard）适配器：用真实模型验证 FloodMind 的
工具调用准确性。

BFCL 把"函数调用"拆成四个可客观判分的维度，正好覆盖 harness 的对外契约：
- simple：单函数单调用，参数值正确；
- multiple：同一轮多函数调用，调用集合与参数都正确；
- parallel：同一函数多次调用（并行语义），每次参数都正确；
- irrelevance：给定函数与问题无关，必须不调用任何函数。

评测只跑 bare runtime（宿主注入函数 = BFCL 函数），不掺入内置工具，因此分数
衡量的是"FloodMind 把函数 schema 交给模型、解析模型工具调用、执行并回报"的整条
链路，而不是模型单独的能力。

数据来自 BFCL v3（HuggingFace 镜像 gorilla-llm/Berkeley-Function-Calling-Leaderboard），
下载后放在独立目录，按 ``--data-dir`` 传入。密钥只从环境变量读取::

    FM_BASE=... FM_KEY=... FM_MODEL=... python tools/bench_bfcl.py \
        --data-dir /path/to/bfcl --categories simple,multiple --limit 20 --out report.json

评分规则与官方 AST checker 对齐：
- 函数名必须匹配；
- ground truth 里列出的每个参数都必须出现，且取值命中候选值之一
  （候选值含空串表示该参数可省略）；
- 参数比较忽略字符串大小写与首尾空白，数值按数值比较；
- 多调用类别按"调用集合"一一配对，多调或少调都算错；
- 模型给出的额外参数不参与判分，但会记录在报告里。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

os.environ.setdefault("FLOODMIND_HOME", tempfile.mkdtemp(prefix="floodmind-bfcl-home-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from floodmind.agent.api import Agent
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.runtime.contracts.tools import ToolSpec
from floodmind.agent.runtime.contracts.workspace import Workspace

BASE = os.environ.get("FM_BASE", "")
KEY = os.environ.get("FM_KEY", "")
MODEL = os.environ.get("FM_MODEL", "gpt-5.6-sol")
CONTEXT_WINDOW = (
    int(os.environ["FM_CONTEXT_WINDOW"]) if os.environ.get("FM_CONTEXT_WINDOW") else None
)

_CATEGORY_FILES = {
    "simple": ("BFCL_v3_simple.json", "ans_BFCL_v3_simple.json"),
    "multiple": ("BFCL_v3_multiple.json", "ans_BFCL_v3_multiple.json"),
    "parallel": ("BFCL_v3_parallel.json", "ans_BFCL_v3_parallel.json"),
    "irrelevance": ("BFCL_v3_irrelevance.json", None),
}

_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to function tools. "
    "When the user's request can be fulfilled by a function, call it with correct arguments. "
    "Do not ask clarifying questions when the needed arguments are stated."
)


# ── 数据加载 ──────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except ValueError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def load_categories(data_dir: Path, categories: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """返回 ``{category: [case, ...]}``；case 上挂好 ``ground_truth``。"""
    result: Dict[str, List[Dict[str, Any]]] = {}
    for category in categories:
        data_name, answer_name = _CATEGORY_FILES[category]
        data_path = data_dir / data_name
        if not data_path.exists():
            raise FileNotFoundError(f"缺少数据文件: {data_path}")
        cases = _load_jsonl(data_path)
        answers: Dict[str, Any] = {}
        if answer_name:
            answer_path = data_dir / answer_name
            if not answer_path.exists():
                raise FileNotFoundError(f"缺少标注文件: {answer_path}")
            for item in _load_jsonl(answer_path):
                answers[item["id"]] = item.get("ground_truth", [])
        for case in cases:
            case["ground_truth"] = answers.get(case["id"], [])
        result[category] = cases
    return result


def user_text(case: Dict[str, Any]) -> str:
    """把 BFCL 的多轮 question 压平成最后一条用户输入。"""
    parts: List[str] = []
    for turn in case.get("question", []):
        for message in turn:
            if message.get("role") == "user":
                parts.append(str(message.get("content", "")))
    return "\n".join(parts)


# ── schema 与工具构造 ─────────────────────────────────────────────────

_BFCL_TYPE_MAP = {
    "dict": "object",
    "list": "array",
    "tuple": "array",
    "float": "number",
    "any": None,
}


def _sanitize_type(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize_type(item) for item in value]
    if isinstance(value, str):
        return _BFCL_TYPE_MAP.get(value, value)
    return value


def normalize_schema(schema: Any) -> Dict[str, Any]:
    """把 BFCL 的非标准 JSON Schema 转成标准 JSON Schema。

    BFCL v3 使用 ``dict``/``float``/``tuple`` 这类 Python 类型名，jsonschema 会拒绝。
    ``any`` 直接去掉 type，避免误判。
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    normalized: Dict[str, Any] = {}
    for key, value in schema.items():
        if key == "type":
            mapped = _sanitize_type(value)
            if mapped is None:
                continue
            normalized[key] = mapped
        elif key in ("properties",) and isinstance(value, dict):
            normalized[key] = {
                name: normalize_schema(spec) for name, spec in value.items()
            }
        elif key == "items":
            if isinstance(value, list):
                normalized[key] = [normalize_schema(item) for item in value]
            else:
                normalized[key] = normalize_schema(value)
        else:
            normalized[key] = value
    if "properties" in normalized and "type" not in normalized:
        normalized["type"] = "object"
    if normalized.get("type") == "array" and "items" not in normalized:
        normalized["items"] = {}
    return normalized


def tool_name_for(original: str) -> str:
    """OpenAI function name 只允许字母数字下划线连字符，且不超过 64 字符。"""
    name = original.replace(".", "__").replace("/", "__").replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    if len(name) > 64:
        name = name[:56] + "__" + str(abs(hash(original)) % 10**7)
    return name


def build_tools(functions: Sequence[Dict[str, Any]]) -> Tuple[List[ToolSpec], Dict[str, str]]:
    """BFCL function → ToolSpec。

    函数体只回报成功；判分以事件流里记录的调用为准，不依赖工具返回值。
    """
    specs: List[ToolSpec] = []
    name_map: Dict[str, str] = {}
    used: set = set()
    for function in functions:
        original = str(function.get("name", ""))
        sanitized = tool_name_for(original)
        if sanitized in used:
            suffix = 2
            while f"{sanitized}_{suffix}"[:64] in used:
                suffix += 1
            sanitized = f"{sanitized}_{suffix}"[:64]
        used.add(sanitized)
        name_map[sanitized] = original
        parameters = normalize_schema(function.get("parameters"))

        def _fake(_original=original, **_kwargs):
            return json.dumps({"status": "ok", "function": _original}, ensure_ascii=False)

        specs.append(
            ToolSpec(
                name=sanitized,
                description=str(function.get("description", "")),
                parameters=parameters,
                func=_fake,
                is_readonly=True,
            )
        )
    return specs, name_map


# ── 判分 ──────────────────────────────────────────────────────────────

def _scalar_equal(model_value: Any, expected: Any) -> bool:
    if isinstance(expected, bool) or isinstance(model_value, bool):
        return expected is model_value
    if isinstance(expected, (int, float)) and isinstance(model_value, (int, float)):
        return abs(float(expected) - float(model_value)) <= 1e-6
    if isinstance(expected, str) and isinstance(model_value, str):
        return model_value.strip().lower() == expected.strip().lower()
    return model_value == expected


def _deep_equal(model_value: Any, candidate: Any) -> bool:
    """候选值深比较：字典键值递归、数组逐元素、标量按类型归一。"""
    if isinstance(candidate, dict):
        if not isinstance(model_value, dict):
            return False
        for key, possible in candidate.items():
            if key not in model_value:
                if isinstance(possible, list) and "" in possible:
                    continue
                return False
            if not _values_match(model_value[key], possible):
                return False
        return True
    if isinstance(candidate, list):
        if not isinstance(model_value, list) or len(model_value) != len(candidate):
            return False
        return all(_scalar_equal(item, expected) for item, expected in zip(model_value, candidate))
    return _scalar_equal(model_value, candidate)


def _values_match(model_value: Any, possible: Any) -> bool:
    """``possible`` 是"可接受取值"列表，元素既可以是标量也可以是复合值。"""
    if isinstance(possible, list):
        return any(_deep_equal(model_value, candidate) for candidate in possible)
    return _deep_equal(model_value, possible)


def _call_matches(gt_call: Dict[str, Any], model_call: Dict[str, Any]) -> Optional[str]:
    """匹配单个 call；返回 None 表示匹配，否则返回不匹配原因。"""
    functions = list(gt_call.keys())
    if not functions:
        return None
    gt_name = functions[0]
    if model_call.get("name") != gt_name:
        return f"function {model_call.get('name')!r} != {gt_name!r}"
    expected = gt_call[gt_name]
    arguments = model_call.get("arguments") or {}
    if not isinstance(arguments, dict):
        return "arguments 不是对象"
    for key, possible in expected.items():
        if key not in arguments:
            if isinstance(possible, list) and "" in possible:
                continue
            return f"缺少参数 {key}"
        if not _values_match(arguments[key], possible):
            return f"参数 {key}={arguments[key]!r} 不在候选 {possible!r} 中"
    return None


def score_case(category: str, ground_truth: List[Dict[str, Any]], calls: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """返回 (是否通过, 失败原因)。"""
    if category == "irrelevance":
        if calls:
            return False, f"不应调用工具，实际调用 {[call['name'] for call in calls]}"
        return True, ""

    if len(calls) != len(ground_truth):
        return False, f"调用数量 {len(calls)} != 期望 {len(ground_truth)}"

    remaining = list(calls)
    for gt_call in ground_truth:
        for index, model_call in enumerate(remaining):
            if _call_matches(gt_call, model_call) is None:
                remaining.pop(index)
                break
        else:
            target = list(gt_call.keys())[0] if gt_call else "?"
            reasons = [
                _call_matches(gt_call, model_call) for model_call in remaining
            ]
            return False, f"没有调用匹配 {target}: {reasons}"
    return True, ""


# ── 执行 ──────────────────────────────────────────────────────────────

class _Recorder:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.usages: List[Dict[str, int]] = []
        self.errors: List[str] = []
        self.final: str = ""

    def on_event(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "action_end":
            arguments: Any = event.get("tool_input", "")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except ValueError:
                    arguments = {}
            self.calls.append({
                "name": event.get("tool_name", ""),
                "arguments": arguments,
                "status": event.get("status", ""),
                "content": str(event.get("content", ""))[:300],
            })
        elif kind == "token_usage":
            self.usages.append({
                key: int(event.get(key, 0) or 0)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            })
        elif kind == "final_text":
            self.final = str(event.get("content", ""))
        elif kind == "error":
            self.errors.append(str(event.get("content", "")))


def run_case(category: str, case: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix=f"fm-bfcl-{category}-"))
    workspace = Workspace.from_folder(root, session_id=f"bfcl-{case['id']}").ensure()

    specs, name_map = build_tools(case.get("function", []))
    recorder = _Recorder()
    client = ModelClient(
        api_key=KEY,
        base_url=BASE,
        model_name=MODEL,
        temperature=0.0,
        max_tokens=1024,
        timeout=120,
        context_window=CONTEXT_WINDOW,
    )
    agent = Agent(
        llm=client,
        bare=True,
        tools=specs,
        workspace=workspace,
        system_prompt=_SYSTEM_PROMPT,
        # BFCL 的设定是"所有函数 schema 随请求一次性给出"，用 eager 模式对齐；
        # progressive 下函数要经 GetTool 才可调用，会把加载开销混进调用准确性分数。
        tool_loading=False,
        max_iterations=1,
        on_event=recorder.on_event,
    )

    started = time.time()
    error = ""
    try:
        agent.run(user_text(case))
    except Exception as exc:  # noqa: BLE001 - 评测记录任何失败
        error = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.time() - started, 2)

    # 只保留 BFCL 函数调用；bare runtime 自带的 GetTool/task_* 不属于被测函数。
    tool_calls: List[Dict[str, Any]] = []
    auxiliary: List[str] = []
    for call in recorder.calls:
        original = name_map.get(call["name"])
        if original is None:
            auxiliary.append(str(call["name"]))
            continue
        item = dict(call)
        item["name"] = original
        tool_calls.append(item)

    passed, reason = score_case(category, case.get("ground_truth", []), tool_calls)
    if error:
        passed, reason = False, f"运行错误: {error}"

    usages = recorder.usages or []
    return {
        "id": case["id"],
        "category": category,
        "passed": passed,
        "reason": reason,
        "elapsed_s": elapsed,
        "llm_calls": len(usages),
        "tokens": sum(item.get("total_tokens", 0) for item in usages),
        "tool_calls": tool_calls,
        "call_statuses": [call.get("status", "") for call in tool_calls],
        "auxiliary_calls": auxiliary,
        "error": error,
    }


# ── CLI ───────────────────────────────────────────────────────────────

def rescore_report(report_path: Path, data_dir: Path) -> List[Dict[str, Any]]:
    """用当前判分规则重算已有报告（不重新调用模型）。"""
    rows = json.loads(report_path.read_text(encoding="utf-8"))
    categories = sorted({row["category"] for row in rows})
    data = load_categories(data_dir, categories)
    cases = {case["id"]: case for items in data.values() for case in items}
    for row in rows:
        case = cases[row["id"]]
        passed, reason = score_case(
            row["category"], case.get("ground_truth", []), row.get("tool_calls", [])
        )
        row["passed"], row["reason"] = passed, reason
    return rows


def _print_summary(rows: List[Dict[str, Any]], out_path: Optional[Path]) -> None:
    print("=" * 100)
    print("BFCL 工具调用评测（bare runtime：只暴露 BFCL 函数）")
    print("=" * 100)
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_category.setdefault(row["category"], []).append(row)
    for category, items in by_category.items():
        passed = sum(1 for item in items if item["passed"])
        tokens = sum(item["tokens"] for item in items)
        elapsed = sum(item["elapsed_s"] for item in items)
        print(
            f"{category:<12} {passed}/{len(items)} 通过  "
            f"{elapsed:>7.1f}s  {tokens:>8} tok"
        )
        for item in items:
            if not item["passed"]:
                print(f"    [FAIL] {item['id']}: {item['reason']}")
                print(f"           calls={json.dumps(item['tool_calls'], ensure_ascii=False)[:300]}")
    total = len(rows)
    passed = sum(1 for item in rows if item["passed"])
    print("-" * 100)
    print(f"总计: {passed}/{total} 通过（{passed / total * 100:.1f}%）" if total else "无样本")
    if out_path is not None:
        out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON 报告: {out_path}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="BFCL 工具调用评测")
    parser.add_argument("--data-dir", required=True, help="BFCL v3 数据目录")
    parser.add_argument(
        "--categories", default="simple,multiple,parallel,irrelevance",
        help="逗号分隔的类别",
    )
    parser.add_argument("--limit", type=int, default=20, help="每类样本数上限")
    parser.add_argument("--out", default="", help="JSON 报告路径")
    parser.add_argument("--rescore", default="", help="用当前规则重算已有 JSON 报告，不调用模型")
    args = parser.parse_args(argv)

    if args.rescore:
        rows = rescore_report(Path(args.rescore), Path(args.data_dir))
        out_path = Path(args.out) if args.out else None
        _print_summary(rows, out_path)
        return 0 if all(row["passed"] for row in rows) else 1

    if not BASE or not KEY:
        print("错误：需要 FM_BASE 与 FM_KEY 环境变量", file=sys.stderr)
        return 2

    categories = [item.strip() for item in args.categories.split(",") if item.strip()]
    unknown = [item for item in categories if item not in _CATEGORY_FILES]
    if unknown:
        print(f"错误：未知类别 {unknown}", file=sys.stderr)
        return 2

    data = load_categories(Path(args.data_dir), categories)
    rows: List[Dict[str, Any]] = []
    for category in categories:
        cases = data[category][: args.limit]
        for case in cases:
            row = run_case(category, case)
            rows.append(row)
            mark = "PASS" if row["passed"] else "FAIL"
            print(f"[{mark}] {category:<12} {row['id']:<24} {row['elapsed_s']:>5.1f}s")

    out_path = Path(args.out) if args.out else None
    _print_summary(rows, out_path)
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
