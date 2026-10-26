# .github/workflows — CI

GitHub Actions 配置。当前只有一个 `ci.yml`。

## 触发

- `push`、`pull_request`
- 每周一 03:17 UTC 定时
- 手动 `workflow_dispatch`

## 步骤

1. checkout + Python 3.10 / 3.12 矩阵
2. `pip install -e ".[gateway]" pytest`
3. `python -m pytest -q`
4. `python scripts/calibrate_judge.py --judge rule --min-agreement 1.0`
5. `python -m evals.runner --json eval-report.json`
6. 定时/手动时追加 `python -m evals.runner --heldout --json heldout-report.json`
7. 上传评测报告 artifact

## 约定

- 日常门禁必须无外部密钥、无真实模型调用、结果确定。
- held-out 只在定时或手动分支运行，避免进入日常调参回路。
- 真实模型评测（`tools/`、`benchmarks --live`）不在 CI 中运行，需要单独受控的 job 与凭据策略。
