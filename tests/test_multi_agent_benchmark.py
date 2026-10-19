"""G-22 多代理收益基线的离线测试。"""

import json
from pathlib import Path

import pytest

from benchmarks.multi_agent.metrics import (
    Observation,
    TaskRun,
    build_task_run,
    compare_task,
    judge,
    summarize_categories,
)
from benchmarks.multi_agent.report import render_table, to_dict
from benchmarks.multi_agent.runner import (
    LiveStrategyExecutor,
    main,
    run_benchmark,
    select_tasks,
)
from benchmarks.multi_agent.tasks import (
    SPLIT_BENEFICIAL,
    SPLIT_NOT_BENEFICIAL,
    TASKS,
    TASKS_BY_ID,
    TaskSuccess,
)

_STATION_ANSWER = "Alpha 12.40；Beta 15.80（warning）；Gamma 9.15。"
_LOG_ANSWER = "E102: 3 次\nE205: 2 次\nE301: 1 次"
_FIX_ANSWER = "已修复 add 函数，将减法改为 a + b。"
_REPORT_ANSWER = "已写入 report.md。"

_FIXED_CALC = "def add(a, b):\n    return a + b\n"
_REPORT_MD = (
    "时间：2026-03-04 02:10\n站点：Qinghe\n峰值水位：16.72 米\n"
    "根因：遥测网关证书过期\n影响：3 个测站数据中断 42 分钟\n"
)


class FakeExecutor:
    """确定性假执行器：按策略返回不同成本，并写出任务要求的产物。"""

    def __init__(self):
        self.calls = []

    def execute(self, task, workspace, strategy):
        self.calls.append((task.id, strategy, workspace))
        if task.id == "station-audit":
            assert (workspace / "data" / "station_alpha.txt").is_file()
        if task.id == "log-triage":
            assert (workspace / "logs" / "app.log").is_file()
        if task.id == "single-file-fix":
            (workspace / "calc.py").write_text(_FIXED_CALC, encoding="utf-8")
        if task.id == "report-from-context":
            (workspace / "report.md").write_text(_REPORT_MD, encoding="utf-8")

        answers = {
            "station-audit": _STATION_ANSWER,
            "log-triage": _LOG_ANSWER,
            "single-file-fix": _FIX_ANSWER,
            "report-from-context": _REPORT_ANSWER,
        }
        beneficial = task.category == SPLIT_BENEFICIAL
        if strategy == "single":
            elapsed = 10.0
            tokens = 1000
            tools = ("Read", "Read", "Read") if task.id == "station-audit" else ("Read",)
        else:
            elapsed = 4.0 if beneficial else 12.0
            tokens = 600 if beneficial else 1500
            tools = ("SubAgent", "Read", "Read") if beneficial else ("SubAgent", "Read")
        return Observation(
            answer=answers[task.id],
            total_tokens=tokens,
            prompt_tokens=tokens - 100,
            completion_tokens=100,
            tool_calls=tools,
            elapsed_seconds=elapsed,
        )


class TestTaskSet:
    def test_task_set_size_and_category_coverage(self):
        assert 3 <= len(TASKS) <= 5
        categories = {task.category for task in TASKS}
        assert categories == {SPLIT_BENEFICIAL, SPLIT_NOT_BENEFICIAL}

    def test_task_ids_are_unique(self):
        ids = [task.id for task in TASKS]
        assert len(ids) == len(set(ids))
        assert set(ids) == set(TASKS_BY_ID)

    def test_fixtures_are_written(self, tmp_path):
        for task in TASKS:
            workspace = tmp_path / task.id
            workspace.mkdir()
            task.setup(workspace)
            assert any(workspace.rglob("*")), f"{task.id} 未生成 fixture"

    def test_log_fixture_has_expected_error_counts(self, tmp_path):
        TASKS_BY_ID["log-triage"].setup(tmp_path)
        text = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
        assert text.count("ERROR E102") == 3
        assert text.count("ERROR E205") == 2
        assert text.count("ERROR E301") == 1

    def test_success_evaluation_reports_missing_answer_and_file(self):
        success = TaskSuccess(
            answer_contains_all=("Alpha",),
            workspace_contains={"calc.py": ("return a + b",)},
            workspace_not_contains={"calc.py": ("return a - b",)},
        )
        failures = success.evaluate("Beta", {"calc.py": "return a - b"})
        assert any("Alpha" in item for item in failures)
        assert any("缺少" in item for item in failures)
        assert any("不应包含" in item for item in failures)

    def test_success_evaluation_passes_complete_result(self):
        success = TASKS_BY_ID["single-file-fix"].success
        failures = success.evaluate("已修复 add：a + b", {"calc.py": _FIXED_CALC})
        assert failures == ()

    def test_required_paths_deduplicates(self):
        success = TaskSuccess(
            workspace_contains={"a.txt": ("x",)},
            workspace_not_contains={"a.txt": ("y",), "b.txt": ("z",)},
        )
        assert success.required_paths() == ("a.txt", "b.txt")


