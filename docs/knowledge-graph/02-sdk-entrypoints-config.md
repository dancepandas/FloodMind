# 02. SDK、入口与配置面

> 面向宿主集成与公共 API 修改。代码位置：`floodmind/__init__.py`、`floodmind/agent/api.py`、`floodmind/agent/sdk_events.py`、`floodmind/cli.py`、`floodmind/config/`、`pyproject.toml`。

## 1. 公共导出

`floodmind/__init__.py` 使用惰性 `__getattr__`，`__all__` 共 33 个符号（`floodmind/__init__.py:91`），`__version__ = "2.3.0"`。核心导出：

| 类别 | 符号 |
|---|---|
| Agent | `Agent`、`NativeFloodAgent`、`create_flood_agent` |
| 模型 | `ModelClient`、`resolve_model`、`ResolvedModel`、`route_codec`、`ProviderCodec`、`StreamState`、`TokenUsage`、各供应商 Codec |
| 工具 | `AgentTool`、`build_agent_tool`、`ToolLoadingConfig`、`ToolCatalogEntry`、`ToolLoader` |
| 记忆 | `DualMemory` |
| 工作区/技能/插件 | `Workspace`、`build_folder_workspace`、`build_workspace`、`Skill`、`SkillRegistry`、`SkillRoot`、`register_skill`、`FloodmindPlugin` |
| MCP | `get_mcp_client_pool`、`build_mcp_tool_specs` |
| 配置 | `ToolLoadingConfig`、模型解析相关 |

注意：`ToolSpec` 不在顶层导出，宿主需从 `floodmind.agent.runtime.contracts`（或 `floodmind.agent.runtime.contracts.tools`）导入；`Guardrail`/`Handoff` 相关类型可经 `__getattr__` 从顶层导入，但不在 `__all__` 中。修改公共面时先确认是否有外部消费者。

## 2. `Agent` 生命周期

`Agent` 位于 `floodmind/agent/api.py:50`，构造时立即创建 `NativeFloodAgent(bare=True)`（`api.py:167`）。构造函数参数分四组：

| 组 | 参数 | 说明 |
|---|---|---|
| 必需 | `llm` | `ModelClient` 实例 |
| 执行 | `tools`、`system_prompt`、`memory`、`session_id`、`max_iterations=999`、`max_total_tokens=0`、`workspace`、`tool_loading`、`bare=True` | `session_id` 为空时生成 `sdk-<uuid>` 并校验格式；`max_total_tokens=0` 表示不限额 |
| 能力 | `enable_search`、`enable_reasoning`、`skill_roots`、`skill_writable_root`、`mcp_pool` | `skill_writable_root` 是 CRUD 与策展状态的唯一写入根；`skill_roots` 为额外只读根 |
| 治理 | `permission_handler`、`permission_decision_hook`、`input_guardrails`、`output_guardrails`、`trace_processors`、`handoffs`、`on_event` | `on_event` 禁止 async 函数，构造函数直接 `TypeError`（`api.py:158`） |

对外方法：

| 方法 | 行为 |
|---|---|
| `run(message)` | 消费 `_iter`，优先取 `final_text`，否则累加 `answer_delta`（`api.py:469`） |
| `stream(message, **kwargs)` | 统一走 `_iter`，透传 `abort_check`、`attachments`（`api.py:484`） |
| `chat(message)` | `run` 别名 |
| `resume(checkpoint_id, user_message="")` | 走 `CheckpointService` + `ResumeService`，绑定恢复后的 authority 到 executor 后 `run_from_state`（`api.py:254`） |
| `events_after(sequence)` | 通过 `project_canonical_many` 返回 committed 事件（`api.py:245`） |
| `bind_workspace(ws)` | 下一轮生效，替代跨线程 contextvar（`api.py:203`） |
| `list_background_tasks()` / `kill_background_task(id)` | 后台任务公开面（`api.py:405`/`:416`） |
| `clear_memory()` / `cleanup()` | 记忆与资源释放 |

只读属性：`last_usage`、`artifacts`、`skill_registry`、`raw`、`session_id`、`conversation_id`、`task_id`、`run_id`、`thread_id`。

`raw` 暴露底层 `NativeFloodAgent` 与 `_agent._orchestrator_executor`。`resume` 内部直接访问 `self._agent._orchestrator_executor`（`api.py:356`）、`_permission_service`、`_path_service`、`_background_task_service`（`api.py:333-335`），这是公开 SDK 与内部实现耦合最重的位置。

