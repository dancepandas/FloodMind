# FloodMind 架构评估：已完成 vs 待处理

> **更新日期**: 2026-07-14
> **变更**: 标记 v2→v3 已完成批次（workspace 跨线程修复、Web 模块化拆分、worktree 隔离、Chronos 迁出）。架构地图见 [`OVERVIEW.md`](./OVERVIEW.md)。

## 评估原则

- **P0**：正确性 bug — 必修。
- **P1**：架构债（重复/两套系统并存）— 应收敛。
- **P2**：可优化 — 非必修但收益明确。
- **P3**：可删除（死代码）— 清理降低认知负担。

---

## ✅ 已完成（按时间线）

### MCP 统一 (2026-07-03, commits: 399e433, 9d96383, 1ca089b)

| 批次 | 内容 | 测试 |
|---|---|---|
| **MCP-A** | 统一 MCP 接入为单一热插拔路径：`build_mcp_tool_specs` 单构造点；`connect_server`/`connect_all` 连接与注册解耦；删除 `connect_and_register` 和 `_register_mcp_tools` 双注册 | 388 tests |
| **MCP-B** | 生命周期原语 `list_servers`/`get_server_info`/`disconnect_server`；`_InstanceToolRegistry.unregister_prefix` + `threading.Lock` 并发锁 | |
| **MCP-C** | 暴露 `ListMcpServers`/`DisconnectMcpServer` Agent 工具（自维护闭环：接入/列举/断开） | |

### Skill 统一 (2026-07-03 ~ 2026-07-04, commits: 4726561, 8cab81e, f90227d)

| 批次 | 内容 | 测试 |
|---|---|---|
| **Skill-A+B** | 单 `SkillRegistry` 发现源（CWD 无关 roots）+ 单 registry + 单 catalog + hot-plug 修复（`refresh_skills` stale-binding 修复 + auto-gen 回调闭环） | 397 tests |
| **Skill-C** | Agent 完整 CRUD 工具：`ListSkills`/`CreateSkill`/`UpdateSkill`/`RemoveSkill`/`RefreshSkills`；`_validate_skill_name` 路径穿越防护；`writable_root` 落盘 | |
| **Skill-D** | Curator 整合：roots 统一到 SkillRegistry；`record_skill_usage` 从 GetSkill 接活；`_handle_remove_skill` 委托 `curator.archive_skill`；`run_maintenance_if_needed()` 自动巡检 | |

### AgentTool↔ToolSpec 收敛 (2026-07-03, commits: 0d4aa65, 9ce9a9f)

| 批次 | 内容 |
|---|---|
| **C-core** | `ToolRegistry` 瘦身（删 8 零调用方方法）；`AgentTool` 删死字段/死方法 |
| **C-deep** | `AgentTool.to_tool_spec()` 唯一转换点；`native_from_agent_tool` 降为薄归一化；删 legacy `ToolResult` 类 + `AgentTool.run()`；系统工具统一 AgentTool 编写；MCP 保持直造 ToolSpec |

### 记忆子系统收敛 (2026-07-02, 批次 A+B)

| 批次 | 内容 |
|---|---|
| **批次 A (P0)** | `_turns_to_frontend` 适配扁平 schema；DualMemory 实现 `search_history`；排队消息终态统一；`_history_compressed` 持久化；streaming flag pump-finally 双清 |
| **批次 B (P1)** | 删除遗留压缩子系统 (b)：`_short_term`/`_consolidate`/`_long_term`/`compressed_summary`；`LongTermMemory` 保留但新增 `search_long_term` |

### 死代码清理 (2026-07-03, 批次 E)

| 批次 | 内容 |
|---|---|
| **E-1** | 删 `native/agent/` 孤立子包 (6 文件) |
| **E-2** | 删死 `EXECUTION_SPECIALIST_PROMPT` 属性 |
| **E-3** | 删 `AgentLoopState` 9 死字段 + `PlanStep` dataclass |
| **E-4** | 前端死组件清理（NotFound/CheckboxGroup/resumeSession） |
| **E-5** | 删 `filter_system_info` 死别名 |
| **E-6** | 删 `/api/download/<path>` legacy 路由 |
| **E-7** | 删 `NativeFloodAgent.resume()` + `chat_stream()` 死方法 |

