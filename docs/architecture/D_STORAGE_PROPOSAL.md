# D 阶段方案：存储/配置单一源（P1-5 / P1-6 / P1-7）

> 本文件是 D 阶段“先出方案再动手”的设计稿，**需用户拍板 A/B 后再执行**。
> 评估背景见 [`ASSESSMENT.md`](./ASSESSMENT.md) P1-5/6/7。

## 1. 现状：三套存储并存

| 存储 | 位置 | 角色 | 状态 |
|---|---|---|---|
| `chat_history.json` | `data/sessions/<sid>/memory/` | 会话历史全量（`memory._turns`） | **LIVE**（批次 A/B 确立为唯一历史源；前端展示 + MemorySearch 检索源） |
| `sessions.db` | `data/sessions/` | SQLite | **半暗**：仅 `sync_events`(回放) + `search_index`(FTS5) 在用；其余表与 chat_history.json + sync_events 信息重复 |
| 配置 | `~/.floodmind/settings.json` | 真配置（settings 模块加载/保存） | **LIVE** |

`sessions.db` 表清单（`session_store.py`）：
- ✅ **LIVE**：`sync_events`(53 行起 SSE 回放)、`search_index`(FTS5，`search_sessions` 919 行)
- ❌ **暗表**：`sessions`(53)、`messages`(63)、`parts`(80)、`tool_states`(90)、`revert_points`(104)、`checkpoints`(116)
- ⚠️ `migrate_from_json`(823 行)：**从不自动运行** —— 典型“未完成的迁移”

## 2. 问题（评估 P1-5/6/7）

- **P1-5（经核实为误判）**：`cli config set` 实际已通过 `save_config()` 写入 `~/.floodmind/settings.json`（真路径），**仅 docstring 过时**（已修正）。非功能 bug。
- **P1-6**：`sessions.db` 暗表（sessions/messages/parts/tool_states/revert_points/checkpoints）与 `chat_history.json` + `sync_events` 信息重复；`migrate_from_json` 从不自动跑 → “未完成的迁移”债。
- **P1-7**：`CheckpointService` 每个状态边界写 checkpoint，但 stream 已不从 checkpoint 恢复（memory 是源，批次 A）。`load()` 仅 CLI/Flask rollback 在用 → 半退休。

## 3. 核心决策：SQLite 升为唯一源 (A) vs JSON 为主 + SQLite 瘦身 (B)

### 方案 A — SQLite 升为唯一存储
- `chat_history.json` 迁入 `sessions.db`（messages/parts 表成权威）。
- **优点**：事务性、可查询（FTS5 已就绪）、单一存储、消除 JSON 文件并发问题。
- **缺点**：
  - **大迁移**：DualMemory 持久化层重写（`_turns` ↔ SQLite），触碰批次 A/B 刚刚稳固的历史路径，回归风险高。
  - web + scheduler 同会话并发从“文件锁”问题（P0-7）转为 SQLite 锁问题，仍需处理。
  - memory._turns 的扁平结构 → 关系表需设计映射，破坏现有简单性。

### 方案 B — JSON 为历史源，SQLite 瘦身（推荐）
- `chat_history.json` 保持为会话历史权威（不动批次 A/B 成果）。
- `sessions.db` 砍到只剩 `sync_events` + `search_index`(FTS5)。删暗表代码（sessions/messages/parts/tool_states/revert_points/checkpoints 的写路径）或保留表结构但停写。
- FTS5 `search_index` 由 `chat_history.json` 单向索引重建（已有 `migrate_from_json` 可改造为重建器）。
- **优点**：
  - 不动工作良好的历史路径，**低回归风险**。
  - 直接消除“未完成迁移”债（暗表是债的主体）。
  - 符合“前端←全量 chat_history.json、检索←FTS5”的既定分层。
- **缺点**：
  - JSON 无事务；web + scheduler 并发写 `chat_history.json` 仍需文件锁（P0-7，独立项，加 `filelock`）。
  - FTS5 索引需保持与 json 同步（写 chat_history 时 upsert search_index，已有路径可接）。

### 推荐：**方案 B**
理由：批次 A/B 刚把 `memory._turns → chat_history.json` 确立为单一历史源并稳固；此时再迁 SQLite 是把已收敛的系统重新拆开，违背“单一源/反屎山”。暗表才是债——瘦身即可去债而不碰工作路径。P0-7 文件锁无论 A/B 都要做，不构成选 A 的理由。

## 4. P1-7 CheckpointService 处置（与 A/B 无关，可独立做）

- 语义拆分：**审计快照**（保留，降频——仅终态/里程碑写，不再每状态边界写）vs **恢复快照**（删——stream 已不从 checkpoint 恢复）。
- 保留 `rollback_files`（CLI 文件级回滚仍用）；`load()` 仅留 rollback 路径。
- 收益：减少每轮 checkpoint 写盘（P2 性能），消除“半退休”歧义。

## 5. 执行路线图（待选 A/B 后分阶段 + 每阶段审查闭环）

**若选 B（推荐）：**
- D-1：核实暗表（sessions/messages/parts/tool_states/revert_points/checkpoints）的写路径零外部依赖 → 删写路径/停写，保留表兼容存量。
- D-2：`migrate_from_json` 改造为“FTS5 重建器”（从 chat_history.json 建 search_index），或确认 search_index 已由写路径增量维护。
- D-3：CheckpointService 拆分审计/恢复（P1-7）。
- D-4：P0-7 文件锁（web + scheduler 并发写 chat_history.json）—— 独立项，可与 D 并行。

**若选 A：**
- D-A1：DualMemory 持久化层改 SQLite（_turns ↔ messages/parts 映射）—— 大工程，需独立专项。
- D-A2：迁移存量 chat_history.json → SQLite（一次性脚本）。
- D-A3：删 JSON 持久化路径 + 文件锁 concerns。
- D-A4：同 D-3/D-4。

## 6. 待用户决策

- **A 还是 B？**（推荐 B）
- 若 B：D-1..D-4 的优先级/范围确认。
- P0-7 文件锁是否纳入本批 D。
