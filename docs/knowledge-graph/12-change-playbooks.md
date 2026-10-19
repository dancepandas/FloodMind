# 12. 改动影响面与验证手册

> 用途：动手前先定位“会碰到谁”，动手后知道“跑什么验证”。

## 1. 通用流程

1. 在 `graph/metrics.md` 找到目标文件的入度/出度。
2. 在 `graph/graph.json` 查该文件的 `imports` 入边与出边。
3. 读对应子系统文档的“关键调用链”和“风险”。
4. 按下面的场景清单确定必测项。
5. 改完重新生成图，确认没有新增循环或跨层反向依赖；涉及执行链路时再跑 `evals`。

查边的快速方法（无需额外依赖）：

```bash
python - <<'PY'
import json
g = json.load(open("docs/knowledge-graph/graph/graph.json", encoding="utf-8"))
target = "floodmind/agent/native/executor.py"
for e in g["edges"]:
    if e["kind"] == "imports" and e["dst"] == f"file:{target}":
        print("in ", e["src"])
    if e["kind"] == "imports" and e["src"] == f"file:{target}":
        print("out", e["dst"])
PY
```

## 2. 新增/修改工具

| 步骤 | 说明 |
|---|---|
| 契约 | `AgentTool.to_tool_spec()` 是普通工具唯一转换点；`ToolSpec` 字段变更需同步 `tool_runtime.py`、`mcp_client.py`、`executor.py` 的直造点 |
| 注册 | `native_flood_agent.py` 的 `all_tools` 是 native 实际注册点（`:885-899`）；`base_tools._register_all_tools` 只写全局镜像，不生效 |
| specialist | 检查 `_is_specialist_builtin_safe`（`native_flood_agent.py:1285-1303`）：state_write 与指定管理工具会被剔除，执行类工具保留并由权限兜底 |
| 权限 | 写类工具声明副作用；路径类工具走 `PathService`；`state_write` 在策略层直接 ALLOW，必须靠 tier/planning 兜底 |
| 事务 | 确认 `idempotency_key` 与 `arguments_sha256` 的 canonical 形态 |
| 事件 | 执行器统一发 `tool.execution.*`；正常路径不发 `tool.result.committed`；MCP 的 `is_error` 只记健康，不转失败 |
| 输出 | 截断与全量卸载在 `tool_output_service.bound_tool_output`；`_finalize_tool_output` 只做重试保护 |
| 测试 | `tests/test_tool_execution_service.py`、`tests/test_permission_service.py`、`tests/test_tool_output_service.py`、新工具的独立测试 |

必测：

```bash
python -m pytest tests/test_tool_execution_service.py tests/test_permission_service.py \
  tests/test_path_service_workspace.py tests/test_tool_output_service.py -q
```

## 3. 新增供应商 / 换模型 / 回退

| 步骤 | 说明 |
|---|---|
| Codec | 在 `providers/` 新增 codec，实现请求编码与流式解码 |
| 注册 | `provider_registry.py` 增加供应商定义；`model_resolver.py` 确认解析 |
| 能力 | `capabilities.py` 写入 `context_window` 与实际能力；executor 通过 `_resolve_capabilities()` 消费 |
| 目录 | `model_presets.py` 增加展示项，避免与已有模型 ID 冲突 |
| 路由 | `settings.model_routing.subagent_model` 控制子代理模型；`fallbacks` / `max_fallbacks` 控制回退链 |
| 回退语义 | 只在尚未向宿主输出时切换；切换后本次 run 内粘滞；每次 run 开始复位；发出 `model_fallback` 事件 |
| 宿主注入 | `Agent(llm=...)` 注入的客户端不叠加配置回退链（模型与凭据由宿主负责），子代理仍按 `subagent_model` 路由 |
| 测试 | `test_provider_pipelines.py`、`test_codec.py`、`test_model_resolver.py`、`test_model_routing.py`、golden fixtures |
| 手工 | `scripts/smoke_provider_pipelines.py` |

必测：

