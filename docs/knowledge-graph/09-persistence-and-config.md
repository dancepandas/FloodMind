# 09. 持久化布局与配置

> 用途：排障、迁移、清理。所有路径以代码为准。

## 1. 三类根目录

| 根 | 默认 | 控制方式 | 内容 |
|---|---|---|---|
| FloodMind Home | `~/.floodmind/` | `FLOODMIND_HOME` > 活跃 profile 目录 > 默认（`floodmind/config/settings.py:72-84`） | `settings.json`、`active_profile`、`mcp.json`、人格文件 |
| 项目根 | 仓库根 | `FLOODMIND_PROJECT_ROOT` > 包父目录；site-packages 安装形态回退 `~/.floodmind`（`floodmind/agent/runtime/services/_runtime_root.py:31-58`） | `.floodmind/`（运行数据）、`data/`（项目级记忆与旧数据） |
| 工作区根 | `Workspace.from_cwd()` 或宿主注入 | `Agent(workspace=...)`、`bind_workspace` | 会话目录、产物、临时文件 |

Profile 目录为 `~/.floodmind/profiles/<name>/`，由 `~/.floodmind/active_profile` 或 `FLOODMIND_PROFILE` 选择。

## 2. 运行数据布局

```text
<runtime_root>/.floodmind/
  conversations/<conversation_id>/
    tasks/<task_id>/
      runs/<run_id>/
        lease.json                      恢复入场锁
        journal/
          events-000000.jsonl           权威事件
          .lock                         跨进程文件锁
          index.json                    writer 快速恢复索引
          journal.sqlite3               派生索引（可重建）
        threads/<thread_id>/
          state/                        检查点与状态
          tmp/                          临时文件
          scripts/                      脚本
          artifacts/                    产物存储
  skill_suggestions/<session_id>.json   回顾生成的技能建议
  sessions/                             旧会话（遗留）
```

工作区侧（folder-first）：

```text
<workspace>/
  .floodmind/
    sessions/<session_id>/
    artifacts/
    tmp/
```

检查点写入 `threads/<thread_id>/state/` 下的 `state.json`、`run_state.json`、`manifest.json`，原子 rename。

注意：旧版本检查点目录会连带保存工作区文件快照（`checkpoints/**/files/`），其中可能递归嵌套更早的 `.floodmind`；这是历史数据，不是当前写入契约。

## 3. 项目级记忆文件

| 文件 | 内容 | 原子性 |
|---|---|---|
| `data/memory/semantic_memory.json` | 语义事实 `version: 2` | tmp + fsync + replace（`semantic_store.py`） |
| `data/memory/core_memory.json` | 项目级核心记忆 | tmp + fsync + replace（`core_memory.py`） |
| `data/memory/long_term_memory.json` | 旧 LTM（`MemoryAdd` 已不写；仅 consolidation 未接线/禁用时由 `background_review` 旧直写回退追加） | tmp + fsync + replace，add 路径先合并磁盘新增（`dual_memory.py:122-156`） |
| `data/task_experience/tree_index.json` + 叶子 | 经验树 | tmp + replace，无 fsync（`experience_tree.py:291-307`） |
| `skills/.floodmind/task_experience/tree_index.json` | owner-bound 经验树 | 同上 |
| `data/sessions/.session_index.json` | 旧会话索引 | 直接写，非原子 |
| `data/sessions/<sid>/session.json` | 旧会话元数据 | 直接写，非原子 |
| `data/sessions/<sid>/msg_*.json` | 旧式消息 | 单文件直接写 |
| `data/sessions/<sid>/trace.jsonl` | 追踪 | 追加写 |
| `data/sessions/<sid>/metrics.jsonl` | 每次 flush 的指标与成本快照 | 追加写 |
| `<state_dir>/sessions/<sid>/plan.json` + `plan.md` | 执行计划（含步骤状态/产物） | tmp + `os.replace` 原子写 |

记忆文件默认落在 `PROJECT_ROOT/data/memory/`；site-packages 安装形态回退到 `~/.floodmind/*.json`（各存储模块顶部常量）。

`data/` 的位置仍不统一：

- `floodmind/agent/native/native_flood_agent.py:2816,2834` 使用 `DATA_DIR` 或 `Path.cwd()/data`；
- `floodmind/agent/runtime/services/path_service.py:403` 使用 `DATA_DIR` 或项目根 `data`。

排障时先确认进程 cwd 与实际 `PROJECT_ROOT`。

## 4. 事件与状态的关系

| 数据 | 是否权威 | 可否重建 |
|---|---|---|
| `events-*.jsonl` | 是 | 否，必须备份 |
| `index.json` / `journal.sqlite3` | 否 | 是，从 JSONL 重建 |
| `RunState` 快照 | 否 | 是，从 JSONL 重放 |
| `turns` / 历史 | 否 | 是 |
| 语义库/核心记忆/经验树 | 是（各自的域） | 否 |
| `session.json` / `msg_*.json` | 否（展示层） | 不能从它恢复对话 |
| `plan.json` | 是（规划产物） | 否，跨 run 恢复未完成计划；`plan.md` 是只读渲染 |
| `trace.jsonl` | 否 | 否，仅观测 |

迁移或清理时必须保留 `events-*.jsonl`。删除 `index.json`、`journal.sqlite3`、检查点都是安全的，系统会重建或从更早状态恢复。

Journal 写入带 fencing：`JournalWriter` 在文件锁内校验 lease token，lease 失效/易主/缺失时拒绝追加（`journal_writer.py:143-150,274,342`）。因此 `lease.json` 不能当作普通临时文件随手删除。

