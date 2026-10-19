"""metrics_report.py — 离线报告脚本的解析、聚合与错误路径。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import metrics_report  # noqa: E402

from floodmind.agent.runtime.contracts.tracing import TraceSpan  # noqa: E402

PRICING_JSON = {"m-a": {"input_per_1k": 0.1, "output_per_1k": 0.2, "currency": "USD"}}


def _write_trace(session_dir: Path, spans: list) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    with (session_dir / "trace.jsonl").open("w", encoding="utf-8") as f:
        for span in spans:
            f.write(span.model_dump_json() + "\n")


def _llm_span(model, in_tok, out_tok, run_id=""):
    span = TraceSpan(type="llm", name="llm_step", input={"model": model})
    if run_id:
        span.metadata["harness.run_id"] = run_id
    span.finalize(output={"tokens": {"prompt_tokens": in_tok, "completion_tokens": out_tok}})
    return span


class TestMetricsReport:
    def test_json_output_with_pricing_json(self, tmp_path, capsys):
        _write_trace(
            tmp_path / "s1",
            [_llm_span("m-a", 1000, 500, run_id="r1"), _llm_span("m-a", 1000, 500)],
        )
        pricing_path = tmp_path / "pricing.json"
        pricing_path.write_text(json.dumps(PRICING_JSON), encoding="utf-8")

        rc = metrics_report.main([
            "--session-id", "s1",
            "--base-dir", str(tmp_path),
            "--pricing-json", str(pricing_path),
            "--json",
        ])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        # 成本：每次 1000*0.1/1000 + 500*0.2/1000 = 0.2，两次共 0.4
        assert out["totals"]["estimated_cost"] == pytest.approx(0.4)
        assert out["by_run"]["r1"]["total_tokens"] == 1500
        assert out["by_run"]["unattributed"]["total_tokens"] == 1500
        assert out["by_model"]["m-a"]["llm_calls"] == 2

    def test_text_output(self, tmp_path, capsys):
        _write_trace(tmp_path / "s1", [_llm_span("m-a", 10, 5, run_id="r1")])
        rc = metrics_report.main([
            "--session-id", "s1",
            "--base-dir", str(tmp_path),
            "--pricing-json", str(_pricing_file(tmp_path)),
        ])
        assert rc == 0
        text = capsys.readouterr().out
        assert "按模型" in text and "按 Run" in text and "按工具" in text
        assert "m-a" in text and "r1" in text

    def test_missing_trace_returns_exit_code_2(self, tmp_path, capsys):
        rc = metrics_report.main(["--session-id", "nope", "--base-dir", str(tmp_path)])
        assert rc == 2
        assert "不存在" in capsys.readouterr().err

    def test_empty_trace_returns_exit_code_2(self, tmp_path, capsys):
        session = tmp_path / "s1"
        session.mkdir(parents=True)
        (session / "trace.jsonl").write_text("", encoding="utf-8")
        rc = metrics_report.main(["--session-id", "s1", "--base-dir", str(tmp_path)])
        assert rc == 2
        assert "为空" in capsys.readouterr().err

    def test_bad_lines_are_skipped(self, tmp_path, capsys):
        session = tmp_path / "s1"
        session.mkdir(parents=True)
        good = _llm_span("m-a", 10, 5).model_dump_json()
        (session / "trace.jsonl").write_text(
            "not json\n" + good + "\n", encoding="utf-8"
        )
        rc = metrics_report.main([
            "--session-id", "s1",
            "--base-dir", str(tmp_path),
            "--json",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        assert "解析失败" in captured.err
        assert "m-a" in captured.out

    def test_bad_pricing_file_returns_exit_code_2(self, tmp_path, capsys):
        _write_trace(tmp_path / "s1", [_llm_span("m-a", 10, 5)])
        rc = metrics_report.main([
            "--session-id", "s1",
            "--base-dir", str(tmp_path),
            "--pricing-json", str(tmp_path / "missing.json"),
        ])
        assert rc == 2
        assert "定价" in capsys.readouterr().err


def _pricing_file(tmp_path: Path) -> Path:
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(PRICING_JSON), encoding="utf-8")
    return path
