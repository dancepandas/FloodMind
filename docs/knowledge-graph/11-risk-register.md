# 11. 风险登记与债务地图

> 汇总静态图、各子系统代码审阅与测试清点的结果。优先级按“影响面 × 触发概率 × 修复成本”排序，不代表已排期。
> 本版已按 2026-10-19 工作区复核：已修复项与本轮遗留内容清理移入第 5 节，不再作为未实现风险出现。

## 1. 优先级定义

| 级别 | 含义 |
|---|---|
| P0 | 会导致数据丢失、错误执行、恢复失败或安全边界失效，应在改动相关模块前处理 |
| P1 | 会造成行为不一致、难以诊断的缺陷或显著维护成本 |
| P2 | 结构/文档/测试债务，影响长期演进 |

## 2. P0：正确性与安全

| # | 风险 | 证据 | 影响 | 建议 |
|---|---|---|---|---|
| P0-1 | 删除会话不清 Journal | `delete_session` 只删会话目录与 worktree（`floodmind/memory/session_manager.py:600-628`；网关入口 `gateway/app.py:95-100`） | 已删除会话的 `events-*.jsonl` 仍可被日志工具检索/恢复 | 明确保留策略，或按 conversation 级联清理 |
| P0-2 | 记忆文件无跨进程锁 | 语义库/核心记忆/LTM/经验树只有进程内 `RLock`（`semantic_store.py`、`core_memory.py`、`dual_memory.py`、`experience_tree.py`），全仓库无 file lock 接入该层 | 桌面多实例同时写会互相覆盖 | 补文件锁或迁移到 CAS |
| P0-3 | `artifact.allowed_roots` 为空即放行 | `_validate_containment` 空列表直接 return（`runtime/services/artifact_service.py:228-234`）；无 workspace 时调用方传空列表（`agent/api.py:317-321`、`native/native_flood_agent.py:2991-2995`） | 生产漏传 workspace 时源文件范围失控 | 生产装配强制非空，或断言 |

## 3. P1：行为与一致性

### 3.1 运行时

| # | 风险 | 证据 | 影响 |
|---|---|---|---|
| P1-2 | `tool.approval.resolved` 过早置 `awaiting_model` | `runtime/reducer.py:429-439` 在审批列表清空后立刻切 `awaiting_model` | 快照与真实工具执行态不一致 |
| P1-3 | 正常路径不发 `tool.result.committed` | 仅 reconciliation 与 resume 发（`runtime/services/reconciliation_service.py:74`、`resume_service.py:231`）；正常执行路径不发（`native/executor.py`） | 状态机语义与实现不符，幂等查询依赖另一事件 |
| P1-4 | `background.*` 不在 `EVENT_TYPES` | reducer 已处理 `background.started/killed/completed`（`runtime/reducer.py:574-576`），但事件类型表没有该组（`runtime/contracts/canonical_events.py:66-94`） | 完整性校验/文档生成漏事件 |
| P1-5 | 后台回顾绕过经验 store 版本 | `apply_review_result` 直接 `experience_tree.add_leaf`（`agent/native/background_review.py:214`）；版本只在 `record_experience` 等 store 方法递增（`memory/task_experience.py:165`），上下文缓存按版本命中（`native_flood_agent.py:2751-2758`） | 新经验在下次版本变化前不可见 |

### 3.2 执行核心

| # | 风险 | 证据 | 影响 |
|---|---|---|---|
| P1-6 | 项目规则读取 `Path.cwd()` | `agent/context_runtime.py:152` | 从错误目录启动时项目级 AGENTS.md 缺失 |
| P1-7 | DOOM 判定用 `"错误" in result[:50]` | `native/executor.py:1169` | 误判提前终止或空转 |
| P1-8 | 事件队列无界 | `native/native_flood_agent.py:2928` 创建无 maxsize 的 `queue.Queue()`；虽有 `consumer_gone` 早停（`:3342` 附近） | 慢消费者时内存增长 |
| P1-9 | 历史压缩缓存键不含内容 | `memory/dual_memory.py:400-402` 只含 conversation_id 与 turn_index/role | 同一轮内容更新后复用旧摘要 |

