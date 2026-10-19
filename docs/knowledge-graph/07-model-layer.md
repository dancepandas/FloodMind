# 07. 模型层

> 代码位置：`floodmind/agent/native/model_client.py`、`model_gateway.py`、`transport.py`、`response_pipeline.py`、`capabilities.py`、`retry.py`、`providers/`、`floodmind/config/`。

## 1. 分层结构

```text
ModelClient              统一门面：from_settings / stream_chat / invoke / chat
  └─ FallbackModelClient 显式回退链包装（可选，按 model_routing.fallbacks 构造）
  └─ ModelTransport      传输抽象：OpenAIChatTransport
       └─ ProviderCodec  供应商方言：请求编码 + 流式解码（route_codec 自动选择）
            └─ HTTP      OpenAI-compatible Chat Completions
  └─ ResponsePipeline    流式 part 归一化为 CanonicalPart，聚合 tool_call/usage/终态
  └─ Capabilities        模型能力表（context_window 等）
  └─ Retry               should_retry + 指数退避建议
```

| 组件 | 文件 | 职责 |
|---|---|---|
| `ModelClient` | `native/model_client.py:29` | 构造、校验、流式调用、同步调用 |
| `FallbackModelClient` | `native/model_gateway.py:31-111` | 显式回退链；只在未输出任何事件前切换 |
| `OpenAIChatTransport` | `native/transport.py:33-44` | HTTP 请求、SSE 解析、错误分类 |
| `ResponsePipeline` | `native/response_pipeline.py:20` | delta 聚合、tool_call 拼装、usage 归一、终态判定 |
| `capabilities.py` | `native/capabilities.py` | provider → family → exact 能力注册表 |
| `retry.py` | `native/retry.py` | `should_retry` 与 `RetryPolicy`（调用方持有） |
| Provider Codec | `native/providers/` | dashscope、deepseek、kimi、minimax、openai_compatible、base、codec |
| 模型网关 | `native/model_gateway.py` | 回退链、子代理模型路由、配置读取 |
| 解析 | `floodmind/config/model_resolver.py` | 唯一模型解析点与窗口解析点 |
| 目录 | `floodmind/config/provider_registry.py`、`model_presets.py` | 供应商定义；前端模型目录（委托 resolver） |
| 全局设置 | `floodmind/config/settings.py` | settings.json 读写、密钥加解密、`ModelConfig` 门面 |

## 2. 调用链

### 2.1 流式

1. `ModelClient.stream_chat(messages, tools, ...)`（`model_client.py:201`）先让 codec 做消息准备。
2. 组装请求体：model、messages、temperature、max_tokens、stream、tools/tool_choice；显式 `extra_body` 优先。
3. `pipeline.prepare_request(..., enable_thinking=self.enable_thinking, stream=True)` 注入厂商参数。
4. `OpenAIChatTransport.send()` 发出请求，逐行解析 SSE（`transport.py:37-38`）。
5. codec 解码为 provider delta；`ResponsePipeline.accumulate()` 归一为 `CanonicalPart`：
   - `text_delta`
   - `reasoning_delta`
   - `tool_call_delta`（按 index 拼装 arguments；malformed JSON 转 `InvalidToolCall`，不生成可执行默认值）
   - `usage`
   - `response_end`（终态：refusal / content_filter / max_tokens）
6. `finalize()` 生成 `ToolCall`/`InvalidToolCall` 并发出 `tool_call_done` / `invalid_tool_call`。
7. 执行器消费 `ModelEvent`，发出 `token` / `reasoning` / `token_usage` 等事件。

### 2.2 同步

`invoke` / `chat`（`model_client.py:123`、`:138`）内部消费 `stream_chat`，聚合为完整文本或 tool_call 列表。

### 2.3 回退语义（G-19）

`FallbackModelClient`（`model_gateway.py:31-111`）：

| 规则 | 事实 |
|---|---|
| 切换时机 | 只在本次调用尚未 yield 任何事件时切换；一旦 `emitted=True`，异常原样上抛（62-87） |
| 链长度 | 构造时截断为 `1 + max_fallbacks`；`max_fallbacks` 默认 2，非法配置抛错（39-49、137-143） |
| 成功后的粘性 | 使用某个回退后，同一 run 内 `_active` 指向该模型（72） |
| 每 run 复位 | 执行器每次 run 起始调用 `reset_fallbacks()`（`executor.py:463-465`；`model_gateway.py:59-60`） |
| 全部失败 | 复位到入口模型并抛出最后一个异常，交给执行器重试（83-87） |
| 构造时机 | `build_fallback_client` 在 Agent 初始化时解析回退目标；未知模型 ID 直接 `ValueError`（`model_gateway.py:146-170`） |
| 可观测 | `on_fallback(from, to, error)` → 主代理 `model_fallback` 事件（`native_flood_agent.py:1409-1422`）；子线程同事件（`child_thread_runtime.py:304-318`） |
| 空配置 | `fallbacks` 为空或 `max_fallbacks=0` 时原样返回 primary（156-157） |

