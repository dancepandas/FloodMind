# FloodMind Forward-Only Architecture Migration — Design

> 状态：Approved（2026-08-10）
> 依据：[`docs/architecture/FM_ARCHITECTURE_BASELINE.md`](../../architecture/FM_ARCHITECTURE_BASELINE.md)
> 原则：**只向前——不向后兼容、不 fallback**。跳过文档中所有 COMPAT 标签、§22.3 兼容面、Shadow Journal 双写过渡与旧接口适配器。

## 1. 目标

按 `FM_ARCHITECTURE_BASELINE.md` 的 TARGET 架构，逐阶段把 FloodMind 收敛到三条主线：

1. **Canonical Event Journal + Deterministic Reducer** —— Journal 是唯一运行事实源。
2. **Model Transport / Codec / ResponsePipeline / Capabilities 正式分层**。
3. **Tool Transaction、Invocation Ledger、Approval Fingerprint 与 Durable Resume**。

每个阶段独立交付、回归全绿后进入下一阶段。

## 2. 已确认的设计决策

| 决策 | 结论 |
|---|---|
| Journal 存储后端 | **JSONL 分段**（文档 §18 布局：`events-000001.jsonl` + `index.json`），`JournalWriter` 抽象化，CAS 用锁 + expected-last-sequence 实现；后续可换 SQLite 作 backend |
| 宿主线协议 | **双轨重构**：SSE 事件名保持为 Presentation 协议（改为由 Journal 派生 + `after_sequence` 断线重放）；SDK 公共 API 按 TARGET 契约重定义，web/desktop 同批次适配 |
| Phase 2（文档） | **不做生产双写 Shadow Journal**。Reducer 正确性靠测试级重放验证 + Divergence 断言，纯 forward-only 直接切权威 |
| 兼容面（文档 §22.3） | 一律不保留：`_turns/chat_history.json` 读取、`AgentTool→ToolSpec` 兼容、Global Legacy API、ContextVar 全局 getter 的 fallback |

## 3. 现状基线（2026-08-10 核实）

- 测试基线：**846 passed, 1 failed, 1 skipped, 1 deselected**（~52s）。
  - `tests/test_profile_system.py::test_10_pytest` 用 subprocess **递归跑全量套件** → 无限嵌套挂死（预存缺陷，基线需 `--deselect`）。
  - `tests/test_agent_skill_roots.py::test_programmatic_update_and_cleanup_are_owner_scoped` 失败：`BackgroundTaskService._subscribers` 全局单例跨测试残留（预存隔离缺陷）。
- 已具备的迁移基础：`TerminalReason`、`InvalidToolCall`、`ToolCall/ToolResult/ToolSpec`、`ToolExecutionService`（schema 校验 + 权限 + sanitize）、`ProviderPipeline`（方言层）、每 Agent 隔离的 Permission/Path/MCP/Skill、`Workspace` frozen dataclass、Background/Specialist 隔离。
- 缺口：身份层级（现为 `session_id`）、Canonical Event Envelope、确定性 Reducer、ToolTransaction/指纹/幂等、Model 四层、Projection Manifest、Immutable RuntimeContext、Journal-backed Resume、ArtifactService、子代理标准 thread_id。

## 4. 阶段计划（P0–P8，forward-only 重定义）

### P0 — 冻结语义与基线
- 固化身份术语、状态机、`TerminalReason` 枚举与事件分类。
- 建立 Provider Golden Stream Fixtures（§25.2 覆盖：Tool JSON 跨 chunk、reasoning 同帧、并行 tool calls、usage-only final chunk、refusal/filter/max_tokens 不误判）。
- 修复两个预存测试问题：递归套件测试改为仅在 `__main__` 下运行；BackgroundTaskService 测试隔离。
- 验收：基线 846 全绿（含 `--deselect` 不再需要——递归测试被修复）。

