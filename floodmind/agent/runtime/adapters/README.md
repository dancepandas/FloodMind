# floodmind/agent/runtime/adapters — 传输无关适配层

把 HTTP/Flask/SSE 等外部载荷翻译成 Runtime 服务调用，并统一客户端事件的清洗与校验。适配层不导入 Flask，也不承载业务决策。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `permission_api.py` | 权限应答、待处理列表、取消会话（调用 `AskService`） |
| `checkpoint_api.py` | 列出/读取/回滚 Checkpoint（调用 `CheckpointService`，白名单序列化） |
| `tracing_api.py` | 读取会话 trace 事件与路径 |
| `event_stream_adapter.py` | 注册/移除 ASK 事件回调、校验并清洗 SSE 事件 |
| `flask_permission_api.py` / `flask_checkpoint_api.py` / `flask_tracing_api.py` | 旧路径兼容别名，仅重导出 |
| `sse_stream_adapter.py` | `event_stream_adapter` 的旧名别名 |

## 约定

- 权限判定属于 `PermissionService`；这里只转发用户应答。
- 事件流必须经过 `VALID_EVENT_TYPES` 校验，`__` 前缀内部字段不得直接下发。
- Checkpoint 对外序列化使用字段白名单，避免泄露绝对路径与内部运行字段。
- 新增适配器保持“载荷翻译”定位，不引入业务分支。
