# 04. 运行时内核：契约、Reducer、Journal、事务与恢复

> 代码位置：`floodmind/agent/runtime/`。这是 FloodMind 的事实源与可恢复执行层，改动风险最高。

## 1. 分层与依赖方向

```text
contracts/            纯数据与协议（无 I/O）
reducer.py            只依赖 contracts，纯函数折叠
services/journal_*    依赖 contracts + reducer
services/*            编排：权限、工具、检查点、沙箱、后台、子线程、产物
adapters/             Flask/SSE 边界（当前基本无生产调用方）
native/、api.py       上层消费者
```

实际存在的反向依赖：

- `services/child_thread_runtime.py` 在模块顶层导入 `floodmind.agent.native.*`（executor、event_bus、message_builder、types、artifact_watcher），而 `native/native_flood_agent.py` 又导入 `child_thread_runtime`。当前没有形成确定的三模块环，但边界已被打破。
- `contracts/tools.py` 在 `check_permissions` 内延迟导入 `floodmind.tools.session_context`。
- `services/path_service.py`、`background_task_service.py` 延迟导入 `floodmind.tools.session_context`。

这些是后续拆包/分层的首要障碍。

## 2. Canonical 契约

| 契约 | 文件 | 关键内容 | 生产使用 |
|---|---|---|---|
| 事件信封 | `contracts/canonical_events.py` | `EventEnvelope`、canonical JSON、payload/event SHA256、`EVENT_TYPES` | 全链路 |
| 流式 part | `contracts/canonical_parts.py` | `CanonicalPart`：text/reasoning/tool_call/usage/response_end | 模型层 → 执行器 |
| 消息 | `contracts/messages.py` | `Message`、`Document`、`MessageStore`、`LLMProtocol` | 模型、记忆、上下文 |
| 运行状态 | `contracts/run_state.py` | `RunStatus`、`RunState`、`PendingApproval`、`ChildThreadState` | reducer、恢复、检查点 |
| 投影 | `contracts/projection.py` | `ProjectionSource`、`InputBudget`、`ProjectionManifest` | 压缩、执行器 |
| 工具事务 | `contracts/tool_transaction.py` | `SideEffectClass`、`ToolStatus`、合法转移、参数哈希 | reducer、执行器、工具服务 |
| 权限 | `contracts/permissions.py` | `PermissionRequest`、`PermissionRule`、`ToolPermissionPolicy` | 权限服务、工具服务 |
| 检查点 | `contracts/checkpoints.py` | `CheckpointManifest`、`journal_cursor`、`reducer_version` | 检查点、恢复 |
| 沙箱 | `contracts/sandbox.py` | `SandboxPolicy`、`ToolInvocation`、`SandboxBackend` | 子线程、后台任务 |
| 子线程 | `contracts/child_thread.py` | `ChildThread`、`SubagentResult` | 子代理运行时 |
| 路径/工作区/上下文/身份 | `contracts/paths.py`、`workspace.py`、`runtime_context.py`、`identity.py` | 路径请求、Workspace、四元组身份 | 全链路 |

### 2.1 契约双源（重要）

此前仓库里存在三组重复契约，现已删除仅测试使用的旧副本：

| 生产使用 | 已删除的旧副本 | 差异 |
|---|---|---|
| `tool_transaction.py` | `transactions.py` | 旧版 `canonical_arguments` 为 Dict、无 `result_committed` |
| `projection.py` | `context_manifest.py` | 旧版多一个 `projection_sha256` |
| `canonical_parts.py` | `parts.py` | 旧版是 SSE part 事件模型 |

旧副本及其测试已随遗留内容清理移除；当前契约层只保留生产使用的一套。

### 2.2 版本策略

- 事件 `schema_version = "1.0"`，没有迁移注册表。
- 兼容依赖“未知事件只推进 cursor、不改状态”：新增事件不会破坏旧 replay，但也不会产生旧状态。
- 检查点用 `reducer_version`、可选 `tool_registry_version` 严格校验，不匹配直接 fail-closed。

## 3. Reducer

入口：`reducer.reduce(state, event)`（`runtime/reducer.py:511`）。

规则：