### P1 — Canonical Contracts（新增，不改运行时行为）
交付（纯数据层 + 测试）：
- **身份层级**：`conversation_id / task_id / run_id / thread_id / turn_id / attempt_id / call_id / transaction_id / artifact_id / checkpoint_id`（§3.1）。
- **Event Envelope**：`schema_version / event_id / event_type / sequence / recorded_at / 身份链 / causation_id / correlation_id / actor / payload / provenance / integrity(哈希链) / privacy`（§4.1）。
- **`JournalWriter`**（JSONL 分段）：原子 append、`expected_last_sequence` CAS、幂等 `event_id`、滚动分段、尾部半写检测、Durability 策略（always/turn_boundary/best_effort）。
- **`ToolTransaction`**：`transaction_id / call_id / tool_id / canonical_arguments / arguments_sha256 / workspace/runtime/permission fingerprint / idempotency_key / side_effect_class / 终态机`（§6.1）。
- **Immutable `RuntimeContext`**（frozen dataclass，§11.1）。
- **Artifact Manifest / Context Manifest / Canonical Model Part events**（§15.1 / §9.2 / §7.5）。
- **`ChildThread` 契约 + Typed SubAgent Result 协议**（§13.1）。
- 验收：新契约测试全绿；现有 846 不回归。

### P2 — Reducer + Journal Authority（权威切换）
- 确定性 `reduce(state, event)`（§5.1，无 I/O、无随机、无全局单例）。
- Executor 稳定提交点落事件（model attempt 完整结束 / tool result 已提交 / approval 持久化 / compact 提交 / 终态）。
- **`_turns` → Reducer Projection**；`ExecutionJournalService`/`chat_history.json` 作为历史源的职责删除（不保留读取 fallback）。
- ContextVar 全局 getter（`get_permission_service`/`get_background_task_service` 等 Legacy Adapter）随 `RuntimeContext` 注入移除。
- Checkpoint 绑定 Journal cursor；`AgentLoopState` 变为 Reducer 派生状态。
- 子代理事件进入同一 run 的 Journal（envelope `thread_id` 作用域），子代理 state/tmp/scripts 落点按 §18 布局。
- 验收：相同事件重放状态完全一致；重复 event_id 不重复副作用；半写尾部可恢复；旧历史源下线且无读取回退。

### P3 — Tool Transaction / Resume
- Invocation Ledger、Approval Fingerprint（§6.3）、幂等键（§6.5）、工具终态机（`proposed→...→succeeded/failed/denied/cancelled/indeterminate`）。
- Pending Transaction Reconciliation、Indeterminate 先 reconcile 禁止直接重试。
- 重开真正的 Checkpoint Resume：cursor + Reducer Replay + Reconciliation（§16.4）。
- 验收：§25.4（指纹参数变化失效、symlink/traversal 拒绝、timeout 标记 indeterminate、幂等防重复写、批准走统一持久链路）+ §25.6（fault-injection crash 恢复不重复副作用）。

### P4 — Model Layer
- `ModelTransport`（纯 Wire I/O）→ `ProviderCodec`（原生块无损保存）→ `ResponsePipeline`（状态化组装）→ `ModelCapabilities`（数据化，消除 `model.startswith(...)`）。
- `ProviderPipeline` 收敛为 Codec；现有 OpenAI SDK 包装为 `OpenAIChatTransport`。
- Retry Advice 由 Transport 返回、Orchestrator 决策（§7.7）。
- 验收：§25.2 Golden Fixtures 全绿；Capability 分层层级覆盖（provider→family→exact→discovery）。

### P5 — Context / Memory
- `Projection Manifest`（每次 Model Call 回答"模型实际看到了什么"，§9.2）+ 输入预算（§9.3）。
- Journal-backed Compact（§9.4 Atomic Groups / §9.5 Summary Event / §9.6 算法）。
- Soul/Core/AGENTS 版本化 + Provenance（§8.3）。
- 验收：§25.5（不拆 Atomic Group、不丢 replay block、不静默截断用户消息、原始 Journal 不变）。

### P6 — Sandbox / Artifact / Background
- `SandboxBackend`（§11.4）、OS/Container 强制边界。
- `ArtifactService`（§15：manifest / hash / MIME / retention / 原子发布）。
- Background Kill 验证链（`kill_requested→terminating→killed/kill_failed`）、Restart PID Reconcile、orphaned/unknown 状态。
- 验收：§25.7 相关项 + §23.2 OPEN 清单中对应项关闭。