```bash
python -m pytest tests/test_provider_pipelines.py tests/test_codec.py tests/test_response_pipeline.py \
  tests/test_model_resolver.py tests/test_model_routing.py tests/test_capabilities.py \
  tests/test_golden_fixtures.py -q
```

注意：`.env.example` 与 `README` 的优先级说明要与 `model_resolver` 一致。

## 4. 修改事件契约或 reducer

影响面最大，先读 04。

| 步骤 | 说明 |
|---|---|
| 事件类型 | `canonical_events.EVENT_TYPES` 与 reducer 分支同时改（`background.*` 目前只有 reducer 分支，缺 taxonomy） |
| payload | 新增字段必须有默认值；修改语义需要新事件类型或版本 |
| reducer | 决定“改状态”还是“仅推进 cursor”；`_advance_to` 对回退/非法边 fail-closed |
| 检查点 | `reducer_version` 是否升级；旧 checkpoint 会 fail-closed |
| 投影 | `history_projection`、`DualMemory`、`sdk_events` 同步 |
| 测试 | canonical、reducer、journal、checkpoint、golden fixtures、acceptance |

必测：

```bash
python -m pytest tests/test_canonical_events.py tests/test_reducer.py tests/test_tool_lifecycle_reducer.py \
  tests/test_journal_writer.py tests/test_journal_authority.py tests/test_journal_fencing.py \
  tests/test_checkpoint_service.py tests/test_checkpoint_cursor.py tests/test_history_projection.py \
  tests/test_golden_fixtures.py -q
```

## 5. 修改工具事务 / 权限 / ASK

| 检查项 | 位置 |
|---|---|
| 状态转移表 | `contracts/tool_transaction.py` |
| 事务推进 | `reducer.py` |
| 权限分层 | `permission_service.py`；子代理收缩 `_check_sub_agent_tier` |
| ASK 路径 | `permission_service._handle_ask` 走 `ask_service` 事件等待（`ask_service.py:187-205`） |
| 审批落定 | `reducer._reduce_approval_resolved` 当前会直接置 `awaiting_model`，改执行态时注意 |
| 指纹字段 | `approval_fingerprint.py` |
| 幂等 | `idempotency.py` 与执行器的参数形态 |
| 恢复落定 | `reconciliation_service.py`、`resume_service.py` |

必测：

```bash
python -m pytest tests/test_tool_transaction.py tests/test_approval_fingerprint.py \
  tests/test_tool_execution_service.py tests/test_permission_service.py tests/test_permission_tier.py \
  tests/test_resume_service.py tests/test_reconciliation_service.py tests/test_p3_acceptance.py -q
```

## 6. 修改记忆

| 子域 | 必读 | 必测 |
|---|---|---|
| 对话历史 | `dual_memory.py`、`history_projection.py` | `test_memory_dual.py`、`test_dual_memory_journal.py`、`test_history_projection.py` |
| 记忆门面 | `memory/services.py` | `test_memory_services.py`、`test_memory_expiry.py` |
| 语义库/固化 | `semantic_store.py`、`consolidation.py` | `test_semantic_store.py`、`test_memory_consolidation.py` |
| 核心记忆 | `core_memory.py`（项目级 `CoreMemoryStore`） | `test_core_memory_store.py`、`test_memory_tools.py` |
| 经验树 | `experience_tree.py`、`task_experience.py` | `test_experience_tree.py`、`test_task_experience.py` |
| 回顾 | `agent/native/background_review.py` | `test_background_review.py` |
| 会话 | `session_manager.py` | 无专门测试，需补 |

注意：

- `DualMemory` 已删除 `max_short_term` / `max_long_term` 等废弃参数，构造签名为 `(session_id, persist_dir, llm, context_window)`。
- 会话级 core memory 辅助函数已删除；核心记忆只走项目级 `CoreMemoryStore`。
- `MemoryAdd` 只写门面语义库（`tools/base_tools.py:1408-1427`，fail-closed）；旧 LTM 仅在 consolidation 禁用时由 `background_review` 旧直写回退追加（`background_review.py:236-251`），不属于工具写入路径。

必测：