主代理接线的两个入口：

- 默认路径与旧 `llm_service` 路径：`_init_model_client()` 构造后立即包回退链（`native_flood_agent.py:1376-1414`）。
- 宿主直接注入 `ModelClient` 实例：`self._model_client = self.llm_service`（`native_flood_agent.py:620-621`）发生在 `_init_model_client()` 之后，**跳过回退链包装**。这是当前唯一绕过回退的路径。

子代理：

- `build_subagent_model_client()`（`model_gateway.py:173-190`）：`model_routing.subagent_model` 非空则单独解析，否则继承父模型。
- 回退链与 token 配额包装叠加：primary 与每个 fallback 都套 `_TokenBudgetModelClient`（`child_thread_runtime.py:308-320`）。

### 2.4 重试

`RetryPolicy(max_retries=3, base_delay=2.0, max_delay=30.0)` 在执行器内构造（`executor.py:780`），不是配置项。

决策链（`executor.py:810-879`）：

1. 本轮实际观察到 token/reasoning/tool_call 即视为 `response_started`，补进 advice（818-823）。
2. `should_retry(advice, terminal_reason)`（`retry.py:23-49`）：
   - `retry_suggested=False` → 否；
   - 终态 `refusal/content_filter/length/max_tokens/pause` → 否；
   - `response_started and not replay_safe` → 否。
3. 不可重试：发 `error` + 结束本轮，`status=failed`。
4. 可重试：先检查 abort；发 `retry_attempt` 与 `wait(retry_backoff)`；清空本轮半截输出；`_sleep_with_abort()` 每 0.2s 轮询中断（`executor.py:1536-1558`）。
5. 重试成功后发 `recover` 事件（`executor.py:806-809`）。

## 3. Provider 与方言

### 3.1 路由

`route_codec(provider_id, model_id, base_url)`（`providers/__init__.py:48-74`）按分数选择：

```text
base_url 精确匹配(100) > provider id(60) > 模型名前缀(40) > OpenAI 兼容兜底
```

- 连线方言由 base_url / provider 决定；模型个性由 codec 内部按模型名分支。
- 仅模型名前缀命中（如聚合网关托管的 `MiniMax/xxx`）时置 `conservative=True`：解析适配保留，请求适配退化为标准 OpenAI，避免网关拒绝厂商方言参数（`providers/__init__.py:4-9`）。

### 3.2 方言表

| Provider | Codec | 备注 |
|---|---|---|
| `dashscope` | `providers/dashscope.py` | URL 含 `dashscope`/`aliyuncs.com` 得 100；provider id 为 `dashscope/qwen/aliyun` 得 60；不看模型名前缀（22-28） |
| `deepseek` | `providers/deepseek.py` | reasoning content 单独字段 |
| `kimi` | `providers/kimi.py` | 工具调用格式差异 |
| `minimax` | `providers/minimax.py` | 多模态与图像输入 |
| `openai_compatible` | `providers/openai_compatible.py` | 兜底方言 |
| Anthropic | 无直连 transport | 官方 `api.anthropic.com` 在构造时被拒绝；只支持 OpenAI-compatible 网关（`provider_registry.py:169-183`；`model_client.py:49-50`） |

Codec 接口在 `providers/codec.py`，`base.py` 提供公共请求构造与 delta 解析。

## 4. 配置解析

### 4.1 `resolve_model()`

`resolve_model(model_key, provider_id)` 是唯一入口（`model_resolver.py:122-178`）。

优先级：

1. 显式 `model_key` 参数；
2. `FLOODMIND_MODEL` 环境变量；
3. catalog 第一个模型（`settings.json` 中 providers 的声明顺序）。

`FLOODMIND_PROVIDER` 同理限定服务商。`model_key` 必须在 catalog 中精确命中，否则抛错，不做静默错配。

连接信息优先级（`model_resolver.py:236-249`）：

- `api_key`：`FLOODMIND_API_KEY` > provider 配置 > `PROVIDER_DEFS` 声明的服务商环境变量。
- `base_url`：provider 配置 > `FLOODMIND_BASE_URL` > `PROVIDER_DEFS` 默认地址。

注意与 `.env.example` 的表述冲突：该文件写“环境变量优先级低于 settings.json”（`.env.example:6-9`），但 `FLOODMIND_API_KEY` 实际覆盖配置。

