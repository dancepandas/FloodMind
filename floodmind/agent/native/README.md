# floodmind/agent/native — Native Agent 执行引擎

不依赖 LangChain 的自研执行层，负责 Agent 主循环、模型流式调用、工具编排、上下文压缩、事件输出与产物检测。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `native_flood_agent.py` | 高层 Agent：组装提示词、工具、模型客户端、executor、Skill/MCP/子代理 |
| `executor.py` | `NativeAgentExecutor`：状态机驱动的 LLM ↔ Tool 循环 |
| `model_client.py` | 统一模型客户端，对接 OpenAI 兼容 Chat Completions |
| `model_gateway.py` | `FallbackModelClient`，按显式回退链切换模型 |
| `transport.py` | 纯 Wire I/O（`OpenAIChatTransport`）与 retry advice |
| `message_builder.py` | OpenAI 兼容消息构造（含多模态 content parts） |
| `response_pipeline.py` | 流式响应累积、tool_call 解析、usage 汇总与终态判定 |
| `tool_loading.py` | eager / catalog / progressive 三种工具加载模式 |
| `tool_runtime.py` | AgentTool 等对象到 `ToolSpec` 的统一适配 |
| `context_compressor.py` | 上下文压缩：保护头尾、裁剪工具输出、fail-closed |
| `atomic_groups.py` | 压缩时不可拆分的消息组（保证 tool_call/result 配对） |
| `projection.py` | 输入预算与 Projection Manifest（纯函数） |
| `event_bus.py` | `EventBus` / `StepEventBus`（子代理事件带 step_key） |
| `artifact_watcher.py` | 基于快照检测本轮新增产物 |
| `background_review.py` | 轮次结束后的轻量回顾，产出记忆/经验/Skill 建议 |
| `capabilities.py` | 数据化模型能力注册表 |
| `retry.py` | 可重试错误判断与指数退避 |
| `types.py` | Native Runtime 数据结构 |
| `providers/` | 各厂商请求/响应方言编解码 |

## 关键不变量

- 执行循环只有 `executor.py` 一处；`native_flood_agent.py` 只做能力装配。
- `response_pipeline.py` 只消费 `CanonicalPart`，非法 tool JSON 必须变成 `InvalidToolCall`，不能填充默认参数。
- `transport.py` 只做 I/O；是否重试由 `retry.py` 与编排层决定。
- `atomic_groups.py` 定义的组在压缩时不可拆分。
- `model_gateway` 仅在本轮尚未输出任何事件时回退。
- `projection.py` 保持纯函数，无 I/O、时间与随机副作用。
