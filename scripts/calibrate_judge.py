"""C2：判定器校准脚本。

用法::

    python scripts/calibrate_judge.py --judge rule
    python scripts/calibrate_judge.py --judge model --model <model_id>

``rule`` 是确定性判定器，可在 CI 中重复运行；``model`` 会真实调用模型，必须显式
指定模型并自行确认凭据与调用成本。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.judge import ModelJudge, RuleJudge, calibrate, load_samples


DEFAULT_SAMPLES = ROOT / "evals" / "calibration" / "samples.json"


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="判定器与人工标签校准")
    parser.add_argument("--judge", choices=("rule", "model"), default="rule")
    parser.add_argument("--model", default="", help="model 判定器使用的模型 ID")
    parser.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    parser.add_argument("--min-agreement", type=float, default=0.8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    samples = load_samples(args.samples)

    if args.judge == "rule":
        judge = RuleJudge()
    else:
        if not args.model:
            print("--judge model 必须同时提供 --model <model_id>", file=sys.stderr)
            return 2
        from floodmind.agent.native.model_client import ModelClient

        judge = ModelJudge(ModelClient.from_settings(model_key=args.model))

    result = calibrate(judge, samples)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"judge={args.judge} samples={result['total']} agreement={result['agreement']:.2%}")
        confusion = result["confusion"]
        print(
            "confusion: "
            f"TP={confusion['true_positive']} TN={confusion['true_negative']} "
            f"FP={confusion['false_positive']} FN={confusion['false_negative']}"
        )
        for mismatch in result["mismatches"]:
            print(
                f"  mismatch {mismatch['id']}: human={mismatch['human_label']} "
                f"judge={mismatch['judge']} reason={mismatch['reason']}"
            )

    return 0 if result["agreement"] >= args.min_agreement else 1


if __name__ == "__main__":
    raise SystemExit(main())
