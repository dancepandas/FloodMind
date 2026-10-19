# 结构与依赖指标

> 生成时间：2026-10-19T11:12:26+08:00

## 总览

| 指标 | 数值 |
|---|---:|
| 文件 | 380 |
| 符号 | 4608 |
| 边 | 8677 |
| import 边 | 1080 |
| call 边 | 2963 |
| inherit 边 | 26 |
| 分层数 | 21 |
| 文件级循环 | 4 |
| 未解析导入 | 1 |
| 无入边文件 | 213 |

## 分层规模

| 分层 | 文件 | LOC | 符号 |
|---|---:|---:|---:|
| 测试 (`tests`) | 137 | 26021 | 2184 |
| 技能 (`skills`) | 65 | 13322 | 455 |
| Agent 执行核心 (`agent-core`) | 23 | 9373 | 372 |
| 运行时服务 (`runtime-services`) | 35 | 8787 | 408 |
| 记忆与会话 (`memory`) | 11 | 4604 | 241 |
| 内部评测集 (`evals`) | 5 | 4349 | 65 |
| 工具 (`tools`) | 6 | 4116 | 164 |
| 工程脚本 (`tooling`) | 14 | 3450 | 66 |
| 模型与配置 (`model-config`) | 13 | 2767 | 161 |
| Gateway（HTTP/SSE 适配） (`gateway`) | 7 | 1725 | 67 |
| 运行时契约 (`runtime-contracts`) | 20 | 1389 | 110 |
| Agent 辅助服务 (`agent-services`) | 5 | 1092 | 70 |
| SDK 公共入口 (`sdk`) | 4 | 991 | 42 |
| CLI/宿主入口 (`cli`) | 5 | 977 | 26 |
| 收益基线 (`benchmarks`) | 6 | 953 | 45 |
| MCP 集成 (`mcp`) | 1 | 677 | 42 |
| Reducer/运行时内核 (`runtime-kernel`) | 2 | 586 | 36 |
| 扩展点 (`extension`) | 6 | 363 | 20 |
| 运行时适配器 (`runtime-adapters`) | 9 | 319 | 18 |
| 其它 (`other`) | 4 | 254 | 3 |
| 通用工具 (`common`) | 2 | 135 | 13 |

## 分层依赖（跨层 import 计数）