### P7 — Child Thread Runtime
- 用 `ChildThread Runtime` 替换 ad-hoc Specialist：quota（max_turns/max_tokens/wall_clock_budget）、Typed Handoff、父取消树（`child_thread.cancelled` 事件链）、持久 lineage、`SandboxBackend` 隔离、子代理专用 Background Namespace。
- 不共享父的 mutable history / ToolLoader loaded set / Compressor / Permission Context / MCP owner state。
- 验收：§25.7（Child Context/ToolLoader/Compressor/Permission 不串线、子代理不能越权、父取消传播、Artifact 不因沙盒销毁丢失、Cleanup 无残留订阅）。

### P8 — Storage 收敛与宿主（双轨）
- 选定 Journal Backend 最终形态；SQLite 只作 Backend 或 Index。
- SSE 事件改由 Journal 派生 + `after_sequence` 重放（Presentation Preview 与 Committed Event 对账，§4.4/§10.1）。
- SDK 公共 API 按 TARGET 契约重定义，web/desktop/TUI 同批次适配。
- 验收：桌面端验收清单（ASK 顺序/重连/重复响应、Preview 对账、断线重放、Background 主动唤醒、Pause/Abort/Resume 可见状态、多 Agent 隔离、Crash 后 Pending Reconcile）。

## 5. 子代理专项（Specialist → ChildThread）

### 现状（已核实）
- `NativeFloodAgent._run_specialist`（`native_flood_agent.py:1985`）：独立 `sub_session_id`（字符串拼装）、`SandboxService` 沙盒隔离、`_make_specialist_tool_runtime()` 独立 Tool 运行时、`ArtifactWatcher` baseline+copy、`SubAgentReport`、`abort_check` 透传。
- 差距：非标准 thread_id；共享父 `state_dir/tmp/scripts_dir`（`native_flood_agent.py:2023-2026`）；无 ChildThread 契约与 quota；无 lineage 持久化；Typed Handoff 只是 dict；无 cancellation-tree 事件；`background_task_service` 未注入 → 共享全局后台命名空间。

### 目标
`ChildThread(thread_id, parent_thread_id, parent_call_id, workspace_snapshot, sandbox_id, tool_allowlist, max_turns, max_tokens, wall_clock_budget)`；父只收 `SubagentAccepted/Running/Result/Failed/Cancelled` + Typed Artifact Reference；不把子代理完整 scratch history 拼进父上下文；取消走 §2.5 层级传播。

### 分阶段落点
- P1：`ChildThread` 契约 + `thread_id` 纳入身份层级 + Typed Result 协议。
- P2：子代理事件入同一 run Journal（`thread_id` 作用域）+ 独立 state/tmp/scripts 落点 + Child Background 命名空间。
- P7：ChildThread Runtime 替换 ad-hoc Specialist（quota / typed handoff / 取消树 / lineage / SandboxBackend）。

## 6. 关键原则（贯穿全部阶段）

1. **不向后兼容、不 fallback**：不新增/不保留旧接口适配器、降级分支、双写路径、`chat_history.json` 读取回退。
2. **单一事实源**：Canonical Journal 是唯一运行事实源；派生状态（前端消息、checkpoint、trace、搜索）均可删除并从 Journal 重建。
3. **模块化**：遵守 `modular-code-principle`——按模块/契约落实现，不打补丁式修改。
4. **回归策略**：§23.2 桌面端问题清单的回归测试保留并重写为对新架构的断言；断言旧架构行为（`_turns` 唯一源、`chat_history.json`）的测试按 forward-only 重写。
5. **每个阶段独立可验证**：新测试 + 受影响旧测试重写 → 全绿 → 进下一阶段。

## 7. 验证口径

- 每个阶段结束时运行：`python -m pytest -q --deselect tests/test_profile_system.py::test_10_pytest`（P0 修复后无需 deselect）。
- 关键验收节：§25.1 Journal/Reducer、§25.2 Provider、§25.3 Streaming、§25.4 Tool/Permission、§25.5 Compact、§25.6 Resume Fault Injection、§25.7 Child/Background、§25.8 Security/Privacy。
- 桌面端/宿主集成在每个架构阶段覆盖：ASK 顺序/重连、Preview/Committed 对账、断线重放、Background 主动唤醒、Pause/Abort/Resume 可见状态、多 Agent 隔离、Crash 后 Pending Reconcile。
