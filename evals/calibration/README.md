# 判定器校准（C2）

路线图要求“状态断言优先，LLM 评审校准”。当前执行器评测仍以状态/轨迹/工作区断言为准；
本目录提供判定器校准的标注集与一致率统计，避免在未校准前把 LLM 评审当成发布门禁。

## 组成

| 文件 | 作用 |
|---|---|
| `samples.json` | 20 条带人工标签的样本（10 pass / 10 fail），覆盖数值、引用、禁止声明、长度、步骤完整性等 |
| `evals/judge.py` | `RuleJudge`（确定性）、`ModelJudge`（LLM，严格 JSON、fail-closed）、`calibrate` |
| `scripts/calibrate_judge.py` | 运行校准并输出一致率、混淆矩阵与不一致样本 |

## 运行

```bash
# CI 基线：确定性判定器，不调用模型
python scripts/calibrate_judge.py --judge rule --min-agreement 1.0

# 需要真实校准 LLM 评审时显式指定模型（会产生真实调用与费用）
python scripts/calibrate_judge.py --judge model --model <model_id> --min-agreement 0.8
```

## 规则

- LLM 评审输出必须是 `{"verdict": "pass"|"fail", "reason": "..."}`；无法解析或枚举非法时按不通过处理。
- 标注集扩充时保持正负样本大致均衡；新增/修改样本必须同时更新 `required_terms` / `forbidden_terms` 或 `max_chars`。
- 只有一致率达到阈值后，才可把 LLM 评审用于人工抽检或发布前的辅助判断；发布门禁仍以确定性评测集为准。
