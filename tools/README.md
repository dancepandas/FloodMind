# tools — 真实模型评测工具

用真实模型验证 FloodMind Harness 的评测脚本。不属于安装包，不进入 CI，按需运行。密钥只从环境变量读取。

## 工具

| 文件 | 作用 |
| --- | --- |
| `bench_bfcl.py` | BFCL v3 适配器：simple / multiple / parallel / irrelevance，按调用集合与参数判分 |
| `live_eval.py` | 11 个端到端场景，按客观产物判定完成度，并统计效率与工具使用质量 |
| `live_probe.py` | 最小 live 探针，验证完整 runtime 的真实链路 |

## 运行

```bash
# BFCL（数据需另行下载到独立目录）
FM_BASE=... FM_KEY=... FM_MODEL=... FM_CONTEXT_WINDOW=... \
  python tools/bench_bfcl.py --data-dir <dir> --limit 50 --out bfcl.json

# 已有报告按最新规则离线重算
python tools/bench_bfcl.py --data-dir <dir> --rescore bfcl.json

# 端到端场景（--repeat 取样稳定性）
FM_BASE=... FM_KEY=... FM_MODEL=... python tools/live_eval.py --out report.json --repeat 2

# 链路探针
FM_BASE=... FM_KEY=... FM_MODEL=... python tools/live_probe.py
```

## 约定

- 密钥只读环境变量，不落盘；脚本在导入 FloodMind 前把 `FLOODMIND_HOME` 指向临时目录。
- BFCL 数据不入库；结果与模型、提示词、上下文窗口绑定，换配置需重新测量。
- 真实调用产生费用与网络依赖，不要接入 pytest 默认流程。
- 历史演示/生成脚本（PPT、图片、文档转换）属于本地辅助材料，不是评测链路的一部分。
