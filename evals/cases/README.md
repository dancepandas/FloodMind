# evals/cases — 手工评测用例

每个 JSON 文件是一个确定性评测用例，覆盖主循环的关键边界。加载与判定逻辑见 `evals/README.md` 与 `evals/runner.py`。

## 用例分层

| 层 | 文件 |
| --- | --- |
| smoke | 主循环基本可用性 |
| tool | 真实工具执行（Read/Write/Glob/Grep/Edit） |
| regression | 契约与安全回归 |
| safety | 越界路径、planning 硬门等安全边界 |

## 约定

- 用例必须确定性：不调用真实模型、网络、随机数、时间或环境。
- 所有用例 ID 全局唯一；系统性扩容优先加到 `evals/case_packs.py` 并注册家族。
- `tool` / `safety` 层只允许操作评测临时 workspace。
- `heldout: true` 的用例默认不加载，只在 `--heldout` 时运行。
