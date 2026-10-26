# floodmind/agent/runtime/contracts — 纯数据协议

Runtime 的协议模型层。所有模块共享的身份、消息、事件、工具、权限与状态结构都定义在这里，不含 I/O 与业务逻辑。

## 主要模块

| 文件 | 内容 |
| --- | --- |
| `identity.py` | canonical ID 前缀、`new_id()`、`Identity` |
| `canonical_events.py` | `EventEnvelope`、Actor、Provenance、完整性哈希 |
| `canonical_parts.py` | `CanonicalPart` 与流式 part 事件 |
| `messages.py` | LangChain-free 的 `Message` / `Document` / `MessageStore` |
| `tools.py` | `ToolCall`、`ToolResult`、`ToolSpec`、`ToolExecutionContext` |
| `permissions.py` | 权限决策、ASK 请求/快照、工具策略、`ToolFeedback` |
| `paths.py` | 路径解析请求与结果 |
| `run_state.py` | `RunStatus`、`RunState`、挂起审批/事务/子线程 |
| `tool_transaction.py` | 工具事务状态机与合法迁移 |
| `checkpoints.py` | Checkpoint record / summary / manifest |
| `child_thread.py` | 子代理线程与 `SubagentResult` |
| `projection.py` | 输入预算与 Projection Manifest |
| `runtime_context.py` | frozen 的 per-run `RuntimeContext` |
| `sandbox.py` | 资源限制、沙盒策略与后端协议 |
| `events.py` | SSE 事件协议与 `VALID_EVENT_TYPES` |
| `artifacts.py` | 产物清单与声明 |
| `tracing.py` | TraceSpan / TraceEvent |
| `workspace.py` | frozen `Workspace`，folder-first 语义与读写根 |
| `approval.py` | 审批记录 |

## 约定

- 只放纯数据结构；不写 I/O、网络、文件系统副作用。
- `Workspace` 与 `RuntimeContext` 是 frozen dataclass，运行期不可原地修改。
- `tool_transaction.py` 的状态迁移被 reducer 与执行器共同依赖，改状态机必须同步所有消费方。
- `permissions.py` 的优先级语义由 `PermissionService` 实现，不能在契约层单独改语义。
- 目录内个别模块为获取当前上下文会引用 `floodmind.tools.session_context`，新增反向依赖前需评估分层。