1. 深拷贝输入，纯函数，无 I/O、无随机、无时间。
2. `event_id` 已在 `processed_event_ids` 中则直接返回，不重复应用。
3. 正常事件写入 `processed_event_ids`，`last_committed_sequence = event.sequence`。
4. 多数事件先判断 `_is_current_thread`，非当前线程的事件不改主状态。
5. 未知事件不改变状态，但推进 cursor（`reducer.py:578`）。
6. 工具状态沿线性链前进，非法转移静默忽略。
7. 工具完成后移除 pending 事务、追加 tool turn、合并 artifacts，无 pending 时置 `awaiting_model`。
8. 审批请求追加 `PendingApproval` 并置 `awaiting_approval`；审批结束且无 pending 时置 `awaiting_model`。

`RunState` 字段：身份、`status`、`last_committed_sequence`、pending 事务/审批、后台任务、子线程、artifacts、token 用量、`processed_event_ids`、取消状态、可恢复性、`turns`。

`processed_event_ids` 无修剪逻辑，长 run 的 checkpoint 体积随事件数线性增长。

### 3.1 投影链

| 投影 | 入口 | 消费方 |
|---|---|---|
| 当前 run 状态 | `JournalAuthority.replay` | 执行器、检查点 |
| 当前会话历史 | `history_projection.project_current` | DualMemory、memory tools |
| 跨 run 会话历史 | `history_projection.project_conversation` | session_manager、gateway |

跨 run 投影扫描 conversation 下所有 run 的 journal，按 `(recorded_at, sequence, event_id)` 排序后重放，并排除子线程 turn。

## 4. Journal

### 4.1 三个组件

| 组件 | 角色 |
|---|---|
| `JournalAuthority` | 门面：`emit` / `read_after` / `replay` / `checkpoint_snapshot` |
| `JournalWriter` | 唯一权威：JSONL 追加、CAS、hash chain、fsync、segment roll、torn-tail 修复 |
| `SqliteJournalIndex` | 派生加速器，可重建，不是第二事实源 |

`emit` 顺序：写 writer → 尝试写 index → 通知 processors。index 异常被静默吞掉，下一次 `read_after` 通过计数校验发现不一致并回退 JSONL 重建。

### 4.2 写入保证

1. 跨进程 `FileLock`。
2. 每次 append 前重新扫描权威尾部（`_reconcile_from_journal`）。
3. 修复撕裂尾（只截断到最后一条完整事件；mid-file 损坏拒绝）。
4. `expected_last_sequence` CAS。
5. `event_id` 去重。
6. 分配 sequence，计算 `payload_sha256`、`previous_event_sha256`、`event_sha256`。
7. 追加一行 canonical JSON，`flush + fsync`。
8. 失败路径先修复尾部，再抛原异常。

### 4.3 磁盘布局

```text
<runtime_root>/.floodmind/
  conversations/<conversation_id>/
    tasks/<task_id>/
      runs/<run_id>/
        lease.json
        journal/
          events-000000.jsonl
          .lock
          index.json
          journal.sqlite3
        threads/<thread_id>/
          state/  tmp/  scripts/
```

`runtime_root` 默认是项目根下的 `.floodmind`；CLI/宿主可显式指定。

## 5. 工具事务

### 5.1 正常顺序

1. 执行器发 `tool.call.proposed`，携带 `transaction_id`、canonical arguments、`arguments_sha256`、`side_effect_class`、`idempotency_key`。
2. `ToolExecutionService` 解析工具、清洗参数、JSON Schema 校验。
3. 权限检查：宿主 `permission_handler` 预授权 → SDK `PermissionService.check` → 工具自身 `check_permissions`。
4. 计算审批指纹：工具名、参数、目标、cwd、workspace、agent tier、运行模式、副作用类、策略版本等 13 项 SHA256。
5. DENY：发 `tool.permission.evaluated(deny)`，返回错误结果。
6. ASK：见 5.2。
7. ALLOW：发 `tool.call.validated`、`tool.permission.evaluated(allow)`。
8. 幂等短路：非 read 且有 idempotency key 时，查已提交结果并复用。
9. 发 `tool.execution.started`。
10. 执行（`_ToolRunnerPool`：每调用一个 daemon 线程 + 信号量，并发上限 8、排队 10s、工具超时 300s）。
11. 根据结果发 `tool.execution.completed` / `failed` / `indeterminate`。
12. reducer 折叠。

