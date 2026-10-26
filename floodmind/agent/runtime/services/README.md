# floodmind/agent/runtime/services — Runtime 服务

Agent 运行期的可复用服务集合。所有服务与传输层解耦，可被 SDK、CLI、网关共同使用。

## 服务分区

### 执行与权限

| 文件 | 职责 |
| --- | --- |
| `tool_execution_service.py` | 唯一工具执行管线：解析、权限、ASK、校验、执行、结果归一、发事件 |
| `permission_service.py` | 统一权限决策与固定优先级 |
| `ask_service.py` | 结构化 ASK：发事件并阻塞等待宿主应答，按会话设置超时 |
| `path_service.py` | 统一路径解析与读写/执行白名单判断 |
| `sandbox_service.py` / `process_sandbox.py` / `sandbox_backend.py` | 子代理工作区隔离与进程树清理 |
| `exec_write_scanner.py` | exec 命令中文件写入的保守静态扫描 |
| `credential_env.py` | 子进程/MCP 环境的凭证过滤与显式注入 |

### 持久化与恢复

| 文件 | 职责 |
| --- | --- |
| `journal_authority.py` / `journal_writer.py` / `journal_index.py` / `journal_backend.py` | Canonical JSONL Journal 写入与 SQLite 派生索引 |
| `checkpoint_service.py` | Agent 状态快照的原子保存/加载/回滚 |
| `resume_service.py` | fencing lease + journal replay + reconcile |
| `reconciliation_service.py` | pending/indeterminate 事务与悬空审批的对账 |
| `idempotency.py` | 非只读工具的幂等键与已提交结果复用 |
| `artifact_service.py` | 内容寻址产物的原子发布与校验 |
| `plan_store.py` | 执行计划的 `plan.json` / `plan.md` 持久化 |

### 运行支撑

| 文件 | 职责 |
| --- | --- |
| `workspace_service.py` | `Workspace` 的 contextvar 注入与构造 |
| `child_thread_runtime.py` | 子代理生命周期与 typed `SubagentResult` |
| `child_permission_context.py` | 子代理独立 PathService / PermissionService |
| `background_task_service.py` | 后台命令任务、文件化输出、并发与存活护栏 |
| `tracing_service.py` | Trace 收集、`trace.jsonl` 落盘、EventBus/OTLP 导出 |
| `usage_accounting.py` / `otlp_exporter.py` | 用量与成本归因、OTLP 导出 |
| `tool_output_service.py` | 超大工具输出卸载为文件并返回有界摘要 |
| `history_projection.py` | Journal 到扁平对话 turn 的投影 |
| `run_identity.py` / `runtime_layout.py` / `process_identity.py` | 运行身份、目录布局、PID 身份匹配 |
| `provenance.py` | 非用户原文内容的来源标记 |
| `approval_fingerprint.py` | 审批指纹（纯函数） |

## 关键不变量

- `ToolExecutionService` 是唯一调用工具函数的地方；工具自身不做权限检查。
- 权限优先级固定：工具级 DENY → 子代理/planning 硬门 → 全局 deny → 工具级 ASK → 全局 allow → 默认。
- JSONL Journal 是唯一权威事实源，SQLite 只是可重建索引。
- 恢复路径必须先获得 fencing lease，失败 fail-closed。
- `credential_env` 默认过滤凭证变量；凭证进入子进程必须显式 allowlist。
- 子代理 `ask_service=None`，父规则以不可变快照复制，tier 硬门继续生效。