## 3. 事件契约

两层事件：

1. **流式预览事件**：`answer_delta`、`thought_delta`、`final_text`、`action_start`、`action_end`、`workflow_plan`、`workflow_step`、`llm_step_start`、`llm_step_end`、`retry_attempt`、`wait`、`recover`、`resume`、`model_fallback`、`context_compress_start/done`、`file_generated`、`image_generated`、`token_usage`、`heartbeat`、`error`、`llm_token_error`、`permission_ask`、`permission_resolved`、`attachment_context`。
2. **committed 事件**：canonical 事件经 `sdk_events.project_canonical` 投影为 `text_committed`、`tool_result` 等，带 `sequence`。

`sdk_events.py` 的职责是投影，不是事实源。`events_after` 读取的是 Journal，因此可用于预览事件与 committed 事件的对账。

已知缺口：`SdkEvent.part_id` 字段存在但从未被填充（`sdk_events.py:38-57` 只填 `attempt_id`）；`resume.started`/`resume.completed` 不在 `_CANONICAL_TO_SDK` 映射中，`events_after` 不会返回它们，恢复进度只能从执行期事件总线的 `resume` 预览事件观察（`api.py:363-377`）。

## 4. CLI 与入口

| 入口 | 位置 | 说明 |
|---|---|---|
| 控制台脚本 | `pyproject.toml`：`floodmind = floodmind.cli:main` | 主入口 |
| 旧入口 | `main.py` | 直接把 dict 事件打印出来，不经过 SDK 投影 |
| 交互模式 | `floodmind/cli_interactive.py` | 与 `cli.py` 互相导入，形成文件级循环 |
| 网关 | `floodmind/gateway/` | 活跃适配层；`floodmind gateway` 启动，见 08 |

CLI 的 resume 路径（`cli.py:271-303` 附近）是 `Agent.resume` 的参考实现，两者应保持一致。

## 5. 配置面

### 5.1 解析优先级

| 配置项 | 优先级（高 → 低） |
|---|---|
| 模型 | 显式传参 > `FLOODMIND_MODEL` > 模型目录第一项 |
| API Key | `FLOODMIND_API_KEY` > provider 配置 `api_key` > provider 环境变量 |
| Base URL | provider 配置 `base_url` > `FLOODMIND_BASE_URL` > 默认 |
| 通用设置 | 环境变量 > `~/.floodmind/settings.json` > 默认值 |
| 旧配置 | `~/.config/floodmind/config.json` 最后合并，**可覆盖新配置** |

配置解析的唯一入口是 `floodmind/config/model_resolver.py:resolve_model`；`provider_registry.py` 定义供应商，`model_presets.py` 是前端模型目录，`settings.py` 承载全局设置与缓存。

### 5.2 模型路由（G-19）

`settings.json` 的 `model_routing` 段由 `floodmind/agent/native/model_gateway.py:routing_config` 读取（默认值见 `settings.py:203-207`，模板见 `settings_template.json:75-79`）：

| 键 | 默认 | 语义 |
|---|---|---|
| `subagent_model` | `""` | 子代理使用的模型 key；为空时继承父代理模型 |
| `fallbacks` | `[]` | 主模型失败时可切换的模型 key 列表，必须能在 providers/catalog 中解析 |
| `max_fallbacks` | `2` | 最多使用几条回退，`0` 表示禁用 |

边界：
- 只有本轮尚未输出任何事件时才切换；已输出后失败原样上抛（`model_gateway.py:62-87`）。
- 回退成功后同一 run 内保持该模型；`NativeAgentExecutor.run_from_state` 每次 run 开始调用 `reset_fallbacks()`（`executor.py:463-465`）。
- 切换通过 `on_fallback` 发 `model_fallback` 事件（`native_flood_agent.py:1416-1423`；子线程 `child_thread_runtime.py:304-315`）。
- `Agent(llm=...)` + `bare=True`（SDK 默认）不会再次包装注入的客户端；完整运行时与子代理路径会经过网关。

### 5.3 成本与观测

`settings.json` 新增两段配置，均由 `TracingService.from_settings()` 在构造时读取：