### 5.2 ASK 的两条路径

| 路径 | 触发 | 事件序列 | 行为 |
|---|---|---|---|
| 阻塞（PermissionService） | 生产默认装配 | `tool.approval.requested` → `action_start` → `permission_ask` → 用户响应 → `tool.approval.resolved` | 调用线程阻塞，最长 AskService 超时 |
| 非阻塞（ToolExecutionService） | 未配置 PermissionService，或 hook 把 ALLOW 收紧为 ASK | `tool.approval.required` → `start_ask` → `tool.approval.requested` | 返回 `awaiting_permission`，由外部响应后续跑 |

因为生产装配优先 `PermissionService.check`，非阻塞分支在生产中较少走到。依赖 `tool.approval.required` 推进状态的代码需要同时兼容 `tool.approval.requested`。

### 5.3 失败矩阵

| 失败点 | 行为 |
|---|---|
| 无 JournalAuthority | 拒绝执行 |
| 工具不存在/参数非法 | 返回 error，不进入权限 |
| DENY | 记录 deny，返回 error |
| ASK 服务不可用 | 非阻塞路径报错；PermissionService 路径自动 DENY |
| ASK 超时/拒绝 | DENY 或 error |
| 并发池满 | `TOOL_EXECUTION_SATURATED`，可重试 |
| 工具超时 | `TOOL_EXECUTION_TIMEOUT_INDETERMINATE`，标记 indeterminate 且 `do_not_retry_same_call` |
| 工具异常 | 返回 error |
| Journal 写失败 | 修复尾部后抛出 |

## 6. 检查点与恢复

### 6.1 保存

- 前置校验：`session_id` 非空、`run_state.last_committed_sequence == journal_cursor`、`run_id` 一致。
- 写 `state.json`、`run_state.json`、`manifest.json` 到临时目录，原子 rename。
- manifest 记录 cursor、`reducer_version`、`tool_registry_version`、五段 identity。
- 不复制工作区文件；文件回滚未实现（`rollback_files` 明确 legacy）。

### 6.2 恢复

1. `open_lease`：FileLock + CAS + TTL 300s + token；未过期抛 `ResumeBusyError`。
2. 打开 JournalAuthority，并把 `lease.fence_check` 传给 writer（`resume_service.py:193`）。
3. 指定 checkpoint 时校验版本、五段 identity、cursor，重放前缀并与快照逐字段比较，失败 `CheckpointConsistencyError`。
4. 发 `resume.started`。
5. replay 得到权威 `RunState`。
6. `ReconciliationService.reconcile` 落定僵尸事务/审批/子线程/后台任务。
7. 再次 replay，并把 pre-execution 阶段的僵尸事务落定为 `indeterminate` + `tool.result.committed(failed)`。
8. 可选写入用户消息。
9. 发 `resume.completed`，返回 `ResumeOutcome`（含 authority、lease）。
10. 调用方把 authority 绑回 executor 并释放 lease。

### 6.3 lease 的边界

lease 同时承担**入场锁**与**写 fencing**：`JournalWriter` 接受 `fence_check`（`journal_writer.py:114-150`），每次 append 在 journal 文件锁内校验 lease token（`journal_writer.py:274`、`:342`），不匹配抛 `JournalFencedError`。运行路径通过 `open_journal_authority(..., fence_check=lease.fence_check)` 接入（`native_flood_agent.py:2980`、`resume_service.py:193`），因此旧 owner 的迟到写入会被拒绝。

残余边界：`fence_check` 是可选参数；任何绕过 lease 直接打开 authority 的调用方仍可写入同一 run。新增运行入口必须显式传入 lease 的 `fence_check`。

## 7. 其他运行时能力

### 7.1 后台任务

状态：`starting / running / kill_requested / terminating / completed / failed / killed / kill_failed / orphaned / unknown`。

- `start`：校验并发上限、创建 stdout/stderr、Popen、可选沙箱、写 meta、发 `background.*`。
- `kill`：终止进程树并验证退出。
- `reconcile_background`：按 PID + create_time 判断存活。
- watcher 线程负责超时与终态。

### 7.2 子线程/子代理

流程：发 `child_thread.accepted` → 打开子 journal → 创建沙箱 workspace → 子权限/路径上下文 → 独立工具运行时 → 执行 → 收集产物并复制到父 → 分类终态（result/failed/cancelled）。

