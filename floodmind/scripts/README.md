# floodmind/scripts — 包内诊断脚本

不属于 Agent 运行时的开发者手工诊断工具，需要显式执行。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `check_qwen_reasoning.py` | 验证 DashScope/Qwen 的 `enable_thinking`、非流式与流式 reasoning/token 事件 |

## 运行

```bash
# 需要 DASHSCOPE_API_KEY；可选 QWEN_REASONING_MODEL / QWEN_MODEL
python -m floodmind.scripts.check_qwen_reasoning
```

## 约定

- 脚本可独立运行，导入时不得产生副作用。
- 不要接入 Agent 启动路径，也不要作为 CI 依赖（需要真实凭证与网络）。
