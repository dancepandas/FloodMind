# 贡献指南

开始前阅读 [开发契约](docs/development/README.md) 与 [测试规范](docs/testing/README.md)。开发契约约束实现，教程解释用法，历史设计与知识图谱不能替代现行行为协议。

## 工作流程

1. 明确变更类型、职责边界及消费者；架构变更先记录取舍，不以补丁、双写或 fallback 掩盖不一致。
2. 行为变化先写能复现失败的测试，再做最小模块化实现。
3. 同步契约、公开签名、调用方和显式登记示例；工具遵循 [工具契约](docs/development/adding-a-tool.md)。
4. 按 [验证矩阵](docs/testing/change-matrix.md) 执行适用检查，至少记录全量回归；不要只写“测试通过”。
5. PR 使用模板提供命令、真实结果、平台、跳过原因和风险。评审检查实际证据，不以清单打勾代替验证。

## 本地环境与检查

Python >=3.10；沿用项目 pyproject 的可选依赖。验证环境需要 pytest，产物构建另需开发工具 build。

```bash
python -m pip install -e ".[gateway]" pytest build
python -m pytest -q
python scripts/check_doc_examples.py
python scripts/calibrate_judge.py --judge rule --min-agreement 1.0
python -m evals.runner --json <临时报告路径>
python scripts/check_distribution.py
```

默认检查不需要模型密钥。真实付费模型评测必须单独说明成本与授权，密钥仅从环境读取。不得提交用户配置、业务文档、报告中的秘密或 `docs/key.txt`。

## CI 与合并

目标稳定汇总检查名称为 `CI required`：只有测试矩阵和 contracts 作业全部 success 才通过；failure、cancelled、skipped 或未知结果都不能视作成功。工作流实现不等于远端已经启用必需检查。

远端 branch protection/ruleset 仍须维护者核实并另行配置。没有远端必需检查时，不声称已经禁止未验证合并。CI 实际运行状态以对应提交的服务端记录为准。

## 交付边界

[产物与发布](docs/testing/artifacts-and-release.md) 要求干净提交验证。提交、推送、tag、发布和保护配置属于独立操作，不能由本地验证推导授权。