| 键 | 默认 | 语义 |
|---|---|---|
| `model_pricing` | `{}` | `{model: {"input_per_1k", "output_per_1k", "currency"}}`；未定价模型成本为 `None`，不会伪造 0 |
| `observability.otlp_endpoint` | `""` | OTLP/HTTP collector 根地址或完整 `/v1/traces`；为空时只落本地 trace |
| `observability.otlp_headers` | `{}` | 附加请求头（如鉴权）；非对象直接报错 |
| `observability.otlp_timeout_seconds` | `5.0` | 导出超时；必须大于 0 |

成本按 run / model / step / tool 聚合；导出失败只记 warning，不阻断本地 `flush`。离线报告见 `scripts/metrics_report.py`。

### 5.4 已知配置问题

1. `.env.example` 声明环境变量“优先级低于 settings.json”，但 `settings.py:_cfg` 对 `api`/`workspace` 等键是环境变量优先，`model_resolver` 中 `FLOODMIND_API_KEY`/`FLOODMIND_MODEL` 也覆盖配置值。
2. 旧配置路径 `~/.config/floodmind/config.json` 仍然参与合并，并且后合并会覆盖新设置（`settings.py:364-369`）。
3. Profile 切换（`set_active_profile`）只清 `_active_profile_cache`，不清 `_config_cache`；同一进程内切换 profile 可能读到旧值（`settings.py:59-74`、`:376-383`）。
4. `settings.api`（`APIConfig`）定义了但生产代码无消费者；`settings.agent.max_iterations` 固定为代码常量 `DEFAULT_MAX_ITERATIONS=999`，而 SDK 默认 999、`NativeFloodAgent`/`NativeAgentExecutor` 默认 10000，三个值并未统一接线。
5. `--reasoning` CLI 参数只写 `settings.model.enable_reasoning`（`cli.py:587-588`），而 `ModelClient.from_settings` 不接受该值，当前没有消费者读到它。
6. 搜索配置固定读 `~/.floodmind/search.json`（`search_config.py:29`），插件目录固定为 `~/.floodmind/plugins/`（`plugin/loader.py:22`），都不随 profile 切换。

## 6. 打包与依赖

- `pyproject.toml` 定义 extras：`gateway`、`chainlit-ui`、`doc`、`deployment`、`all`；Docker 安装 `.[deployment]`。
- `pyproject.toml` 与 `MANIFEST.in` 不再引用已删除的 `floodmind/MCP` 目录。
- `requirements.txt` 与 `pyproject.toml` 存在漂移，以 `pyproject.toml` 为准。
- `floodmind.egg-info`、`dist/` 不应进入版本控制。
- CI 在 `.github/workflows/ci.yml`：Python 3.10/3.12 矩阵，`pip install -e . pytest` → `python -m pytest -q` → `python -m evals.runner --json eval-report.json`，并上传评测报告。

## 7. 修改影响提示

| 变更 | 必查 |
|---|---|
| `Agent.__init__` 参数 | 所有宿主编排、`tests/test_sdk*.py`、`desktop` 契约文档 |
| 事件名/字段 | `sdk_events.py`、前端消费方、`events_after` 对账 |
| `Agent.resume` | `cli.py` 同名逻辑、`ResumeService` 契约、checkpoint manifest 字段 |
| 配置优先级 | `model_resolver`、`settings`、`.env.example`、CLI `providers` 输出 |
| `model_routing` | `model_gateway.py`、`NativeFloodAgent._init_model_client`、`child_thread_runtime`、`tests/test_model_routing.py` |
| 公共导出 | `__all__`、`floodmind/agent/__init__.py`、文档示例 |

## 8. 已知风险摘要

- `bare=True` 的行为边界：按设计仍会注册后台任务查询/控制工具、MCP 外部工具与 `GetSkill`，只跳过内置文件/命令工具与插件（`native_flood_agent.py:586-625`）。宿主不应把 bare 理解为“零工具”。
- `Agent.resume` 绕过 `_iter`，因此 `on_event`、`last_usage`、`artifacts` 在恢复路径上不更新（`api.py:254-397`）；恢复进度只通过底层事件总线的 `resume` 预览事件发出。
- `agent_registry._load_merged` 用 `dict(BUILTIN_AGENTS)` 浅拷贝后原地修改 `AgentInfo`，多次加载会污染内置定义（`agent_registry.py:178-198`）。
- SDK `bare=True` 且宿主注入 `ModelClient` 时，不经过 `FallbackModelClient` 包装，配置中的 `model_routing.fallbacks` 对这条路径不生效；完整运行时（`bare=False`）与子代理路径会包装。