配额：turn、token、wall-clock。清理失败时 `cancelled`/quota 终态会降级为 failed。

### 7.3 产物

- 内容寻址：`objects/<sha[:2]>/<sha>`。
- 发布前校验源文件存在、allowed roots containment、symlink resolve 后 containment、大小与哈希。
- 事件：`artifact.declared` → manifest → `artifact.committed`。
- `allowed_roots` 为空时不做源文件 containment 检查，生产装配必须显式传入。

### 7.4 追踪

每 session 内存缓冲 span/event，flush 到 `trace.jsonl`，并向 `metrics.jsonl` 追加一条指标快照。指标包含 totals 与 `breakdown`（by_run / by_model / by_step / by_tool）；成本来自 `settings.model_pricing`。

配置 `observability.otlp_endpoint` 时，`flush` 还会把已完成 span 转成 OTLP/HTTP JSON 导出到 collector；导出失败只告警，不影响本地落盘与缓冲清理。未传 base 时使用 `Path.cwd()/data/sessions`，与 Journal 的显式 runtime root 策略不一致。

## 8. 风险清单

| # | 风险 | 影响 |
|---|---|---|
| 1 | 三组契约双源 | 误 import、类型不兼容、测试假绿 |
| 2 | `background.*` 不在 `EVENT_TYPES` | 完整性校验/文档生成漏事件 |
| 3 | `run.pause/cancel` 事件存在但 reducer 不处理 | `paused/cancelled` 状态派生不出来 |
| 4 | `_advance_to` 允许 targets 与合法转移表冲突 | 未来调用静默 no-op |
| 5 | 正常路径不发 `tool.result.committed` | `result_committed` 状态闲置；幂等查询依赖 `tool.execution.completed` |
| 6 | ASK 双语义 | 事件序列不一致，消费方需兼容两套 |
| 7 | `tool.approval.resolved` 过早置 `awaiting_model` | 快照状态与真实执行态不一致 |
| 8 | `fence_check` 可选：未显式传入的 authority 打开路径不受 lease fencing 保护 | 新增入口若漏传，旧 owner 仍可迟到写入 |
| 9 | reconciliation 只在 resume 调用 | 崩溃后不 resume 则僵尸不落定 |
| 10 | artifact allowed_roots 为空即放行 | 源文件范围失控 |
| 11 | tracing 默认目录依赖 cwd | trace 分散 |
| 12 | `processed_event_ids` 无界 | 长 run checkpoint 膨胀 |
| 13 | `contracts/__init__` 导出不全 | 外部被迫依赖内部路径 |
| 14 | adapters 基本无生产调用方 | 修改/删除前需确认外部路径导入 |
| 15 | index 写入异常被吞 | 长期失败无告警 |
| 16 | runtime services 顶层依赖 native | 分层反转，易成环 |

## 9. 修改影响提示

| 变更区域 | 必查 |
|---|---|
| `EventEnvelope` / 事件类型 / payload | journal writer、reducer、checkpoint、history projection、SSE 映射；兼容旧 journal |
| `ToolStatus` / 合法转移 | reducer、executor、工具服务、reconciliation、resume |
| `RunState` 字段 | reducer 全部 handler、checkpoint 快照、resume 校验、UI |
| journal 目录/segment/index | authority、checkpoint、resume、history projection、后台/子线程 |
| 权限/ASK | permission_service、tool_execution_service、ask_service、adapters、gateway |
| 审批指纹字段 | 已批准操作重入、审计；字段变化使历史指纹全部失效 |
| 幂等键派生 | executor 与 service 必须使用同一 canonical 参数形态 |
| checkpoint manifest | cli/api resume、executor 周期保存、旧数据兼容 |
| lease/resume | 并发恢复、崩溃恢复、TTL 语义 |
| 沙箱/进程树/后台 | 子线程、后台服务、Windows/POSIX 差异 |
| 子线程 | 父 Agent、子权限/路径、产物复制、配额 |
| 产物 | executor、resume、子线程、artifact API |
| 行为回归 | `tests/` + `python -m evals.runner`（`evals/cases/regression-*` 覆盖中断、预算、非法工具调用等运行时终态） |
