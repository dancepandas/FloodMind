# floodmind/gateway/chainlit_public — Chainlit 品牌资源

Chainlit 前端使用的公开资源（图标、头像等），运行时由网关拷贝到工作区 `public/` 目录。

## 约定

- 本目录是资源源，`public/` 是生成副本，不提交生成结果。
- 新增资源需同步 `pyproject.toml` 的 package-data。
- 资源只影响展示，不参与 Agent 逻辑。