| 从 | 到 | 次数 |
|---|---|---:|
| `tests` | `runtime-services` | 172 |
| `tests` | `runtime-contracts` | 125 |
| `tests` | `agent-core` | 121 |
| `tests` | `model-config` | 50 |
| `runtime-services` | `runtime-contracts` | 36 |
| `tests` | `tools` | 34 |
| `tests` | `memory` | 30 |
| `agent-core` | `runtime-contracts` | 22 |
| `agent-core` | `runtime-services` | 20 |
| `tests` | `runtime-kernel` | 18 |
| `agent-core` | `model-config` | 14 |
| `tests` | `sdk` | 14 |
| `tests` | `skills` | 12 |
| `tools` | `runtime-services` | 10 |
| `agent-core` | `tools` | 8 |
| `runtime-services` | `agent-core` | 7 |
| `cli` | `model-config` | 6 |
| `tools` | `runtime-contracts` | 6 |
| `memory` | `runtime-services` | 5 |
| `tools` | `memory` | 5 |
| `tests` | `agent-services` | 5 |
| `evals` | `tools` | 4 |
| `evals` | `agent-core` | 4 |
| `cli` | `runtime-services` | 4 |
| `runtime-services` | `runtime-kernel` | 4 |
| `gateway` | `runtime-services` | 4 |
| `gateway` | `runtime-contracts` | 4 |
| `tests` | `evals` | 4 |
| `tests` | `benchmarks` | 4 |
| `tests` | `extension` | 4 |
| `sdk` | `model-config` | 3 |
| `sdk` | `agent-core` | 3 |
| `sdk` | `runtime-services` | 3 |
| `agent-core` | `memory` | 3 |
| `model-config` | `agent-core` | 3 |
| `model-config` | `runtime-contracts` | 3 |
| `agent-core` | `extension` | 3 |
| `runtime-kernel` | `runtime-contracts` | 3 |
| `runtime-adapters` | `runtime-contracts` | 3 |
| `runtime-adapters` | `runtime-services` | 3 |
| `runtime-services` | `model-config` | 3 |
| `gateway` | `model-config` | 3 |
| `tooling` | `model-config` | 3 |
| `tests` | `tooling` | 3 |
| `tests` | `mcp` | 3 |
| `cli` | `memory` | 2 |
| `cli` | `agent-services` | 2 |
| `evals` | `runtime-contracts` | 2 |
| `evals` | `runtime-services` | 2 |
| `cli` | `agent-core` | 2 |
| `sdk` | `memory` | 2 |
| `sdk` | `runtime-contracts` | 2 |
| `mcp` | `runtime-contracts` | 2 |
| `agent-core` | `agent-services` | 2 |
| `agent-core` | `skills` | 2 |
| `runtime-services` | `tools` | 2 |
| `runtime-services` | `common` | 2 |
| `gateway` | `sdk` | 2 |
| `memory` | `model-config` | 2 |
| `tools` | `skills` | 2 |
| `tools` | `model-config` | 2 |
| `tests` | `cli` | 2 |
| `tests` | `gateway` | 2 |
| `benchmarks` | `sdk` | 1 |
| `benchmarks` | `runtime-contracts` | 1 |
| `benchmarks` | `memory` | 1 |
| `evals` | `model-config` | 1 |
| `cli` | `sdk` | 1 |
| `cli` | `gateway` | 1 |
| `cli` | `skills` | 1 |
| `agent-services` | `agent-core` | 1 |
| `agent-services` | `sdk` | 1 |
| `agent-services` | `runtime-contracts` | 1 |
| `agent-services` | `model-config` | 1 |
| `mcp` | `runtime-services` | 1 |
| `agent-services` | `common` | 1 |
| `agent-core` | `runtime-kernel` | 1 |
| `agent-core` | `sdk` | 1 |
| `agent-core` | `mcp` | 1 |
| `runtime-contracts` | `tools` | 1 |
| `runtime-services` | `memory` | 1 |
| `runtime-services` | `sdk` | 1 |
| `runtime-services` | `skills` | 1 |
| `gateway` | `memory` | 1 |
| `gateway` | `common` | 1 |
| `memory` | `skills` | 1 |
| `extension` | `tools` | 1 |
| `extension` | `model-config` | 1 |
| `tools` | `agent-services` | 1 |
| `tooling` | `evals` | 1 |
| `tooling` | `runtime-contracts` | 1 |
| `tooling` | `runtime-services` | 1 |
| `tests` | `runtime-adapters` | 1 |

## 高连接文件（import 入度 + 出度）

| 文件 | 入度 | 出度 | 合计 |
|---|---:|---:|---:|
| `floodmind/agent/native/native_flood_agent.py` | 21 | 52 | 73 |
| `floodmind/agent/runtime/services/journal_authority.py` | 44 | 6 | 50 |
| `floodmind/agent/native/model_client.py` | 38 | 10 | 48 |
| `floodmind/agent/native/types.py` | 44 | 1 | 45 |
| `floodmind/agent/native/executor.py` | 20 | 19 | 39 |
| `floodmind/config/settings.py` | 28 | 3 | 31 |
| `floodmind/agent/runtime/contracts/tools.py` | 27 | 3 | 30 |
| `floodmind/tools/base_tools.py` | 13 | 17 | 30 |
| `floodmind/agent/runtime/contracts/permissions.py` | 29 | 0 | 29 |
| `floodmind/agent/native/event_bus.py` | 26 | 0 | 26 |
| `floodmind/agent/runtime/contracts/canonical_events.py` | 26 | 0 | 26 |
| `floodmind/agent/runtime/reducer.py` | 23 | 3 | 26 |
| `floodmind/agent/runtime/services/path_service.py` | 18 | 7 | 25 |
| `floodmind/agent/runtime/services/workspace_service.py` | 21 | 3 | 24 |
| `floodmind/agent/runtime/contracts/runtime_context.py` | 23 | 0 | 23 |
| `floodmind/agent/runtime/services/permission_service.py` | 19 | 3 | 22 |
| `floodmind/agent/runtime/services/child_thread_runtime.py` | 5 | 16 | 21 |
| `floodmind/tools/session_context.py` | 21 | 0 | 21 |
| `floodmind/agent/native/message_builder.py` | 19 | 1 | 20 |
| `floodmind/agent/runtime/contracts/workspace.py` | 20 | 0 | 20 |
| `floodmind/tools/agent_tool.py` | 13 | 7 | 20 |
| `tests/test_sdk_agent.py` | 0 | 20 | 20 |
| `floodmind/agent/api.py` | 6 | 13 | 19 |
| `floodmind/agent/runtime/services/tool_execution_service.py` | 10 | 9 | 19 |
| `floodmind/agent/runtime/services/background_task_service.py` | 13 | 5 | 18 |
| `floodmind/cli.py` | 3 | 15 | 18 |
| `tests/test_executor.py` | 0 | 17 | 17 |
| `floodmind/agent/runtime/services/checkpoint_service.py` | 10 | 6 | 16 |
| `tests/test_child_thread_runtime.py` | 0 | 16 | 16 |
| `tests/test_p7_acceptance.py` | 0 | 16 | 16 |