## 5. 环境变量

| 变量 | 作用 |
|---|---|
| `FLOODMIND_HOME` | 覆盖 FloodMind Home |
| `FLOODMIND_PROFILE` | 选择 profile |
| `FLOODMIND_PROJECT_ROOT` | 覆盖项目根，影响 `.floodmind` 与 `data/` 定位 |
| `FLOODMIND_MODEL` | 覆盖模型 |
| `FLOODMIND_PROVIDER` | 覆盖供应商 |
| `FLOODMIND_API_KEY` | 全局 API Key 覆盖 |
| `FLOODMIND_BASE_URL` | 覆盖 Base URL |
| `FLOODMIND_ENABLE_REASONING` | 默认开启推理 |
| `FLOODMIND_ENABLE_SEARCH` | 默认开启搜索 |
| `FLOODMIND_SEARCH_ENGINE` / `FLOODMIND_SEARCH_URL` / `FLOODMIND_SEARCH_API_KEY` | 搜索配置 |
| `BAIDU_API_KEY` | 搜索兜底 Key |
| `FLOODMIND_USER_DIR` / `FLOODMIND_SESSION_ROOT` | 工作区与会话根 |
| `FLOODMIND_SANDBOX_STRATEGY` | 沙箱策略 |
| `FLOODMIND_OVERWRITE_PROTECTION` | 覆盖保护 |
| `FLOODMIND_GATEWAY_TOKEN` / `FLOODMIND_GATEWAY_WORKSPACE` | 网关鉴权与工作区 |
| `DATA_DIR` | 旧数据目录覆盖 |
| 供应商变量 | `DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY` 等 |

模型回退与子代理路由没有独立环境变量，写在 `settings.json` 的 `model_routing` 段。

优先级与已知问题见 [02](./02-sdk-entrypoints-config.md) 第 5 节。

## 6. settings.json 结构

`DEFAULT_CONFIG`（`floodmind/config/settings.py`）当前包含：

| 段 | 内容 |
|---|---|
| `providers` | 供应商配置：`api_key`、`base_url`、`models`、`context_window` |
| `task_experience` | 经验捕获目录与阈值 |
| `background_review` | 回顾开关与最小消息数 |
| `memory_recall` | 召回开关、top_k、衰减与字符预算 |
| `memory_consolidation` | 固化开关与冲突候选数 |
| `core_memory` | 开关、字符预算、旧数据迁移 |
| `tool_loading` | progressive/eager、核心工具与加载上限 |
| `model_routing` | `subagent_model`、`fallbacks`、`max_fallbacks` |
| `api` | 宿主 API 基址与超时 |
| `workspace` | 用户目录、会话根、沙箱策略、覆盖保护 |

另有运行时写入的 `gateway.auth_token`（自动生成，`enc:v1:` 密文）。MCP servers 不在 `settings.json`，而是 `get_floodmind_home()/mcp.json`。

模板 `floodmind/config/settings_template.json` 只提供 `providers`、`tool_loading`、`model_routing` 三段作为起点，其余段由代码默认值补齐。

敏感值静态加密：settings 树中的 API Key、token、搜索 Key、MCP 鉴权 header 在保存时转为 `enc:v1:`；Windows 用 DPAPI，其他平台用 Fernet（密钥文件 `~/.floodmind/.secret_key`，0600）。解密失败按空值处理并告警，不会把密文当凭据使用（`floodmind/config/secret_store.py`）。

旧配置 `~/.config/floodmind/config.json` 仍会被合并（`settings.py:364`），且后合并可能覆盖新配置。

## 7. 排障顺序

1. 确认 `FLOODMIND_HOME` 与实际 profile。
2. 确认进程 cwd 与 `FLOODMIND_PROJECT_ROOT`，定位 `.floodmind/` 与 `data/`。
3. 查看 `journal/events-*.jsonl` 是否存在、最后一行是否完整。
4. 若索引异常，删除 `journal.sqlite3` 与 `index.json` 让其重建。
5. 检查 `lease.json` 是否残留未释放的恢复锁；不要绕过 fencing 手工改 lease。
6. 检查 `data/memory/*.json` 是否损坏或时间戳停滞。
7. 检查 `trace.jsonl` 的 base 是否落在 `Path.cwd()/data/sessions` 或项目根。
8. 配置里的凭据显示为 `enc:v1:` 是正常现象；若解密告警，检查是否跨用户/跨机器复制了 settings。

## 8. 清理建议

| 对象 | 是否可删 | 说明 |
|---|---|---|
| `journal/journal.sqlite3` | 可 | 派生索引 |
| `journal/index.json` | 可 | writer 重建 |
| 检查点 | 可 | 丢失恢复点 |
| `trace.jsonl` | 可 | 观测数据 |
| `tmp/` | 可 | 临时文件 |
| `journal/events-*.jsonl` | **不可** | 唯一事实源 |
| `lease.json` | 谨慎 | 影响 fencing 与恢复；按服务生命周期清理 |
| `data/memory/*.json` | 谨慎 | 项目级记忆，删除即丢失 |
| `data/sessions/` | 谨慎 | 旧展示层，删除不影响 Journal，但前端历史会消失 |
| `.floodmind/**/checkpoints/**/files/` | 谨慎 | 旧版检查点的工作区文件快照，可能递归嵌套；确认无恢复需求后清理 |

扫描或备份时排除 `.claude/worktrees/`，否则会把 agent 工作副本里的前端文件当成主工作区内容。
