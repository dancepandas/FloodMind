# 标准测试规范

适用于所有代码与交付变更。测试的作用是证明外部可观察行为，而不是证明实现按预设分支运行。

## 四层证据

| 层次 | 应证明什么 | 不能替代什么 |
| --- | --- | --- |
| 单元 | 纯逻辑、边界与故障处理 | 组件接线和最终任务结果 |
| 契约 | 数据/权限/事件/错误语义，生产者与消费者一致 | 真实执行链 |
| 组装运行时 | 使用真实执行器/服务，模型与网络边界可替换 | 可安装产物 |
| 安装产物 | sdist → wheel → 隔离安装、公共导入、资源、CLI | 业务行为和真实模型质量 |

复用 `tests/` 与 `evals/`，不创建第二套运行时或验证框架。回归用例应在恢复缺陷时失败。Mock 放在模型、网络、时间等不确定边界，不能把被验证的执行链全部 Mock 掉。

## 默认命令

```bash
python -m pytest -q
python scripts/check_doc_examples.py
python scripts/calibrate_judge.py --judge rule --min-agreement 1.0
python -m evals.runner --json <临时报告路径>
python scripts/check_distribution.py
```

脚本命令随本批实现接入；不以文档列出命令代替实际执行记录。登记示例检查只覆盖显式登记块；组装测试验证其真实工具执行，不声称验证全部文档语义。

普通 PR 的验证应无密钥、可重复，不调用真实付费模型。真实模型质量/效率评测单独授权执行，结果不得替代确定性回归。不要为所有模块强制任意的 100% 覆盖率；重要的是能捕获契约破坏和实际失败。

- [变更验证矩阵](change-matrix.md)
- [夹具与隔离](fixtures-and-isolation.md)
- [产物与发布验收](artifacts-and-release.md)

本地通过、远端 CI 通过、服务端配置为必需检查是三个不同事实，必须分别报告。