class TestMetrics:
    def _run(self, strategy, success=True, tokens=1000, elapsed=10.0, tools=()):
        return build_task_run(
            strategy,
            Observation(
                answer="ok",
                total_tokens=tokens,
                elapsed_seconds=elapsed,
                tool_calls=tuple(tools),
            ),
            () if success else ("failed",),
        )

    def test_delegation_calls_counts_only_delegation_tools(self):
        run = self._run("delegated", tools=("SubAgent", "Read", "ParallelTask"))
        assert run.delegation_calls == 2

    def test_judge_requires_success_not_lower(self):
        verdict = judge(
            single_success=1.0,
            delegated_success=0.5,
            token_ratio=0.5,
            time_ratio=0.5,
            token_threshold=0.9,
            time_threshold=0.9,
        )
        assert not verdict.worthwhile
        assert "成功率下降" in verdict.reason

    def test_judge_accepts_token_or_time_improvement(self):
        by_token = judge(
            single_success=1.0,
            delegated_success=1.0,
            token_ratio=0.8,
            time_ratio=1.2,
            token_threshold=0.9,
            time_threshold=0.9,
        )
        by_time = judge(
            single_success=1.0,
            delegated_success=1.0,
            token_ratio=1.2,
            time_ratio=0.8,
            token_threshold=0.9,
            time_threshold=0.9,
        )
        assert by_token.worthwhile and by_time.worthwhile

    def test_judge_rejects_no_efficiency_gain(self):
        verdict = judge(
            single_success=1.0,
            delegated_success=1.0,
            token_ratio=1.1,
            time_ratio=1.1,
            token_threshold=0.9,
            time_threshold=0.9,
        )
        assert not verdict.worthwhile
        assert "无成本/时延收益" in verdict.reason

    def test_judge_rejects_all_zero_success_even_with_good_ratio(self):
        verdict = judge(
            single_success=0.0,
            delegated_success=0.0,
            token_ratio=0.5,
            time_ratio=0.5,
            token_threshold=0.9,
            time_threshold=0.9,
        )
        assert not verdict.worthwhile

    def test_compare_task_exposes_ratios(self):
        comparison = compare_task(
            task_id="t",
            title="T",
            category=SPLIT_BENEFICIAL,
            single=self._run("single", tokens=1000, elapsed=10.0),
            delegated=self._run("delegated", tokens=500, elapsed=5.0, tools=("SubAgent",)),
            token_ratio_threshold=0.9,
            time_ratio_threshold=0.9,
        )
        assert comparison.token_ratio == 0.5
        assert comparison.time_ratio == 0.5
        assert comparison.worth_splitting

    def test_category_summary_groups_by_category(self):
        single_good = self._run("single", tokens=1000, elapsed=10.0)
        delegated_good = self._run("delegated", tokens=500, elapsed=5.0, tools=("SubAgent",))
        single_bad = self._run("single", tokens=1000, elapsed=10.0)
        delegated_bad = self._run("delegated", tokens=1500, elapsed=12.0, tools=("SubAgent",))
        comparisons = [
            compare_task(
                task_id="good", title="Good", category=SPLIT_BENEFICIAL,
                single=single_good, delegated=delegated_good,
                token_ratio_threshold=0.9, time_ratio_threshold=0.9,
            ),
            compare_task(
                task_id="bad", title="Bad", category=SPLIT_NOT_BENEFICIAL,
                single=single_bad, delegated=delegated_bad,
                token_ratio_threshold=0.9, time_ratio_threshold=0.9,
            ),
        ]
        summaries = summarize_categories(
            comparisons, token_ratio_threshold=0.9, time_ratio_threshold=0.9
        )
        by_category = {item.category: item for item in summaries}
        assert by_category[SPLIT_BENEFICIAL].worth_splitting
        assert not by_category[SPLIT_NOT_BENEFICIAL].worth_splitting

    def test_category_summary_detects_per_task_regression(self):
        single_ok = self._run("single", tokens=1000, elapsed=10.0)
        delegated_ok = self._run("delegated", tokens=500, elapsed=5.0, tools=("SubAgent",))
        delegated_bad = self._run(
            "delegated", success=False, tokens=500, elapsed=5.0, tools=("SubAgent",)
        )
        comparisons = [
            compare_task(
                task_id="kept", title="Kept", category=SPLIT_BENEFICIAL,
                single=single_ok, delegated=delegated_ok,
                token_ratio_threshold=0.9, time_ratio_threshold=0.9,
            ),
            compare_task(
                task_id="regressed", title="Regressed", category=SPLIT_BENEFICIAL,
                single=self._run("single", tokens=1000, elapsed=10.0), delegated=delegated_bad,
                token_ratio_threshold=0.9, time_ratio_threshold=0.9,
            ),
        ]
        summary = summarize_categories(
            comparisons, token_ratio_threshold=0.9, time_ratio_threshold=0.9
        )[0]
        assert summary.regressions == 1
        assert not summary.worth_splitting
        assert "存在 1 个任务" in summary.reason

    def test_no_actual_delegation_cannot_be_attributed_to_splitting(self):
        comparison = compare_task(
            task_id="unused", title="Unused", category=SPLIT_BENEFICIAL,
            single=self._run("single", tokens=1000, elapsed=10.0),
            delegated=self._run("delegated", tokens=500, elapsed=5.0),
            token_ratio_threshold=0.9, time_ratio_threshold=0.9,
        )
        assert not comparison.worth_splitting
        assert "未实际使用委派" in comparison.reason
        summary = summarize_categories(
            [comparison], token_ratio_threshold=0.9, time_ratio_threshold=0.9
        )[0]
        assert summary.delegated_delegation_calls == 0
        assert not summary.worth_splitting
        assert "未实际使用委派" in summary.reason


