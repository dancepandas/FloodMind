# floodmind/gateway/static — 内置 Web UI

网关自带的前端静态资源，由 `gateway/app.py` 挂载。属于可选网关能力，不影响 SDK 核心。

## 约定

- 资源通过包数据随 wheel 发布，新增文件需同步 `pyproject.toml` 的 package-data。
- 前端只通过网关 HTTP/SSE API 与后端交互，不直接依赖 Python 内部对象。
- 不要把运行时生成的文件写入本目录。
