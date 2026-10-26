# floodmind/gateway — HTTP / Web 网关

把 SDK Agent Runtime 暴露为 FastAPI 服务，并提供 Chainlit 前端桥接、会话历史与鉴权。属于可选能力（`pip install -e ".[gateway]"`）。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `app.py` | FastAPI 应用：健康检查、会话 CRUD、SSE 对话、中断、权限应答、文件读取 |
| `server.py` | `run_gateway()`：uvicorn 启动、鉴权策略、自动打开浏览器 |
| `state.py` | `GatewayState`：SessionManager、按会话 abort 事件、Agent 工厂、token 解析 |
| `chainlit_app.py` | Agent 流式事件到 Chainlit 的桥接 |
| `chainlit_history.py` | Chainlit 本地历史持久化与本地单用户鉴权补丁 |
| `static/` / `chainlit_public/` | 内置 Web UI 与品牌资源 |

## 约定

- 除 `/api/health` 外所有 `/api` 路由都依赖 token；开启鉴权但 token 为空返回 503，不匹配返回 401，不得静默放行。
- `/api/chat` 的 SSE 生成器在 `finally` 中禁止 `yield`（客户端断开时避免 `GeneratorExit`）。
- 权限 ASK 非阻塞：前端通过 `POST /api/permission/respond` 应答，executor 在挂起处续跑。
- `/api/file` 必须做工作区 containment 校验。
- `GatewayState` 刻意不启动会话清理线程，避免与运行中的 journal 写入竞争。
- `chainlit_history.install()` 必须在 Chainlit 回调注册前执行。