### 4.2 能力字段与窗口

`context_window`、`temperature`、`max_tokens` 只取自模型自身定义，不从其他字段回退：

- `context_window` 缺省 32768；`<=0` 或超过上界时告警并回退默认（`model_resolver.py:265-288`）。
- 绝不使用 `maxTokens`（生成上限）推导记忆窗口。

`resolve_context_window()` 是窗口唯一入口（`model_resolver.py:181-227`）：catalog 命中取该模型；未命中回退 catalog 默认模型；catalog 为空回退 32768。SDK 与 `NativeFloodAgent` 都委托它（`api.py:192-201`；`native_flood_agent.py:1351-1363`）。

`model_presets.py` 不再自行解析：`get_preset`/`get_default_model_key`/`get_models_list` 都委托 `model_resolver.list_models()`（`model_presets.py:12-45`）。

`ModelConfig`（`settings.py:462-549`）也是 `resolve_model()` 的门面，`settings.model.model_name` setter 仅修改进程内 `_active_key`，不写配置文件。

### 4.3 `model_routing`

默认值（`settings.py:203-207`；`settings_template.json:75-79`）：

```json
"model_routing": {
  "subagent_model": "",
  "fallbacks": [],
  "max_fallbacks": 2
}
```

- `fallbacks` 必须是模型 ID 字符串数组；`max_fallbacks` 必须是整数。
- 回退目标在 Agent 初始化时逐个解析，任何 ID 解析失败都会让启动失败，而不是运行时静默跳过。

## 5. 能力表

`ModelCapabilities`（`capabilities.py:16-28`）字段：

- `context_window`
- `supports_tools`
- `supports_parallel_tools`
- `supports_reasoning`
- `supports_vision`
- `supports_documents`
- `supports_structured_output`
- `supports_stream_usage`
- `supports_server_compaction`
- `transport_family`、`reasoning_replay_mode`

注册表合并顺序：**provider defaults → family override → exact override**，每层只覆盖显式设置的字段（`capabilities.py:40-79`）。

实际消费：

- 执行器在构造与每轮开始时解析一次并缓存快照（`executor.py:223`、`:361`、`:1883-1900`）。
- `_resolve_capabilities()` 在 `caps.context_window` 为空时回退为仅含 `self.context_window` 的能力，保证预算可计算。
- `compute_input_budget(caps)`（`projection.py:19-32`）用 `context_window` 减去输出、工具、协议开销与安全余量。
- 默认注册表只显式设置部分 `supports_*` 标志，**从不设置 `context_window`**（`capabilities.py:82-97`），所以窗口实际来自 `resolve_context_window()` 的回退。
- 其余 `supports_*` 标志目前没有消费者（前端目录与 `enable_thinking` 判断读的是 `model_presets` 的模型字段，不是能力表）。

### 5.1 评测与基准覆盖

- `evals` 用脚本化模型客户端替换真实 `ModelClient`，驱动真实执行器；覆盖脚本轨迹、工具往返、非法工具参数、模型错误终态、token 预算与中断。它验证执行器对模型事件的消费，不验证供应商 codec 的 wire 格式。
- `benchmarks/multi_agent` 的 `--live` 路径才真实调用模型（读取 settings 的模型配置并走 `Agent` 完整 runtime）；默认离线模式不产生模型调用。CI 只跑 `pytest` 与 `evals.runner`，不跑 `--live`。
- 供应商方言由 `tests/` 下的录制/回放或 codec 单测覆盖，不由 evals 覆盖。

## 6. 风险清单

