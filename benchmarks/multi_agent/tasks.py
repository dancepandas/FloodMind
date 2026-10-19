"""G-22 对比任务集与 fixture 生成。

任务声明与执行逻辑分离：每个任务只描述输入、fixture、成功判定与拆分预期，
运行器（``runner.py``）负责把任务放进独立 workspace 并调用策略执行器。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

SPLIT_BENEFICIAL = "split_beneficial"
SPLIT_NOT_BENEFICIAL = "split_not_beneficial"


@dataclass
class TaskSuccess:
    """任务成功判定：回答内容 + 产物文件内容。"""

    answer_contains_all: Tuple[str, ...] = ()
    answer_contains_any: Tuple[str, ...] = ()
    answer_regex_all: Tuple[str, ...] = ()
    workspace_contains: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    workspace_not_contains: Dict[str, Tuple[str, ...]] = field(default_factory=dict)

    def evaluate(self, answer: str, workspace_files: Mapping[str, Optional[str]]) -> Tuple[str, ...]:
        """返回失败原因列表；空列表表示成功。"""
        failures: List[str] = []
        for text in self.answer_contains_all:
            if text not in answer:
                failures.append(f"回答缺少 {text!r}")
        if self.answer_contains_any and not any(text in answer for text in self.answer_contains_any):
            failures.append("回答未命中任一期望关键词: " + ", ".join(repr(t) for t in self.answer_contains_any))
        for pattern in self.answer_regex_all:
            if re.search(pattern, answer) is None:
                failures.append(f"回答不匹配正则 {pattern!r}")
        for rel_path, texts in self.workspace_contains.items():
            content = workspace_files.get(rel_path)
            if content is None:
                failures.append(f"缺少产物文件 {rel_path}")
                continue
            for text in texts:
                if text not in content:
                    failures.append(f"{rel_path} 缺少 {text!r}")
        for rel_path, texts in self.workspace_not_contains.items():
            content = workspace_files.get(rel_path)
            if content is None:
                continue
            for text in texts:
                if text in content:
                    failures.append(f"{rel_path} 不应包含 {text!r}")
        return tuple(failures)

    def required_paths(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys([*self.workspace_contains, *self.workspace_not_contains]))


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    title: str
    category: str
    rationale: str
    prompt: str
    setup: Callable[[Path], None]
    success: TaskSuccess
    max_iterations: int = 30


# ── fixtures ────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _setup_station_audit(workspace: Path) -> None:
    _write(
        workspace / "data" / "station_alpha.txt",
        "station=Alpha\nriver=Qing\nlatest_level=12.40\nstatus=normal\n",
    )
    _write(
        workspace / "data" / "station_beta.txt",
        "station=Beta\nriver=Qing\nlatest_level=15.80\nstatus=warning\n",
    )
    _write(
        workspace / "data" / "station_gamma.txt",
        "station=Gamma\nriver=Qing\nlatest_level=9.15\nstatus=normal\n",
    )


def _setup_log_triage(workspace: Path) -> None:
    error_at = {5: "E102", 23: "E102", 41: "E102", 59: "E205", 77: "E205", 95: "E301"}
    lines: List[str] = []
    for index in range(1200):
        stamp = f"2026-04-01T{index // 60:02d}:{index % 60:02d}Z"
        code = error_at.get(index)
        if code is not None:
            lines.append(f"{stamp} ERROR {code} simulated failure in module {index % 7}")
        elif index % 11 == 0:
            lines.append(f"{stamp} WARN queue depth {index % 50}")
        else:
            lines.append(f"{stamp} INFO request {index} completed")
    _write(workspace / "logs" / "app.log", "\n".join(lines) + "\n")


def _setup_single_file_fix(workspace: Path) -> None:
    _write(
        workspace / "calc.py",
        "def add(a, b):\n"
        "    return a - b\n"
        "\n"
        "\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
    )


def _setup_report_from_context(workspace: Path) -> None:
    _write(
        workspace / "notes.md",
        "# 事故记录\n"
        "\n"
        "- 时间：2026-03-04 02:10\n"
        "- 站点：Qinghe\n"
        "- 峰值水位：16.72 米\n"
        "- 根因：遥测网关证书过期\n"
        "- 处置：切换备用网关并更新证书\n"
        "- 影响：3 个测站数据中断 42 分钟\n",
    )


# ── 任务集 ──────────────────────────────────────────────────────


TASKS: Tuple[BenchmarkTask, ...] = (
    BenchmarkTask(
        id="station-audit",
        title="三站点独立巡检",
        category=SPLIT_BENEFICIAL,
        rationale="三个站点文件互不依赖，可并行读取；拆分可压缩父上下文占用与墙钟时间。",
        prompt=(
            "读取 data/ 目录下的三个站点文件，逐个报告站点名与最新水位，"
            "最后用一行说明是否存在非 normal 状态的站点。全部基于文件内容，不要编造。"
        ),
        setup=_setup_station_audit,
        success=TaskSuccess(
            answer_contains_all=("Alpha", "12.40", "Beta", "15.80", "Gamma", "9.15"),
        ),
    ),
    BenchmarkTask(
        id="log-triage",
        title="大日志错误统计",
        category=SPLIT_BENEFICIAL,
        rationale=(
            "日志有 1200 行，完整读入父上下文会挤占预算；子代理在独立上下文里统计"
            "后只回传聚合结果。"
        ),
        prompt=(
            "分析 logs/app.log，统计每个不同的 ERROR 代码（形如 E 加数字）出现的次数，"
            "输出代码与次数。必须覆盖所有代码，不要遗漏。"
        ),
        setup=_setup_log_triage,
        success=TaskSuccess(
            answer_regex_all=(
                r"E102\D{0,10}3",
                r"E205\D{0,10}2",
                r"E301\D{0,10}1",
            ),
        ),
    ),
    BenchmarkTask(
        id="single-file-fix",
        title="单文件小修复",
        category=SPLIT_NOT_BENEFICIAL,
        rationale=(
            "改动范围只有一个函数，完整上下文很小；委派的交接与汇总开销高于直接修改。"
        ),
        prompt=(
            "修复 calc.py 中 add 函数的错误，使 test_add 通过。只修改必要的代码行，"
            "并在回答中说明修改了什么。"
        ),
        setup=_setup_single_file_fix,
        success=TaskSuccess(
            answer_contains_any=("a + b", "add"),
            workspace_contains={"calc.py": ("return a + b",)},
            workspace_not_contains={"calc.py": ("return a - b",)},
        ),
    ),
    BenchmarkTask(
        id="report-from-context",
        title="上下文密集报告",
        category=SPLIT_NOT_BENEFICIAL,
        rationale=(
            "所有事实都必须原样保留在同一上下文；拆分会让子代理只看到片段，"
            "增加遗漏或编造风险。"
        ),
        prompt=(
            "根据 notes.md 写一份事故摘要到 report.md，必须包含时间、站点、峰值水位、"
            "根因、影响时长这五项信息；不要添加 notes.md 中没有的内容。完成后在回答中确认。"
        ),
        setup=_setup_report_from_context,
        success=TaskSuccess(
            answer_contains_any=("report.md", "已完成", "已写入"),
            workspace_contains={
                "report.md": (
                    "2026-03-04 02:10",
                    "Qinghe",
                    "16.72",
                    "证书过期",
                    "42 分钟",
                )
            },
        ),
    ),
)

TASKS_BY_ID: Dict[str, BenchmarkTask] = {task.id: task for task in TASKS}