### 3.3 记忆

| # | 风险 | 证据 | 影响 |
|---|---|---|---|
| P1-10 | 旧 LTM 与语义库并存 | `MemoryAdd` 写门面语义库（`tools/base_tools.py:1408-1427`）；旧 LTM 仅由 `background_review` 旧直写回退追加（`background_review.py:236-251`） | 同一事实可能只在其中一个库，召回口径不一致 |
| P1-11 | `fork_session` / `fork_to_worktree` 无调用方且无测试 | `memory/session_manager.py:653-670,873-895`；全仓库无外部调用 | fork 路径（含 worktree 创建与父关系标记）未被实际使用，回归时无保护 |

### 3.4 模型与配置

| # | 风险 | 证据 | 影响 |
|---|---|---|---|
| P1-12 | 旧配置后合并覆盖新配置 | `config/settings.py:364` 仍合并 `~/.config/floodmind/config.json` | 设置不生效，排障困难 |
| P1-13 | 两套 `TokenUsage` | `native/types.py:155` 与 `native/providers/usage.py:16` | 转换遗漏时 usage 字段丢失 |

### 3.5 SDK

| # | 风险 | 证据 | 影响 |
|---|---|---|---|
| P1-14 | `AgentInfo` 的 allow/deny 未被执行 | `agent_registry.py:80` 定义 `can_use()`，全仓库无调用；`allow/deny` 只用于提示词描述 | `plan`/`explore` 类型不限制实际工具集 |
| P1-15 | 旧检查点目录含工作区文件快照且递归嵌套 | `.floodmind/sessions/**/checkpoints/**/files/` 中可递归出现 `.floodmind` | 磁盘膨胀，与“检查点只存状态”表述冲突 |
| P1-16 | `.claude/worktrees/` 含前端副本 | 目录存在 | 搜索与扫描重复命中，误判活跃代码 |

### 3.6 工具、技能与 MCP

| # | 风险 | 证据 | 影响 |
|---|---|---|---|
| P1-17 | `ApplyPatch` 只注册到全局 `ToolRegistry` | `tools/file_tools.py:837`；native 的 `all_tools` 不含它（`native_flood_agent.py:885-899`） | native 运行时不可见，UI 标签却存在 |
| P1-18 | MCP `is_error` 只记健康，不转失败 | `mcp_client.py:415-417,586-593`；`build_mcp_tool_specs` 的闭包直接返回 str 子类（`:442-446`） | 工具失败仍标 completed，不触发 retry/journal failed |
| P1-19 | Skill `tools.py` 加载器未接线 | `skills/base.py:60-78` 定义 `load_tools_module()`，全仓库无调用方 | 声明能力不会注册工具 |
| P1-20 | `CreateSkill`/`UpdateSkill` 写盘非原子 | `native_flood_agent.py:1773` 先 `write_text` 再回读；失败会回滚（`:1763-1794`），但进程崩溃仍可能留下截断文件 | 极端情况下 SKILL.md 损坏 |
| P1-21 | contrib Skill 不在默认根 | 默认发现根为 `skills/` 与 `.claude/skills`（`skills/registry.py:89-92`）；`contrib/skills` 需显式注入 | `chronos` 等无法按默认路径使用 |
| P1-22 | 全局 `ToolRegistry` 与实例 registry 双事实源 | `tools/base_tools.py:1860+` 注册全局镜像；native 使用自己的 registry | 持续产生“注册但不可见”的漂移 |
| P1-23 | 两份 `mcp.json` 且键名不一致 | 运行时只读 `get_floodmind_home()/mcp.json`（`config/settings.py:663-664`）；项目内 `.floodmind/mcp.json` 使用 `type`，而校验强制 `transport`（`settings.py:680-684`） | 切换 `FLOODMIND_HOME` 后启动直接报 `McpConfigError`；当前是 fail-fast，不再是静默失败 |

## 4. P2：结构与工程

