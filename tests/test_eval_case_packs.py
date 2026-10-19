"""评测集扩容（C1）的 case pack 结构测试。

验证 evals/case_packs.py 的确定性生成契约与 evals/runner.py 的合并加载行为：
- load_cases 非空、ID 全局唯一、结构字段齐备；
- 四个常规层级（smoke/tool/regression/safety）均非空；
- held-out 用例存在且默认被 _load_cases 排除、include_heldout=True 时纳入；
- runner.main 按层级运行（含 --heldout）退出码为 0。
"""

from __future__ import annotations

import os

import pytest

# evals.runner 在导入时把 FLOODMIND_HOME 指向评测临时目录，保证 CLI 不读写真实状态。
# pytest 进程内导入必须立即还原，否则会污染同一进程里的其它测试。
_SAVED_FLOODMIND_HOME = os.environ.get("FLOODMIND_HOME")
from evals import case_packs, runner  # noqa: E402

if _SAVED_FLOODMIND_HOME is None:
    os.environ.pop("FLOODMIND_HOME", None)
else:
    os.environ["FLOODMIND_HOME"] = _SAVED_FLOODMIND_HOME

_REQUIRED_FIELDS = ("id", "tier", "description", "user_text", "rounds", "expect")


def _all_cases() -> list:
    return case_packs.load_cases()


class TestCasePackStructure:
    def test_load_cases_non_empty(self):
        cases = _all_cases()
        assert cases, "case pack 必须生成至少一个用例"

    def test_ids_unique(self):
        cases = _all_cases()
        ids = [case["id"] for case in cases]
        assert len(ids) == len(set(ids)), f"存在重复 id: {sorted({i for i in ids if ids.count(i) > 1})}"

    def test_required_fields_present(self):
        for case in _all_cases():
            for field in _REQUIRED_FIELDS:
                assert field in case, f"用例 {case.get('id', '?')} 缺少字段 {field}"

    def test_tier_valid(self):
        for case in _all_cases():
            assert case["tier"] in runner.SUPPORTED_TIERS, (
                f"用例 {case['id']} 层级非法: {case['tier']}"
            )

    def test_rounds_have_events(self):
        for case in _all_cases():
            assert case["rounds"], f"用例 {case['id']} 没有模型轮次"
            for idx, rnd in enumerate(case["rounds"]):
                assert "events" in rnd and rnd["events"], (
                    f"用例 {case['id']} 第 {idx} 轮缺少 events"
                )

    def test_expect_has_assertions(self):
        for case in _all_cases():
            assert case["expect"], f"用例 {case['id']} 没有任何断言（不凑数原则）"

    def test_deterministic_generation(self):
        first = _all_cases()
        second = case_packs.load_cases()
        assert first == second, "同一版本多次生成必须完全等价（确定性原则）"

    def test_chinese_description(self):
        for case in _all_cases():
            desc = case.get("description", "")
            assert desc, f"用例 {case['id']} 缺少中文 description（可解释性原则）"
            assert any("一" <= ch <= "鿿" for ch in desc), (
                f"用例 {case['id']} 的 description 不是中文: {desc!r}"
            )


class TestTierCoverage:
    @pytest.mark.parametrize("tier", runner.SUPPORTED_TIERS)
    def test_regular_tiers_non_empty(self, tier):
        cases = runner._load_cases(tier=tier)
        assert cases, f"层级 {tier} 必须非空（全量门禁要求四个常规层级齐备）"

    def test_heldout_cases_exist(self):
        cases = _all_cases()
        heldout = [case for case in cases if case.get("heldout")]
        assert heldout, "必须存在 held-out 用例"
        assert len(heldout) >= 10, f"held-out 用例过少: {len(heldout)}"


class TestHeldoutLoading:
    def test_default_excludes_heldout(self):
        all_cases = runner._load_cases(tier="all")
        heldout_ids = {c["id"] for c in _all_cases() if c.get("heldout")}
        loaded_ids = {c["id"] for c in all_cases}
        assert heldout_ids, "前置：case pack 必须含 held-out 用例"
        assert not (heldout_ids & loaded_ids), (
            f"默认加载不应包含 held-out: {sorted(heldout_ids & loaded_ids)}"
        )

    def test_include_heldout_adds_them(self):
        regular = runner._load_cases(tier="all")
        with_heldout = runner._load_cases(tier="all", include_heldout=True)
        regular_ids = {c["id"] for c in regular}
        heldout_ids = {c["id"] for c in with_heldout} - regular_ids
        assert heldout_ids, "include_heldout=True 必须额外纳入 held-out 用例"

    def test_no_duplicate_ids_after_merge(self):
        with_heldout = runner._load_cases(tier="all", include_heldout=True)
        ids = [c["id"] for c in with_heldout]
        assert len(ids) == len(set(ids)), "JSON 与 case pack 合并后 id 必须全局唯一"


class TestRunnerMain:
    @pytest.mark.parametrize("tier", runner.SUPPORTED_TIERS)
    def test_tier_runs_green(self, tier):
        assert runner.main(["--tier", tier]) == 0

    def test_heldout_runs_green(self):
        assert runner.main(["--heldout"]) == 0
