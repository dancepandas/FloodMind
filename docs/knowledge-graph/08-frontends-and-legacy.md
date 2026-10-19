# 08. 前端、网关与平台边界

> 结论先行：当前是“活跃 gateway + 外部 desktop 仓库”，旧 Web/TUI/TS 前端已全部删除。

## 1. 状态矩阵

| 目录 | 技术栈 | 状态 | 证据 | 启动方式 |
|---|---|---|---|---|
| `floodmind/gateway/` | FastAPI + uvicorn；`static/index.html` 单文件 UI；Chainlit 可选 | **活跃** | CLI 注册 `gateway` 命令（`floodmind/cli.py:145-190`）；`pyproject.toml` 有 `[gateway]`、`[chainlit-ui]` extras 并打包静态资源；README 有专门章节 | `pip install -e ".[gateway]"`；`floodmind gateway [--ui builtin\|chainlit]` |
| `gateway/static/` | 单文件 HTML/CSS/JS | **活跃**（默认 UI） | `app.py` 挂载 `/`（`floodmind/gateway/app.py:251`） | 随 gateway 启动 |
| `gateway/chainlit_public/` | 品牌资源 | **活跃**（Chainlit 模式） | 打包配置、README | 启动时复制到 workspace |
| `desktop/` | Electron | **外部仓库** | 自带 `.git`，主仓库 `.gitignore` 忽略 | 独立仓库维护 |
| `skills/` | SKILL.md + scripts | **活跃** | `SkillRegistry` 默认发现根为 `skills/` 与 `.claude/skills`（`floodmind/skills/registry.py:89-92`）；项目级写入根默认 `skills/`（`:177`） | 无独立启动 |
| `evals/`、`benchmarks/` | Python 脚本 | **活跃（质量门禁）** | 见 10 与 12；不提供用户界面 | `python -m evals.runner`、`python -m benchmarks.multi_agent.runner` |

已删除、不再存在于工作区的旧边界：

| 路径 | 原技术栈 | 处置 |
|---|---|---|
| `web/` | React 19 + Vite + Radix/Tailwind | 删除（协议停留在旧后端，与 gateway 不兼容） |
| `floodmind/tui-ts/` | Bun + Ink | 删除（无人维护，硬编码旧端口） |
| `sdk-ts/` | TypeScript / Vitest / DSH | 删除（未完成实验，不可构建） |
| `floodmind/tui/` | Python TUI | 删除（此前已移除源码，本轮清掉剩余 pyc） |
| `floodmind/server/` | Python FastAPI 旧路由 | 删除（此前已移除源码，本轮清掉剩余 pyc） |

## 2. Gateway（活跃）

### 2.1 启动链

```text
floodmind gateway
  └─ cli.py:163 gateway 命令
       ├─ --ui builtin  → gateway/server.py:run_gateway → uvicorn
       └─ --ui chainlit → 子进程 python -m chainlit run gateway/chainlit_app.py
```

### 2.2 内置网关端点

| 方法 | 路径 | 作用 | 鉴权 |
|---|---|---|---|
| GET | `/api/health` | 健康检查 | 否 |
| GET/POST/DELETE | `/api/sessions` | 会话列表/创建/删除 | 是 |
| GET | `/api/sessions/{id}/messages` | 历史消息分页 | 是 |
| POST | `/api/chat` | SSE 对话流 | 是 |
| POST | `/api/chat/abort` | 中断 | 是 |
| POST | `/api/permission/respond` | 权限响应 | 是 |
| GET | `/api/file` | 文件下载 | 是 |
| GET | `/` | 内置 UI | 否 |

路由定义见 `floodmind/gateway/app.py:70-251`。SSE 格式：`data: <json>\n\n`，结束标记 `{"type":"__done__"}`。

下发事件：`answer_delta`、`thought_delta`、`action_start`、`action_end`、`workflow_plan`、`workflow_step`、`permission_ask`、`llm_step_end`、`token_usage`、`llm_token_error`、`file_generated`、`image_generated`。