| # | 风险 | 证据 | 影响 |
|---|---|---|---|
| P2-1 | 4 个文件级循环 | `graph/cycles.md`：skills、config（6 文件，含 `secret_store`）、tools、cli | 初始化顺序脆弱 |
| P2-2 | `runtime/services/child_thread_runtime.py` 顶层依赖 `native/*` | `child_thread_runtime.py:14-18` | 分层反转，易成环，拆包困难 |
| P2-3 | `contracts/tools.py` 延迟依赖 `tools.session_context` | 契约层不纯 | 边界模糊 |
| P2-4 | `native_flood_agent.py` 高连接 | import 入度 21 + 出度 52 = 73（`graph/metrics.md:148`） | 改动影响面大 |
| P2-5 | 213 个文件无入边 | `graph/metrics.md` | 测试/入口/脚本混杂，需要分类清理 |
| P2-7 | `contracts/__init__` 导出不全 | `contracts/__init__.py` 不导出重复契约模块；外部依赖内部路径 | 公共面不稳定 |
| P2-8 | adapters 基本无生产调用方 | 只有导入烟测 | 删除/保留需决策 |
| P2-9 | 网关无自动化测试 | `tests/` 无 gateway 用例；CI 只跑 pytest + evals | SSE、权限 ASK、历史分页无回归 |
| P2-10 | `docs/architecture/` 被 gitignore | `.gitignore:10` | 架构文档不随仓库分发 |
| P2-13 | tracing 默认目录依赖 cwd | 观测数据分散 | 排障困难 |
| P2-14 | `TOOL_DEFAULTS` 无消费者 | `tools/agent_tool.py:306` 定义，仅 `tools/__init__.py` 再导出 | 误以为改它能统一限制输出/超时 |
| P2-15 | 1 个未解析导入 | `graph/unresolved.md`：`skills/skill-creator/scripts/package_skill.py` → `scripts.quick_validate` | 静态分析盲区，需确认是否为真实断链 |

## 5. 已化解（本轮 harness 调整）

| 原风险 | 现状 | 证据 |
|---|---|---|
| `test_profile_system.py` 是假测试 | 已改为真实断言 | `check()` 失败抛 `AssertionError`；`python -m pytest tests/test_profile_system.py` 9 passed |
| `MemoryAdd` 未注册到 native | 已注册 | `native_flood_agent.py:888`（`all_tools` 含 `add_memory`）；工具名 `MemoryAdd` 定义于 `tools/base_tools.py:1434` |
| resume lease 不是写 fencing | 已实现 | `journal_writer.py:143-150,274,342`；`tests/test_journal_fencing.py` |
| 重试不感知中断 | 已实现 | `executor.py:842-879` abort 检查与可中断退避；`model_client.py:269-275` |
| 权限 ASK 忙等 | 已改为事件等待 | `runtime/services/ask_service.py:56,187-205` |
| API Key 明文存储 | 已静态加密 | `config/secret_store.py`（DPAPI/Fernet，`enc:v1:`）；`settings.py:19` |
| 根项目无 CI | 已有 | `.github/workflows/ci.yml`（3.10/3.12，pytest + evals） |
| 模型回退/子代理路由缺失 | 已实现 | `native/model_gateway.py`；`settings.model_routing`；`tests/test_model_routing.py` |
| 无内部评测集 | 已实现 | `evals/` 243 个常规用例 + 10 个 held-out，CI 门禁 |
| 无多代理收益基线 | 已实现 | `benchmarks/multi_agent/`；ADR-007；`tests/test_multi_agent_benchmark.py` |
| `full_ref` 无生产者 | 已实现 | `runtime/services/tool_output_service.py:59-88` |
| MCP 单 server 失败中止后续 | 已隔离 | `mcp_client.py:545-554` 逐个捕获 |
| `_advance_to` 非法转移静默 no-op | 已 fail-closed | `runtime/reducer.py:220-236` |
| 经验维护任务无调用方 | 已接线 | `native_flood_agent.py:912` 调用 `run_maintenance_if_needed` |
| DashScope 路由不含 qwen | 已覆盖 | `providers/dashscope.py:24-26` |
| pause/cancel 无对应状态 | 语义已明确：暂停 = abort，终态 failed，半截轮丢弃 | `native/executor.py:458,886-891`；`test_checkpoint_cursor.py` |
| `Agent.resume` 绕过主循环 / `events_after` 丢事件 | 已改走 `run_from_state` 与 canonical 对账 | `agent/api.py:245-266` |
| bare 模式语义与文档不符 | 已明确并文档化：bare 跳过内置/Plugin，但接入宿主工具、后台任务工具、MCP 与 Skill 目录 | `native_flood_agent.py:586-601` |
| 重复契约模块（原 P1-1） | 已删除 | `contracts/transactions.py`、`parts.py`、`context_manifest.py` 及其测试移除；实际使用 `tool_transaction.py`、`canonical_parts.py`、`projection.py` |
| 零引用死代码（原 P2-6） | 已删除 | `agent/task_runtime.py`、`memory/global_search.py` |
| 打包引用不存在的 `floodmind/MCP`（原 P2-11） | 已移除 | `MANIFEST.in`、`pyproject.toml` 不再引用 |
| tests 下 pyc 残留与空目录（原 P2-12） | 已清理 | 主工作区无 `__pycache__` 目录（`.claude/worktrees/` 副本除外） |
| 旧前端与旧宿主 | 已删除 | `web/`、`floodmind/tui-ts/`、`sdk-ts/`、`floodmind/tui/`、`floodmind/server/` 均不存在；仅保留活跃 `gateway/` |

