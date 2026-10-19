"""C2：评测判定器校准。

路线图要求“状态断言优先，LLM 评审校准”。本模块提供：

- ``RuleJudge``：确定性判定器，用标注集里的 required/forbidden terms 与长度上限判定，
  作为 CI 中可重复的校准基线；
- ``ModelJudge``：可选的 LLM 评审器，要求模型返回严格 JSON。输出无法解析时按
  fail-closed 记为不通过，避免评审器故障被误当成通过；
- ``calibrate``：把判定结果与人工标签对比，输出一致率与混淆矩阵。

LLM 评审不在 CI 中自动调用真实模型；需要显式传入模型客户端或运行
``scripts/calibrate_judge.py --judge model --model <model_id>``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


@dataclass(frozen=True)
class CalibrationSample:
    """一条带人工标签的评审样本。"""

    sample_id: str
    input_text: str
    rubric: str
    output: str
    human_label: str
    required_terms: List[str] = field(default_factory=list)
    forbidden_terms: List[str] = field(default_factory=list)
    max_chars: Optional[int] = None

    def __post_init__(self) -> None:
        if self.human_label not in ("pass", "fail"):
            raise ValueError(
                f"样本 {self.sample_id} 的 human_label 必须是 pass/fail，实际: {self.human_label!r}"
            )


@dataclass(frozen=True)
class JudgeVerdict:
    """判定器输出。"""

    passed: bool
    reason: str


class Judge(Protocol):
    def evaluate(self, sample: CalibrationSample) -> JudgeVerdict:
        ...


class RuleJudge:
    """确定性判定器：required 全命中、forbidden 全不命中、长度不超限。"""

    def evaluate(self, sample: CalibrationSample) -> JudgeVerdict:
        output = sample.output or ""
        missing = [term for term in sample.required_terms if term not in output]
        forbidden = [term for term in sample.forbidden_terms if term in output]
        too_long = sample.max_chars is not None and len(output) > sample.max_chars

        reasons: List[str] = []
        if missing:
            reasons.append(f"缺少必需内容: {', '.join(missing)}")
        if forbidden:
            reasons.append(f"包含禁止内容: {', '.join(forbidden)}")
        if too_long:
            reasons.append(f"长度 {len(output)} 超过上限 {sample.max_chars}")
        if reasons:
            return JudgeVerdict(passed=False, reason="；".join(reasons))
        return JudgeVerdict(passed=True, reason="状态断言全部通过")


_MODEL_JUDGE_SYSTEM_PROMPT = """你是严格的评测判定器。根据评分标准判断候选输出是否通过。
只输出一个 JSON 对象，不要输出 Markdown 代码块或额外解释。
JSON 格式：{"verdict": "pass" 或 "fail", "reason": "一句话理由"}"""


class ModelJudge:
    """LLM 评审器。模型客户端只需实现 ``invoke(prompt, system_prompt=...)``。"""

    def __init__(self, model_client: Any):
        self._model_client = model_client

    def evaluate(self, sample: CalibrationSample) -> JudgeVerdict:
        prompt = (
            f"任务输入:\n{sample.input_text}\n\n"
            f"评分标准:\n{sample.rubric}\n\n"
            f"候选输出:\n{sample.output}\n"
        )
        response = self._model_client.invoke(
            prompt,
            system_prompt=_MODEL_JUDGE_SYSTEM_PROMPT,
            temperature=0.0,
        )
        content = str(getattr(response, "content", "") or "")
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return JudgeVerdict(
                passed=False,
                reason=f"评审器输出不是合法 JSON，按不通过处理: {content[:200]!r}",
            )
        if not isinstance(payload, dict):
            return JudgeVerdict(
                passed=False,
                reason=f"评审器输出不是 JSON 对象，按不通过处理: {content[:200]!r}",
            )
        verdict = str(payload.get("verdict", "")).strip().lower()
        reason = str(payload.get("reason", "") or "")
        if verdict not in ("pass", "fail"):
            return JudgeVerdict(
                passed=False,
                reason=f"评审器 verdict 非法，按不通过处理: {verdict!r}",
            )
        return JudgeVerdict(passed=verdict == "pass", reason=reason)


def load_samples(path: str | Path) -> List[CalibrationSample]:
    """加载 JSON 标注集。结构必须是样本数组。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("判定器校准标注集必须是 JSON 数组")
    samples: List[CalibrationSample] = []
    seen: set[str] = set()
    for raw in data:
        sample = CalibrationSample(
            sample_id=str(raw["id"]),
            input_text=str(raw.get("input", "")),
            rubric=str(raw.get("rubric", "")),
            output=str(raw.get("output", "")),
            human_label=str(raw["human_label"]),
            required_terms=[str(item) for item in raw.get("required_terms", [])],
            forbidden_terms=[str(item) for item in raw.get("forbidden_terms", [])],
            max_chars=raw.get("max_chars"),
        )
        if sample.sample_id in seen:
            raise ValueError(f"判定器校准样本 ID 重复: {sample.sample_id}")
        seen.add(sample.sample_id)
        samples.append(sample)
    if not samples:
        raise ValueError("判定器校准标注集为空")
    return samples


def calibrate(judge: Judge, samples: List[CalibrationSample]) -> Dict[str, Any]:
    """统计判定结果与人工标签的一致率。"""
    true_positive = true_negative = false_positive = false_negative = 0
    mismatches: List[Dict[str, Any]] = []

    for sample in samples:
        verdict = judge.evaluate(sample)
        human_pass = sample.human_label == "pass"
        if verdict.passed and human_pass:
            true_positive += 1
        elif not verdict.passed and not human_pass:
            true_negative += 1
        elif verdict.passed and not human_pass:
            false_positive += 1
            mismatches.append({
                "id": sample.sample_id,
                "human_label": sample.human_label,
                "judge": "pass",
                "reason": verdict.reason,
            })
        else:
            false_negative += 1
            mismatches.append({
                "id": sample.sample_id,
                "human_label": sample.human_label,
                "judge": "fail",
                "reason": verdict.reason,
            })

    total = len(samples)
    agreed = true_positive + true_negative
    return {
        "total": total,
        "agreement": round(agreed / total, 4) if total else 0.0,
        "confusion": {
            "true_positive": true_positive,
            "true_negative": true_negative,
            "false_positive": false_positive,
            "false_negative": false_negative,
        },
        "mismatches": mismatches,
    }