## 被调用最多的符号（call 入度）

| 符号 | 位置 | 入度 | 出度 |
|---|---|---:|---:|
| `symbol:tests.test_guardrails._context` | `tests/test_guardrails.py:63` | 32 | 0 |
| `symbol:tests.test_guardrails._make_executor` | `tests/test_guardrails.py:43` | 32 | 0 |
| `symbol:floodmind.agent.runtime.reducer._clone` | `floodmind/agent/runtime/reducer.py:29` | 24 | 0 |
| `symbol:tests.test_permission_service.TestPermissionService._make_svc` | `tests/test_permission_service.py:16` | 23 | 0 |
| `symbol:floodmind.memory.session_manager.validate_session_id` | `floodmind/memory/session_manager.py:35` | 22 | 0 |
| `symbol:tests.test_executor.TestNativeAgentExecutor._make_context` | `tests/test_executor.py:40` | 22 | 0 |
| `symbol:tests.test_executor.TestNativeAgentExecutor._make_executor` | `tests/test_executor.py:18` | 22 | 0 |
| `symbol:tests.test_plan_update._make_bare_agent` | `tests/test_plan_update.py:15` | 22 | 0 |
| `symbol:tests.test_plan_update._make_plan` | `tests/test_plan_update.py:26` | 22 | 0 |
| `symbol:tests.test_guardrails._token_done` | `tests/test_guardrails.py:76` | 20 | 0 |
| `symbol:tests.test_provider_pipelines._base_params` | `tests/test_provider_pipelines.py:124` | 20 | 0 |
| `symbol:tests.test_tool_execution_service._ctx` | `tests/test_tool_execution_service.py:210` | 19 | 0 |
| `symbol:floodmind.agent.native.event_bus.EventBus.emit` | `floodmind/agent/native/event_bus.py:55` | 17 | 0 |
| `symbol:floodmind.agent.runtime.reducer._is_current_thread` | `floodmind/agent/runtime/reducer.py:39` | 17 | 0 |
| `symbol:floodmind.memory.task_experience.TaskExperienceStore.tree` | `floodmind/memory/task_experience.py:151` | 16 | 0 |
| `symbol:tests.test_journal_writer._evt` | `tests/test_journal_writer.py:12` | 16 | 0 |
| `symbol:tests.test_model_routing._FakeClient` | `tests/test_model_routing.py:22` | 16 | 0 |
| `symbol:tests.test_provider_pipelines._ns` | `tests/test_provider_pipelines.py:24` | 16 | 0 |
| `symbol:tests.test_reducer._ev` | `tests/test_reducer.py:8` | 16 | 0 |
| `symbol:tests.test_handoff._mc` | `tests/test_handoff.py:23` | 14 | 0 |
| `symbol:tests.test_mcp_client.FakeConn` | `tests/test_mcp_client.py:24` | 14 | 0 |
| `symbol:tests.test_mcp_config_validation._patched_home` | `tests/test_mcp_config_validation.py:39` | 14 | 0 |
| `symbol:tests.test_handoff.Registry` | `tests/test_handoff.py:49` | 13 | 0 |
| `symbol:tests.test_plan_persistence._make_bare_agent` | `tests/test_plan_persistence.py:18` | 13 | 0 |
| `symbol:tests.test_file_tools._bind_workspace` | `tests/test_file_tools.py:16` | 12 | 0 |
| `symbol:tests.test_file_tools._reset` | `tests/test_file_tools.py:23` | 12 | 0 |
| `symbol:tests.test_handoff._executor` | `tests/test_handoff.py:35` | 12 | 0 |
| `symbol:floodmind.config.search_config.load_search_config` | `floodmind/config/search_config.py:50` | 11 | 1 |
| `symbol:tests.test_background_task_service._sleep_cmd` | `tests/test_background_task_service.py:17` | 11 | 0 |
| `symbol:tests.test_execution_plan._make_plan` | `tests/test_execution_plan.py:8` | 11 | 0 |