| # | 风险 | 证据 | 影响 |
|---|---|---|---|
| 1 | LLM 超时硬编码 90s | `transport.py:34`、`model_client.py:37` | 长回答被截断，无法按部署调整 |
| 2 | 重试次数/退避硬编码 3/2s/30s | `executor.py:780` | 无法按供应商调整；高延迟链路可能过早失败 |
| 3 | `.env.example` 优先级描述与实际相反 | `.env.example:6-9` vs `model_resolver.py:236-249` | 部署时误判谁覆盖谁 |
| 4 | CLI `--reasoning` 未贯通 | `cli.py:587-588` 只改 `settings.model.enable_reasoning`；`from_settings()` 不读该字段，默认 `enable_thinking=False`（`model_client.py:68`） | 命令行开启推理无效 |
| 5 | CLI `--model` 只部分生效 | `cli.py:251`/`:586` 只改 `settings.model._active_key`；`settings.model.temperature/max_tokens` 会跟随，但 `from_settings()` 的 `model_name/api_key/base_url` 来自无参 `resolve_model()`（`model_client.py:76-82`） | 实际请求可能仍打到 catalog 第一个模型，参数却来自 `--model` 指定的模型 |
| 6 | 两套 `TokenUsage` | `providers/usage.py:16` 仅被重新导出（`providers/__init__.py:22`）；实际消费 `types.py:155` | 富 usage 字段（缓存 token 等）不会进入 canonical 流 |
| 7 | 同名模型跨 provider 解析不一致 | `resolve_model` 返回 catalog 中第一个（`model_resolver.py:145-151`）；`get_preset` 字典赋值后者覆盖前者（`model_presets.py:12-49`） | 前端展示与运行时可能选到不同供应商 |
| 8 | 能力表标志位无消费者 | `capabilities.py:82-97` 的 `supports_*` 在运行时无读取点 | 能力声明是文档性的，不能驱动行为 |
| 9 | 默认能力表不写窗口 | `capabilities.py:82-97` | 窗口由 `resolve_context_window()` 注入执行器；直接消费默认注册表的调用方拿不到窗口 |
| 10 | 官方 Anthropic 端点不可用 | `provider_registry.py:169-183` 直接拒绝 | 只能接 OpenAI-compatible 网关，不能用 Messages API |
| 11 | 宿主注入 `ModelClient` 跳过回退链 | `native_flood_agent.py:620-621` 在 `_init_model_client()` 之后覆盖 | 桌面/宿主注入模型时 `model_routing.fallbacks` 不生效 |
| 12 | `replay_safe` 始终为 False | `TransportRetryAdvice` 默认值（`transport.py:12-17`），transport 从不赋值 | 只要已开始输出就绝不自动重试（保守，但无法利用供应商明确的可重放信号） |

已关闭的旧风险（不再列入）：

- 重试不可中断：`_sleep_with_abort` 轮询 abort（`executor.py:1536-1558`）。
- 重试建议字段完全未填充：执行器补齐 `response_started`（`executor.py:818-823`）。
- 能力表空转：执行器解析并用于预算（`executor.py:1883-1900`）。
- 窗口回退不一致：`resolve_context_window()` 唯一入口，`model_presets` 委托 resolver。
- 密钥明文：`secret_store.py` 使用 DPAPI/Fernet，settings 保存时 `protect_tree`、加载时 `unprotect_tree`（`secret_store.py:9-13`、`:199-241`；`settings.py:145`、`:372`、`:410`）。
- 客户端缓存键弱哈希：`ModelClient` 已无客户端缓存，旧风险失效。
- DashScope 路由漏配：`match()` 同时检查 URL 与 provider id（`providers/dashscope.py:22-28`）。

## 7. 修改影响提示

| 变更 | 必查 |
|---|---|
| `ModelClient` 签名 | `Agent._resolve_context_window`、`from_settings`、CLI、`model_gateway`、测试 |
| Codec 协议 | 对应 provider 的录制/回放测试、`ResponsePipeline` 单测 |
| 流式 part | `canonical_parts.py`、executor 消费点、SDK 事件 |
| 能力表 | `compute_input_budget`、压缩触发、前端模型展示、`resolve_model` 输出 |
| 配置优先级 | `.env.example`、`settings.ModelConfig`、`model_resolver`、CLI `providers` 命令 |
| 回退链 | `model_gateway`、`reset_fallbacks` 调用点、`model_fallback` 事件、子线程 token 配额包装、宿主注入 `ModelClient` 路径 |
| 子代理路由 | `model_routing.subagent_model`、`build_subagent_model_client`、`child_thread_runtime` |
| 重试/超时 | `executor.py:780-879`、`retry.should_retry`、abort 语义、`wait`/`retry_attempt`/`recover` 事件 |
| 密钥 | `secret_store.py`、`settings.save/load`、`mcp.json` 读写、备份与迁移脚本 |
| 窗口 | `resolve_context_window`、`DualMemory` 构造、`ContextCompressor`、`compute_input_budget` |

## 8. 建议的收敛方向

1. 超时、重试次数、退避上限进入 settings；`replay_safe` 由 transport 按供应商语义显式赋值。
2. 统一 `TokenUsage`，让 canonical 流保留缓存 token 等字段。
3. 统一 `.env.example` 与 resolver 的优先级描述。
4. 把 CLI `--model` / `--reasoning` 贯通到 `ModelClient.from_settings`（显式传参，或让 `from_settings` 读取 `settings.model` 的 override）。
5. 同名模型跨 provider 时要求 `provider/id` 形式或显式 provider 限定，避免目录覆盖。
6. 宿主注入 `ModelClient` 时也套回退链（或明确文档化为“宿主自负路由”）。
7. 让能力表的 `supports_*` 至少驱动一个真实决策（如工具调用降级、视觉输入拒绝、流式 usage）。