class TestRunner:
    def test_offline_run_produces_expected_verdicts(self):
        executor = FakeExecutor()
        report = run_benchmark(TASKS, executor, model="fake")
        assert len(report.comparisons) == len(TASKS)
        assert all(item.single.success and item.delegated.success for item in report.comparisons)
        categories = {item.category: item for item in report.categories}
        assert categories[SPLIT_BENEFICIAL].worth_splitting
        assert not categories[SPLIT_NOT_BENEFICIAL].worth_splitting

    def test_each_strategy_gets_an_isolated_workspace(self):
        executor = FakeExecutor()
        run_benchmark(TASKS, executor, model="fake")
        workspaces = [workspace for _, _, workspace in executor.calls]
        assert len(workspaces) == len(TASKS) * 2
        assert len({str(path) for path in workspaces}) == len(workspaces)
        assert all(workspace.name == "workspace" for workspace in workspaces)

    def test_workspaces_are_cleaned_up(self):
        executor = FakeExecutor()
        run_benchmark(TASKS, executor, model="fake")
        assert all(not workspace.exists() for _, _, workspace in executor.calls)

    def test_executor_exception_is_recorded_not_raised(self):
        class ExplodingExecutor:
            def execute(self, task, workspace, strategy):
                raise RuntimeError("boom")

        report = run_benchmark(TASKS, ExplodingExecutor(), model="fake")
        assert all(not item.single.success and not item.delegated.success for item in report.comparisons)
        assert all("RuntimeError: boom" in item.single.error for item in report.comparisons)
        assert all(not item.worth_splitting for item in report.comparisons)

    def test_missing_artifact_fails_success_check(self):
        class MissingArtifactExecutor(FakeExecutor):
            def execute(self, task, workspace, strategy):
                observation = super().execute(task, workspace, strategy)
                if task.id == "single-file-fix":
                    (workspace / "calc.py").unlink()
                return observation

        report = run_benchmark([TASKS_BY_ID["single-file-fix"]], MissingArtifactExecutor(), model="fake")
        comparison = report.comparisons[0]
        assert not comparison.single.success
        assert any("缺少产物文件 calc.py" in item for item in comparison.single.failures)

    def test_select_tasks_keeps_requested_order(self):
        selected = select_tasks(["log-triage", "station-audit"])
        assert [task.id for task in selected] == ["log-triage", "station-audit"]

    def test_select_tasks_rejects_unknown_id(self):
        with pytest.raises(SystemExit):
            select_tasks(["not-a-task"])

    def test_non_positive_threshold_is_rejected(self):
        with pytest.raises(ValueError, match="阈值必须是正数"):
            run_benchmark([TASKS_BY_ID["log-triage"]], FakeExecutor(), token_ratio_threshold=0)