```bash
python -m pytest tests/test_memory_dual.py tests/test_dual_memory_journal.py tests/test_memory_services.py \
  tests/test_memory_consolidation.py tests/test_memory_recall.py tests/test_memory_expiry.py \
  tests/test_semantic_store.py tests/test_core_memory_store.py tests/test_experience_tree.py \
  tests/test_task_experience.py tests/test_memory_tools.py tests/test_background_review.py -q
```

## 7. 修改 SDK 公共面

| 检查项 | 说明 |
|---|---|
| `Agent.__init__` | 新参数要有默认值，且不影响 `NativeFloodAgent` 既有调用 |
| 事件 | 新事件加入 `api.py` docstring 与 `sdk_events` 投影 |
| `resume` | 走 `ResumeService` replay + reconciliation，再 `run_from_state`；不要改回 `stream(resume_checkpoint_id=...)` |
| 对账 | `events_after(sequence)` 从当前 authority 读 canonical 事件 |
| 导出 | `floodmind/__init__.py` 的 `__all__` 与惰性 getattr |
| 纯净性 | 不引入网络/Flask/textual 等导入副作用 |

必测：

```bash
python -m pytest tests/test_sdk_agent.py tests/test_sdk_api_identity.py tests/test_sdk_events.py \
  tests/test_sdk_purity.py -q
```

## 8. 修改上下文/压缩

| 检查项 | 说明 |
|---|---|
| 提示词层 | `message_builder.py`、`context_runtime.py`（项目规则当前读 `Path.cwd()`） |
| 压缩 | `context_compressor.py`、`atomic_groups.py` |
| 投影预算 | `projection.py`、`contracts/projection.py` 的 `InputBudget`；单 run 预算在 `executor.py:666-672` |
| 历史视图压缩 | `DualMemory` 的 0.85/6、版本无关的内容缓存键 |
| 窗口来源 | `model_resolver.resolve_context_window(model_key=...)`，不要回到全局默认模型 |

必测：

```bash
python -m pytest tests/test_context_compressor.py tests/test_atomic_groups.py tests/test_input_budget.py \
  tests/test_projection_manifest.py tests/test_image_message_projection.py tests/test_token_budget.py \
  tests/test_native_context_window.py tests/test_p5_acceptance.py -q
```

## 9. 修改后台任务 / 子线程 / 沙箱

| 子域 | 必测 |
|---|---|
| 后台 | `test_background_task_service.py`、`test_background_kill_chain.py`、`test_background_reconcile.py` |
| 子线程 | `test_child_thread_runtime.py`、`test_child_thread_cancellation.py`、`test_child_thread_quota.py`、`test_child_permission_context.py`、`test_subagent_journal.py` |
| 沙箱 | `test_sandbox_service.py`、`test_sandbox_backend.py`、`test_child_sandbox_backend.py` |
| 产物 | `test_artifact_service.py`、`test_artifact_traversal.py`、`test_artifacts.py` |

平台差异：Windows 走 Job Object，Linux 走 Landlock；改动后至少在目标平台跑一次。

## 10. 修改配置/模型解析

| 检查项 | 说明 |
|---|---|
| 优先级 | 显式 > 环境变量 > settings > 默认；旧配置最后合并的行为要明确 |
| 缓存 | profile 切换或配置变更后清空 `_config_cache` |
| 敏感值 | 新增密钥类字段必须经过 `secret_store`，保存为 `enc:v1:` |
| 路由 | `model_routing` 的模型 id 必须可解析；未知 id 启动即报错 |
| MCP | `mcp.json` 是唯一契约：只允许 `transport`，出现 `type` 直接 `McpConfigError` |
| 展示 | CLI `providers`、前端 `/api/models` |
| 文档 | `.env.example`、README |

必测：

```bash
python -m pytest tests/test_settings.py tests/test_model_resolver.py tests/test_model_presets.py \
  tests/test_search_config.py tests/test_secret_store.py tests/test_credential_env.py \
  tests/test_mcp_config_validation.py -q
```

## 11. 新增/修改 Skill