## 6. 循环依赖明细

| 循环 | 成员 | 处理建议 |
|---|---|---|
| 1 | `skills/base.py` ↔ `skills/registry.py` | 把解析/校验下沉到 base，registry 只做聚合 |
| 2 | `model_client.py`、`model_presets.py`、`model_resolver.py`、`provider_registry.py`、`secret_store.py`、`settings.py` | 把解析结果抽为独立 dataclass，切断 resolver↔registry↔presets；`secret_store` 与 `settings` 的互相引用改为单向 |
| 3 | `tools/base_tools.py` ↔ `tools/file_tools.py` | 把共享 helper 抽到独立模块 |
| 4 | `cli.py` ↔ `cli_interactive.py` | 抽 command 层，交互层单向依赖 |

这些循环当前没有导致运行时错误（多用延迟导入），但会放大初始化顺序问题与测试替换成本。

## 7. 文档与实现漂移

| 文档 | 声明 | 实际 |
|---|---|---|
| `docs/architecture/OVERVIEW.md` | v2.0.0 已移除 Web/TUI | 工作区同样已无 `web/`、`floodmind/tui-ts`；当前新增的是活跃 `gateway/` |
| `floodmind/memory/README.md` | 语义库 version 1、旧 LTM 冻结只读 | 实现 version 2；`MemoryAdd` 已只写语义库，但 consolidation 禁用时 `background_review` 旧直写回退仍会追加 LTM（`background_review.py:236-251`） |
| `.env.example` | 优先级描述 | 需与 `model_resolver` 逐条核对 |
| `docs/architecture/` | 被当作当前架构 | 整目录被 gitignore，且部分内容早于本次 harness 调整 |

## 8. 建议的处理顺序

1. **先止血**：P0-1 会话删除、P0-2 跨进程写、P0-3 产物根。
2. **再统一语义**：P1-2/P1-3 工具生命周期事件、P1-4 事件 taxonomy。
3. **再收敛记忆与配置**：P1-5 回顾入口、P1-10 LTM/语义库、P1-12 旧配置合并。
4. **最后做结构**：拆 `native_flood_agent.py`、消除循环、清理 legacy adapters。
5. 全程同步：测试门禁、文档数字、知识图谱重生成。

## 9. 风险与改动区域对照

| 要改的区域 | 先读 |
|---|---|
| Journal/事件 | 04 第 2-4 节、09 第 4 节 |
| 工具事务/权限 | 04 第 5 节、11 P1-2~P1-4 |
| 记忆/上下文 | 05、03 第 6 节 |
| 模型/供应商 | 07、02 第 5 节 |
| SDK 公共面 | 02、11 P1-14~P1-16 |
| 前端/网关 | 08 |
| 测试/评测 | 10、12 |