class TestReport:
    def test_table_contains_tasks_and_verdicts(self):
        report = run_benchmark(TASKS, FakeExecutor(), model="fake")
        table = render_table(report)
        for task in TASKS:
            assert task.id in table
        assert "SPLIT" in table
        assert "KEEP-SINGLE" in table
        assert "分类汇总" in table

    def test_table_renders_non_empty_category_rows(self):
        report = run_benchmark(TASKS, FakeExecutor(), model="fake")
        assert report.categories
        table = render_table(report)
        header = next(line for line in table.splitlines() if line.startswith("类别"))
        assert "退化" in header
        assert "委派" in header
        assert "回退" not in header
        for item in report.categories:
            row = next(
                line for line in table.splitlines() if line.startswith(item.category)
            )
            # 任务数、逐任务退化数、委派次数都必须实际渲染出来
            assert f"{item.task_count}" in row
            assert f"{item.regressions}" in row
            assert f"{item.delegated_delegation_calls}" in row

    def test_json_dict_is_serializable(self):
        report = run_benchmark(TASKS, FakeExecutor(), model="fake")
        data = to_dict(report)
        encoded = json.dumps(data, ensure_ascii=False)
        decoded = json.loads(encoded)
        assert decoded["thresholds"] == {"token_ratio": 0.9, "time_ratio": 0.9}
        assert len(decoded["tasks"]) == len(TASKS)
        assert {item["category"] for item in decoded["categories"]} == {
            SPLIT_BENEFICIAL,
            SPLIT_NOT_BENEFICIAL,
        }

    def test_write_json_creates_file(self, tmp_path):
        from benchmarks.multi_agent.report import write_json

        report = run_benchmark(TASKS, FakeExecutor(), model="fake")
        target = tmp_path / "report.json"
        write_json(report, target)
        decoded = json.loads(target.read_text(encoding="utf-8"))
        assert decoded["model"] == "fake"
        assert len(decoded["tasks"]) == len(TASKS)


class TestCli:
    def test_default_mode_does_not_execute_or_call_model(self, capsys, monkeypatch):
        def _forbidden(*args, **kwargs):
            raise AssertionError("默认模式不应构造 live 执行器")

        monkeypatch.setattr(
            "benchmarks.multi_agent.runner.LiveStrategyExecutor", _forbidden
        )
        assert main([]) == 0
        output = capsys.readouterr().out
        assert "默认不执行任务" in output
        assert "--live" in output

    def test_list_mode_prints_tasks(self, capsys):
        assert main(["--list"]) == 0
        output = capsys.readouterr().out
        for task in TASKS:
            assert task.id in output

    def test_live_executor_rejects_unknown_strategy(self, tmp_path):
        executor = LiveStrategyExecutor(model_key="unused")
        with pytest.raises(ValueError, match="未知策略"):
            executor.execute(TASKS[0], tmp_path, "bogus")

    def test_live_executor_collects_events_usage_and_cleans_up(self, monkeypatch, tmp_path):
        class _FakeAgent:
            def __init__(self):
                self.last_usage = {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}
                self.cleaned = False
                self.on_event = None

            def run(self, prompt):
                self.on_event({"type": "action_start", "tool_name": "Read"})
                self.on_event({"type": "action_start", "tool_name": "SubAgent"})
                self.on_event({"type": "answer_delta", "content": "partial"})
                return "final answer"

            def cleanup(self):
                self.cleaned = True

        fake = _FakeAgent()

        def _build(self, task, workspace, strategy, on_event):
            fake.on_event = on_event
            return fake

        monkeypatch.setattr(LiveStrategyExecutor, "_build_agent", _build)
        observation = LiveStrategyExecutor(model_key="unused").execute(TASKS[0], tmp_path, "delegated")
        assert observation.answer == "final answer"
        assert observation.total_tokens == 40
        assert observation.tool_calls == ("Read", "SubAgent")
        assert observation.error == ""
        assert fake.cleaned

    def test_live_executor_records_run_error(self, monkeypatch, tmp_path):
        class _FailingAgent:
            last_usage = {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1}

            def __init__(self):
                self.cleaned = False

            def run(self, prompt):
                raise RuntimeError("model down")

            def cleanup(self):
                self.cleaned = True

        fake = _FailingAgent()

        def _build(self, task, workspace, strategy, on_event):
            return fake

        monkeypatch.setattr(LiveStrategyExecutor, "_build_agent", _build)
        observation = LiveStrategyExecutor(model_key="unused").execute(TASKS[0], tmp_path, "single")
        assert observation.error == "RuntimeError: model down"
        assert observation.total_tokens == 1
        assert fake.cleaned


