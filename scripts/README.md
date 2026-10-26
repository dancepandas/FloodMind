# scripts — 仓库级运维与离线工具

不属于 `floodmind` 安装包，从仓库根运行，服务于校准、可观测性、网关验证与架构图生成。

## 主要入口

| 文件/目录 | 作用 |
| --- | --- |
| `calibrate_judge.py` | 用人工标注集校准 `RuleJudge` / `ModelJudge`，输出一致率与混淆矩阵 |
| `metrics_report.py` | 读取 `trace.jsonl`，按 run/model/step/tool 聚合 token 与成本 |
| `e2e_gateway.py` | 网关 SSE 对话 + 权限 ASK 闭环的手工端到端脚本 |
| `smoke_provider_pipelines.py` | 厂商真实 API 冒烟（需要本地凭证，不进入 CI） |
| `knowledge_graph/` | 静态分析生成架构知识图谱 |

## 常用命令

```bash
python scripts/calibrate_judge.py --judge rule --min-agreement 1.0
python scripts/metrics_report.py --session-id <sid> [--json]
python -m scripts.knowledge_graph --out docs/knowledge-graph/graph
python -m scripts.knowledge_graph.query <path-or-symbol>
```

## 约定

- 会读取真实凭证的脚本不得进入 CI，也不得打印密钥。
- `metrics_report.py` 在 trace 缺失或为空时以退出码 2 失败，不伪造零值。
- 架构目录变更后，先更新 `knowledge_graph/layers.py` 与 `subsystems.py` 再重新生成图。
- 图谱是静态启发式结果，反射、动态注册与跨模块属性调用不在覆盖范围内。