### 2.3 鉴权

- 除 `/api/health` 与 `/` 外，所有路由依赖 `require_token`（`app.py:59-66`）。
- token 优先级：显式 `--token` > `FLOODMIND_GATEWAY_TOKEN` > `settings.json` 的 `gateway.auth_token`（`floodmind/gateway/state.py:108-128`）。
- 未配置 token 时自动生成 `fmgt-<40 hex>` 并持久化；`gateway.auth_token` 在 `settings.json` 中以 `enc:v1:` 密文保存（DPAPI/Fernet，见 `floodmind/config/secret_store.py`）。
- 开启鉴权但没有可用 token 时，路由直接 503 拒绝，不做静默放行；`--no-auth` 才显式关闭。
- 内置 UI 会带 token；仓库内已无其它前端消费这些端点。

### 2.4 Chainlit 模式

`chainlit_app.py` 直接在进程内 `from floodmind.agent.api import Agent`，消费 `agent.stream()`，把 SDK 事件映射成 `cl.Step` / `cl.Message` / `Action`。

已知脆弱点：

- 依赖对 Chainlit 内部机制的补丁（`local_steps` 清空、auth 固定用户、翻译包装、DOM/JS 注入）。
- `pyproject.toml` 只约束 `chainlit>=2.12`，没有上界。
- 品牌资源启动时复制到 workspace。

## 3. `desktop/`

- 独立 git 仓库，主仓库忽略。
- 与 FloodMind 的边界只体现在 Python SDK 的 desktop 契约：
  - 身份四元组 `conversation_id/task_id/run_id/thread_id`；
  - `events_after(sequence)` 对账（`floodmind/agent/api.py:245-252`）；
  - `resume(checkpoint_id)`（`api.py:254-...`，走 ResumeService replay + reconciliation）；
  - 权限 hook 与 `PermissionDecision`；
  - 后台任务 API。
- 本仓库没有可执行的 desktop 集成测试。

注意：`desktop/` 与根 `deepseek-harness/` 是两套东西，不要混淆版本与依赖。

## 4. 与 SDK 的对接

| 层 | 方式 | 事件协议 |
|---|---|---|
| gateway builtin | 进程内 `Agent.stream()` → SSE | 完整 SDK 事件 |
| gateway chainlit | 进程内 `Agent.stream()` → Chainlit 模型 | 子集映射 |
| `desktop` | 外部宿主，按 SDK 契约 | 结构化事件 |
| `skills/` | SDK 进程内扫描 | 无 |
| `evals/` | 进程内驱动真实执行器 | 不涉及宿主协议 |
| `benchmarks/` | 进程内构造 `Agent(bare=False)` | 复用 SDK 事件总线 |

## 5. 剩余风险

| # | 风险 | 影响 |
|---|---|---|
| 1 | Chainlit 私有补丁 + 无上界版本 | 升级即可能破坏审批卡/历史/品牌 |
| 2 | Gateway 无自动化测试 | CI 只跑 pytest 与 evals，SSE、权限 ASK、历史分页仍靠手工 e2e |
| 3 | 架构评估文档过期 | 仍引用已删除的 `server/agent_factory.py` |
| 4 | `desktop/` 双重身份 | 版本/依赖混淆 |
| 5 | `.claude/worktrees/` 中的 agent 工作副本 | 全仓搜索时可能命中历史副本，扫描时显式排除 |
| 6 | 旧检查点 `files/` 目录含工作区快照且递归嵌套 | 与“检查点只存状态”的表述冲突，磁盘膨胀 |

## 6. 后续清理事项

1. 给 gateway 补最小契约测试（健康检查 + `/api/chat` 冒烟 + 鉴权），纳入现有 `.github/workflows/ci.yml`。
2. 把 `docs/architecture/` 纳入版本控制，或在本知识图谱中明确它是历史快照（当前仍被 `.gitignore:10` 忽略）。
3. 视需要清理历史检查点中的工作区文件快照。