### Desktop SDK 适配 (2026-07-14, commit: 9618d8f)

| 批次 | 内容 | 测试 |
|---|---|---|
| **workspace 跨线程修复** | NativeFloodAgent 新增 `workspace` 实例属性 + `bind_workspace()` + `_effective_workspace()`（与 PathService 同模式）；`_run_loop` 子线程重绑 contextvar；`Agent` (api.py) 透传；修复桌面端 sidecar 子线程丢失 `floodmind_workspace` 导致写入落到 C 盘 AppData | 404 tests (含 7 新) |

### v2→v3 相关变更 (2026-07-14)

| 变更 | 说明 |
|---|---|
| Chronos 外置为 MCP | `floodmind/skills/chronos/` → `contrib/chronos/`；3 个 chronos 工具项从 models.py 移除；`_warmup_chronos` 退化为 pass |
| Web 后端模块化 | 单块 `web_server.py` 拆分为 `floodmind/server/{routes/,agent_factory,session_state,sanitize,file_utils}` |
| worktree 会话隔离 | SessionManager 新增 `create_worktree`/`list_worktrees`/`remove_worktree`/`fork_to_worktree` + 元数据文件 |
| file_utils 预览重构 | CSV 预览改用 stdlib `csv`，Excel 改用 `openpyxl`，移除 pandas 依赖 |

---

## ⏳ 待处理（优先级排序）

### P0 — 正确性 Bug

| # | 位置 | 问题 | 状态 |
|---|---|---|---|
| P0-7 | `scheduler.py:62` + `web_server.py` | web 与 scheduler 同会话竞态写 `chat_history.json` | ⏳ |
| P0-8 | `floodmind/server/agent_factory.py:52-58` | `create_agent_for_session` 硬编码 `max_short_term=20` / `context_window=32768`，忽略 `settings.agent` | ⏳ |
| P0-10 | `useChatStream.ts:562-572` | `stopRequestedRef` handlePauseResume 不复位；排队无 in-flight 护栏 | ⏳ |

### P1 — 架构债

| # | 位置 | 问题 | 状态 |
|---|---|---|---|
| P1-3 | `agent_tool.py` + `contracts/tools.py` | 工具双抽象 (AgentTool + ToolSpec) **基本收敛**，双 ToolResult **已删 legacy**；`ToolRegistry` + `_InstanceToolRegistry` 双注册表维持（职责不同：全局编写 vs 实例运行） | 🟡 部分完成 |
| P1-4 | `base_tools.py:491` + `permission_service.py` | 两处危险命令库（Bash 层 + 权限层）— 不同层纵深防御，保留 | 🟡 设计如此 |
| P1-5 | `settings.json` vs `config.json` | 双配置源未统一 | ⏳ |
| P1-6 | `session_store.py` | SQLite 大半暗表未清 | ⏳ |
| P1-7 | `checkpoint_service.py` | CheckpointService 半退休（每轮仍写，仅用于 rollback） | ⏳ |
| P1-8 | `event_bus.py` | EventBus vs StepEventBus 近乎全复制 | ⏳ |
| P1-9 | `ChatComposer.tsx` + `WelcomePage.tsx` | 前端重复 `PINNED_MODELS/MODEL_ICON_MAP/sortModels` | ⏳ |

### P2 — 可优化

