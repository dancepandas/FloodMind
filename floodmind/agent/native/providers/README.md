# floodmind/agent/native/providers — 厂商方言编解码

把不同 LLM 厂商的“线上方言”收敛为统一的 canonical 请求与流式事件：请求参数适配、消息归一化、chunk 解析与 usage 归一化。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `__init__.py` | `route_codec()` 路由入口与 codec 注册表 |
| `base.py` | `ProviderCodec` 基类、`StreamState`、`<think>` 标签拆分等共享工具 |
| `codec.py` | provider-neutral 编解码接口（encode_request / decode_chunk / decode_message） |
| `openai_compatible.py` | 标准 OpenAI 兼容兜底 |
| `dashscope.py` | 阿里云百炼方言（enable_thinking、max_completion_tokens、多模态 block） |
| `deepseek.py` | DeepSeek 方言（thinking.type、思考模式下的采样参数） |
| `kimi.py` | Kimi/Moonshot 按代际处理 thinking、temperature、usage 位置 |
| `minimax.py` | MiniMax 方言（reasoning_split、temperature 钳制） |
| `usage.py` | 统一 `TokenUsage` |

## 路由规则

`base_url` 精确命中（100）> `provider` id（60）> 模型名前缀（40）> OpenAI 兜底。仅模型名前缀命中时标记 `conservative=True`：响应解析保留适配，请求退化为标准 OpenAI 行为。

## 约定

- codec 只翻译线上方言，不触碰编排、记忆与 Agent 状态。
- 每次流式调用使用独立的 `StreamState`，不得跨调用复用。
- 新增厂商实现 `ProviderCodec` 子类并注册到 `_CODECS`，不要在 `ModelClient` 里写厂商分支。
- 当前只支持 OpenAI Chat Completions 兼容传输；Anthropic Messages API 端点会被校验拒绝。