class TestLiveAgentConstruction:
    def _install_fakes(self, monkeypatch, tool_names=None):
        import floodmind
        import floodmind.agent.runtime.contracts.workspace as workspace_mod
        import floodmind.memory.dual_memory as memory_mod

        recorded = {"tool_names": set(tool_names or {"SubAgent", "ParallelTask", "Read"})}

        class _Registry:
            def __init__(self):
                self._names = set(recorded["tool_names"])

            def names(self):
                return list(self._names)

            def unregister_names(self, names):
                removed = 0
                for name in names:
                    if name in self._names:
                        self._names.remove(name)
                        removed += 1
                return removed

            def tools_schema(self):
                return []

        class _Executor:
            def set_tools_schema(self, schema):
                recorded["schema"] = schema

        class _Raw:
            def __init__(self):
                self._orchestrator_registry = _Registry()
                self._orchestrator_executor = _Executor()
                self.rebuilt = 0

            def _rebuild_system_prompts(self):
                self.rebuilt += 1

        class _Agent:
            def __init__(self, **kwargs):
                recorded["agent_kwargs"] = kwargs
                self.raw = _Raw()

            def cleanup(self):
                recorded["cleaned"] = True

        class _Workspace:
            @classmethod
            def from_folder(cls, folder, session_id=""):
                return cls()

            def ensure(self):
                return self

        monkeypatch.setattr(floodmind, "Agent", _Agent)
        monkeypatch.setattr(floodmind, "ModelClient", type("M", (), {"from_settings": staticmethod(lambda: object())}))
        monkeypatch.setattr(workspace_mod, "Workspace", _Workspace)
        monkeypatch.setattr(memory_mod, "DualMemory", lambda **kwargs: object())
        return recorded

    def test_single_strategy_removes_delegation_tools(self, monkeypatch, tmp_path):
        recorded = self._install_fakes(monkeypatch)
        executor = LiveStrategyExecutor()
        agent = executor._build_agent(TASKS[0], tmp_path / "workspace", "single", lambda event: None)
        assert agent.raw._orchestrator_registry.names() == ["Read"]
        assert agent.raw.rebuilt == 1
        prompt = recorded["agent_kwargs"]["system_prompt"]
        assert str((tmp_path / "workspace").resolve()) in prompt
        assert "工作目录" in prompt

    def test_delegated_strategy_fails_closed_without_delegation_tools(self, monkeypatch, tmp_path):
        self._install_fakes(monkeypatch, tool_names={"Read"})
        executor = LiveStrategyExecutor()
        with pytest.raises(RuntimeError, match="缺少委派工具"):
            executor._build_agent(TASKS[0], tmp_path / "workspace", "delegated", lambda event: None)

    def test_llm_factory_is_injected(self, monkeypatch, tmp_path):
        recorded = self._install_fakes(monkeypatch, tool_names={"SubAgent", "ParallelTask"})
        sentinel = object()
        calls = []

        def _factory():
            calls.append(True)
            return sentinel

        executor = LiveStrategyExecutor(llm_factory=_factory)
        executor._build_agent(TASKS[0], tmp_path / "workspace", "delegated", lambda event: None)
        assert calls == [True]
        assert recorded["agent_kwargs"]["llm"] is sentinel

