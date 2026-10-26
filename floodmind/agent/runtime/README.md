# floodmind/agent/runtime — Runtime 基础层

Agent 运行期的基础设施：纯数据契约、可复用服务、传输无关适配，以及确定性的状态 reducer。

## 分层

| 目录 | 职责 |
| --- | --- |
| `contracts/` | 纯数据协议：身份、消息、事件、工具、权限、路径、状态、Checkpoint、Workspace |
| `services/` | 可复用服务：路径、权限、ASK、工具执行、沙盒、Journal、Checkpoint、产物、追踪 |
| `adapters/` | HTTP/SSE 载荷到 runtime 服务的翻译层 |

## 主要文件

- `reducer.py` — 纯函数 `reduce(state, event) -> state`，从 canonical 事件折叠出 `RunState`。

## 依赖方向

`adapters → services → contracts`；`native` 与 `tools` 依赖 contracts/services，反向依赖不允许。`services` 不得引入 Flask/HTTP 依赖。

## 关键不变量

- `reducer.py` 必须确定性：无 I/O、无随机、无当前时间、不修改输入对象。
- `contracts` 只放协议模型，不承载业务实现。
- `services` 与传输层解耦（AskService 可替换为 Redis/消息队列实现）。
- `adapters` 只翻译载荷，不做权限判断与业务决策。
