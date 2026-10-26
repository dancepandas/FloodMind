# floodmind/agent — Agent 门面与运行时装配

Agent 子系统的门面层：对宿主暴露 SDK，对内装配 Native 执行引擎、Runtime 服务、MCP、Guardrail、Handoff、子代理与定时任务。

## 目录

| 目录 | 作用 |
| --- | --- |
| `native/` | 自研 Agent 执行引擎（主循环、模型客户端、事件、压缩、工具加载） |
| `runtime/` | 数据契约、可复用服务、传输适配与确定性 reducer |

## 主要文件

- `api.py` — `Agent` SDK 类，封装 `NativeFloodAgent(bare=True)`，提供 `run` / `stream` / `chat` / `resume` / `events_after` / 权限应答 / 后台任务 API。
- `agent_registry.py` — 内置 Agent 类型（build / plan / general / explore）及其工具集合。
- `context_runtime.py` — 带优先级的上下文块：system prompt、项目规则、core memory、技能目录。
- `guardrail.py` — 输入/输出 Guardrail 契约与执行器，tripwire 后停止。
- `handoff.py` — `HandoffTarget`，把控制权移交给另一个 Agent（区别于子代理结果回流）。
- `mcp_client.py` — MCP 连接池与 `ToolSpec` 构造，连接管理与工具注册解耦。
- `scheduled_task_runtime.py` — 定时 Agent 任务的持久化与调度。
- `sdk_events.py` — canonical 事件到公共 `SdkEvent` 的投影。
- `__init__.py` — 懒加载导出，避免 import 时初始化 settings。

## 约定

- 编排逻辑属于 `native/` 与 `runtime/`；`api.py` 只做宿主人机接口。
- Handoff 接管同一 run 的后续模型调用；需要子任务结果回流时使用 `SubAgent`。
- MCP 连接池不负责工具注册与清理，调用方负责生命周期。
- Guardrail 输入侧校验完整消息列表，输出侧校验最终答案，首次拦截自动修正重试一次。