| # | 问题 | 状态 |
|---|---|---|
| P2-1 | LLM 压缩持锁调 `_llm.invoke`（冻结 memory 子系统） | ⏳ |
| P2-2 | `add_assistant_round` 每轮 `save_chat_history` O(n²) | ⏳ |
| P2-3 | `round_reasoning` 入口不复位 | ⏳ |
| P2-4 | `turn_index=max(_turn_index-1,0)` 无前置 user 误标 | ⏳ |
| P2-5 | 空 parts 缓存命中静默丢老历史 | ⏳ |
| ~~P2-6~~ | ~~`_get_skill_cached` lru_cache 不随 refresh 失效~~ | ✅ 已修 (Skill-D) |
| P2-7 | 脱敏靠纪律无装饰器 | ⏳ |
| P2-8 | `_session_token_usage` 无锁 | ⏳ |
| P2-9 | NativeFloodAgent 无实例级并发 stream 守卫 | ⏳ |
| ~~P2-10~~ | ~~for_model() Write/Edit 抑制~~ | ❌ close — 不做模型级过滤 |
| P2-11 | `MemoryAdd` policy_type=state_write 但 check_permissions_fn=readonly | ⏳ |
| P2-12 | 两套 prompt 构建路径需 `_rebuild_system_prompts` 镜像 | ⏳ |

### P3 — 可删除（死代码）

| 位置 | 内容 | 状态 |
|---|---|---|
| `dual_memory.py` | `ContextCompressor` 死类（与 executor 同名混淆） | ⏳ |
| `cli.py:248` | `pause` 命令（`agent.pause()` 空壳） | ⏳ |
| 旧 TUI 栈 | `tui/app.py`/`screens/{home,main,chat}.py`/`web_client.py`/`router.py` | ⏳ |
| `model_client.py:321` | `finished_reason` no-op 分支 | ⏳ |
| 前端 | `web/config.json`（指向不存在路径）；TanStack Query 挂载未用；checkpoint/trace API 无 UI 消费 | ⏳ |
| `web_server.py` | `/api/session/resume` (前端 caller 已删，TUI 仍 POST)；重复 `clear_memory` 路由 | ⏳ |

---

## 新增：本次重构后审计发现 (2026-07-04)

经对抗审查全量代码，以下为本次重构后仍存在的关注项：

### 🟡 设计取舍（有意为之，非 bug）

| 项 | 说明 |
|---|---|
| `archive_skill()` 不自动 refresh | 调用方 `_handle_remove_skill` 负责闭环；直接调用 curator 是底层操作 |
| `_rebuild_system_prompts()` 不是 refresh callback | `agent.refresh_skills()` 串联调用；直接调 `get_skill_registry().refresh()` 是合法底层操作 |
| `find_duplicates()` 独立扫描 | 支持测试隔离（自定义 dir 不走全局 registry） |

### 🔵 预存问题（非本次引入）

| 位置 | 问题 |
|---|---|
| `native_flood_agent.py:764,821-824` | `update_plan` 工具 `is_readonly=False` 但 `policy_type="readonly"` — 语义矛盾 |
| `registry.py:21` | `_PROJECT_ROOT` 与 `_runtime_root.PROJECT_ROOT` 重复定义 |
| `base.py:59` | `Skill.load_tools_module()` 死代码，无调用方 |
| `skill_generator.py:162` | `write_skill_to_disk` 无显式路径穿越验证 |

---

## 与原需求的对齐确认

- ✅ **会话历史统一架构**：`memory._turns` 单一源，整轮原子，abort 丢弃。批次 A 补回归，批次 B 消遗留。
- ✅ **模块化/不打补丁**：MCP/Skill 统一各为独立批次，内聚主题，成体系推进。
- ✅ **MCP 随时接入/随时发现**：Agent 工具 `LoadMcpServer`/`DisconnectMcpServer`；连接与注册解耦。
- ✅ **Skill 自维护**：Agent 5 个 CRUD 工具 + curator 自动巡检 + auto-gen 闭环。
- ✅ **效率/稳定/控 token**：P0 修复完成；遗留压缩删除减少 LLM 调用。
- ⏳ **脱敏装饰器强制**：P2-7 待做。
- ⏳ **存储/配置单一源**：P1-5/6/7 待做（批次 D，较大）。
