"""C2：判定器校准框架与标注集测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.judge import (
    CalibrationSample,
    ModelJudge,
    RuleJudge,
    calibrate,
    load_samples,
)


SAMPLES_PATH = Path(__file__).resolve().parent.parent / "evals" / "calibration" / "samples.json"


class _FakeClient:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def invoke(self, prompt, system_prompt=None, temperature=None):
        self.calls.append({
            "prompt": prompt,
            "system_prompt": system_prompt,
            "temperature": temperature,
        })
        return SimpleNamespace(content=self.content)


def _sample(**overrides) -> CalibrationSample:
    base = {
        "sample_id": "s1",
        "input_text": "问题",
        "rubric": "必须包含 12.3",
        "output": "水位 12.3 m",
        "human_label": "pass",
        "required_terms": ["12.3"],
    }
    base.update(overrides)
    return CalibrationSample(**base)


class TestRuleJudge:
    def test_required_and_forbidden_terms(self):
        judge = RuleJudge()
        assert judge.evaluate(_sample()).passed
        assert not judge.evaluate(_sample(output="水位 11 m")).passed
        assert not judge.evaluate(_sample(forbidden_terms=["12.3"])).passed

    def test_max_chars(self):
        judge = RuleJudge()
        verdict = judge.evaluate(_sample(max_chars=3))
        assert not verdict.passed
        assert "长度" in verdict.reason

    def test_calibration_samples_are_consistent(self):
        samples = load_samples(SAMPLES_PATH)
        assert len(samples) == 20
        assert sum(1 for s in samples if s.human_label == "pass") == 10
        result = calibrate(RuleJudge(), samples)
        assert result["agreement"] == 1.0
        assert result["mismatches"] == []


class TestModelJudge:
    def test_parses_pass_and_fail(self):
        assert ModelJudge(_FakeClient('{"verdict": "pass", "reason": "ok"}')).evaluate(_sample()).passed
        assert not ModelJudge(_FakeClient('{"verdict": "fail", "reason": "bad"}')).evaluate(_sample()).passed

    def test_invalid_json_is_fail_closed(self):
        verdict = ModelJudge(_FakeClient("我觉得可以通过")).evaluate(_sample())
        assert not verdict.passed
        assert "合法 JSON" in verdict.reason

    def test_invalid_verdict_is_fail_closed(self):
        verdict = ModelJudge(_FakeClient('{"verdict": "maybe", "reason": "?"}')).evaluate(_sample())
        assert not verdict.passed
        assert "verdict 非法" in verdict.reason

    def test_non_object_json_is_fail_closed(self):
        verdict = ModelJudge(_FakeClient('["pass"]')).evaluate(_sample())
        assert not verdict.passed
        assert "不是 JSON 对象" in verdict.reason

    def test_prompt_contains_rubric_and_output(self):
        client = _FakeClient('{"verdict": "pass", "reason": "ok"}')
        ModelJudge(client).evaluate(_sample())
        prompt = client.calls[0]["prompt"]
        assert "必须包含 12.3" in prompt
        assert "水位 12.3 m" in prompt
        assert client.calls[0]["temperature"] == 0.0


class TestCalibrate:
    def test_confusion_matrix(self):
        samples = [
            _sample(sample_id="tp", human_label="pass"),
            _sample(sample_id="tn", output="无", human_label="fail"),
            _sample(sample_id="fp", output="通过", human_label="fail", required_terms=[]),
        ]
        # 第三个样本没有 required_terms，RuleJudge 会误判为 pass，形成 FP。
        result = calibrate(RuleJudge(), samples)
        assert result["confusion"] == {
            "true_positive": 1,
            "true_negative": 1,
            "false_positive": 1,
            "false_negative": 0,
        }
        assert result["agreement"] == pytest.approx(2 / 3, abs=1e-4)
        assert result["mismatches"][0]["id"] == "fp"

    def test_invalid_label_rejected(self):
        with pytest.raises(ValueError):
            _sample(human_label="unknown")

    def test_duplicate_sample_id_rejected(self, tmp_path):
        path = tmp_path / "samples.json"
        path.write_text(
            '[{"id":"x","human_label":"pass"},{"id":"x","human_label":"fail"}]',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="重复"):
            load_samples(path)