| 检查项 | 说明 |
|---|---|
| 发现根 | `SkillRegistry` 默认根为 `skills/` 与 `.claude/skills`；contrib 需 `skill_roots` 注入 |
| 写入根 | `skill_writable_root` 决定 CRUD 与 curator 状态/归档位置 |
| 解析 | `_parse_skill_md` 的 YAML 边界、威胁扫描、knowledge-only 判定 |
| 写入 | `CreateSkill`/`UpdateSkill` 走 `_write_skill_md_verified`：回读逐字节校验、frontmatter 解析、失败回滚；但底层仍是一次 `write_text`，不是 tmp+replace |
| 策展 | 归档不覆盖已有；状态文件与维护标记有新旧两种路径 |
| 工具 | `GetSkill` 的 `lru_cache` 与 refresh 回调必须对称；`Skill.load_tools_module()` 目前无调用方，`tools.py` 不会自动注册 |

必测：

```bash
python -m pytest tests/test_skill_registry.py tests/test_skill_curator.py tests/test_skill_md_writeback.py \
  tests/test_get_skill_tool_factory.py tests/test_agent_skill_roots.py -q
```

## 12. 修改 MCP

| 检查项 | 说明 |
|---|---|
| 配置位置 | 运行时只读 `get_floodmind_home()/mcp.json`；项目内 `.floodmind/mcp.json` 是另一份，默认不加载 |
| 配置键 | 只接受 `transport`；出现 `type`、未知字段或非法必填项直接 `McpConfigError`（fail-fast） |
| Pool 生命周期 | 连接复用、重复工具名、断开子进程；单 server 失败不中止后续（`connect_all` 逐个捕获） |
| 传输 | `sse` 实为 Streamable HTTP；stdio 需终止进程树；超时与健康标记 |
| 隔离 | 每个 Agent 的 pool 不串扰；conftest 默认清空 servers |
| 命名 | 注册名被清洗为 `mcp_server_tool`，调用名仍是 `mcp:server:tool` |
| 错误语义 | `is_error` 只写入 call health；ToolSpec 仍返回字符串，执行器按 completed 处理 |

必测：

```bash
python -m pytest tests/test_mcp_client.py tests/test_mcp_agent_isolation.py \
  tests/test_mcp_config_validation.py -q
```

## 13. 修改前端/网关

当前无自动化测试；CI 也不会覆盖 gateway。改动前先确认该目录是否仍在支持范围（见 08）。

| 检查项 | 说明 |
|---|---|
| API 契约 | `/api/models`、会话分页、权限响应 |
| SSE | 事件名与 `sdk_events` 的对应 |
| 鉴权 | `require_token`、`FLOODMIND_GATEWAY_TOKEN`、持久化 token 的加密读写 |
| 工作区 | `FLOODMIND_GATEWAY_WORKSPACE` |
| 宿主边界 | 旧 `web/`、`tui-ts/` 已删除；网关是唯一内置宿主适配层 |

手工验证：`scripts/e2e_gateway.py`（需先启动服务并提供 token）。

## 14. 发布前检查清单

1. `python -m pytest -q` 全绿；当前基线为 1565 passed, 1 skipped。
2. `python scripts/calibrate_judge.py --judge rule --min-agreement 1.0` 一致率 100%。
3. 内部评测集：`python -m evals.runner --json eval-report.json`，要求 243/243；定期/手动再跑 `--heldout`（253/253）。
4. SDK 导入纯净性：`tests/test_sdk_purity.py`。
5. 收益基线（默认离线，不调用模型）：`python -m benchmarks.multi_agent.runner --list`；需要真实对照时才加 `--live`。
6. `python -m scripts.knowledge_graph` 重新生成图，检查：
   - 循环数没有增加；
   - 未解析导入没有增加；
   - 分层依赖没有新增反向边（`evals`、`benchmarks` 是新增分层）；
   - 直接用浏览器打开 `graph/graph.html`，确认文件/分层/子系统三视图可渲染。
7. 干净检出验证：`git archive` 提取后跑一次全量，避免只验证工作区。
8. 文档数字与代码同步（测试数、版本、路径）；测试数字以 CI 输出为准。
