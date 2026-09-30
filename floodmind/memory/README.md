# floodmind/memory — 记忆子系统目录说明

本目录是 FloodMind 记忆能力的实现层（P0 记忆增强）。核心思想：**门面统一装配、语义事实失效模型、core memory 项目级常驻**。

## 文件职责一览

| 文件 | 职责 |
| --- | --- |
| `services.py` | **门面（唯一装配入口）**。懒加载单例 `get_memory_services()` 装配 store/recall/consolidation/core 四服务，暴露 `on_turn_completed` / `on_run_completed` / `on_checkpoint` 生命周期钩子。**dsh 迁移边界：门面是唯一重绑点**，迁移时只改本文件，所有接线方零改动。 |
| `semantic_store.py` | 语义事实存储（`SemanticMemoryStore`）。Graphiti 式失效语义：UPDATE 不覆盖，旧条目打 `invalid_at`/`superseded_by` 保留时点回溯；DELETE 为软删除。提供 `extract_terms`、候选检索 `find_candidates` 等。 |
| `recall.py` | 三因子召回服务（`MemoryRecallService`）：相关度 × 新鲜度衰减 × 多样性，按用户输入构建召回块 `build_block()`，注入 system 上下文。 |
| `consolidation.py` | 四类操作固化管线（`MemoryConsolidationService`）：ADD / UPDATE / DELETE / NOOP 判定。判定失败全部丢弃（不回退直写），UPDATE 失效不覆盖、DELETE 软删。模型客户端由 background_review 运行时注入（`set_model_client`）。 |
| `core_memory.py` | 项目级 core memory（`CoreMemoryStore`）：持久化 JSON + 启动注入（`render()`）+ 旧会话文件聚合迁移（`migrate_legacy`）。条目为 dict 结构（实际落盘仅 `{fact, scope}` 两键）。 |
| `background_review.py`（在 `agent/native/`） | 每轮流结束后 daemon 线程回顾对话，产出 `ReviewResult`（user_preferences + facts），经门面 `on_turn_completed` 走固化管线；无门面时回退旧直写路径。 |
| `dual_memory.py` | **旧 DualMemory（会话级）**：对话历史唯一历史源（`_turns`），仍由 `self.memory` 使用。新语义事实存储与其分离、并存。 |
| `task_experience.py` / `experience_tree.py` | 任务经验树（TASK_EXPERIENCE 上下文段），与本目录其余组件并列注入。 |
| `session_manager.py` / `global_search.py` / `skill_generator.py` / `memory.md` | 既有会话管理与辅助组件，未受 P0 改动影响。 |

## 数据流

### 写入路径（回顾 → 固化 → 存储）

```
NativeFloodAgent.stream 流结束
  └─ spawn_background_review(memory_services=_get_mem_services_for_review())
       └─ Reviewer.review_session()  → ReviewResult(user_preferences, facts)
            └─ apply_review_result(..., memory_services)
                 └─ memory_services.on_turn_completed(result)
                      └─ consolidation.consolidate(candidates)
                           ├─ ADD    → store.add()            （新条目）
                           ├─ UPDATE → store.mark_superseded + add（旧条目失效，保留回溯）
                           ├─ DELETE → store.mark_deleted     （软删除）
                           └─ NOOP/判定失败 → 丢弃（不回退直写）
```

另有工具直写路径：`CoreMemoryAppend` 工具 → 门面 `.core` → `CoreMemoryStore.append()`（项目级常驻记忆）。

### 读取路径（门面 → recall/core → 注入）

```
NativeFloodAgent.stream 每次 LLM 调用前
  └─ get_memory_services()
       ├─ _ms.core.render()          → core memory 常驻块（ContextPriority 15）
       ├─ _build_experience_context() → 经验上下文（ContextPriority 25）
       ├─ _ms.recall.build_block(user_input) → 语义事实召回块（ContextPriority 30）
       └─ memory.get_chat_history_for_system_prompt() → 精简对话历史
  以上按序组装为 memory_messages 注入 system 上下文。
```

## JSON 存储文件

| 文件 | 位置 | 格式 |
| --- | --- | --- |
| `semantic_memory.json` | `data/memory/semantic_memory.json` | `{"version": 1, "entries": [{id, content, type, terms, valid_at, invalid_at, superseded_by, deleted_at, ...}]}` |
| `core_memory.json` | `data/memory/core_memory.json` | `{"version": 2, "facts": {category: [{fact, scope}]}}` |
| 旧 `long_term_memory.json` | `floodmind/memory/long_term_memory.json` | 旧 DualMemory 长期记忆（**冻结只读**）；门面初始化时 `import_legacy()` 一次性幂等导入语义存储 |

默认路径由 `semantic_store._DEFAULT_STORE_FILE` / `core_memory._DEFAULT_STORE_FILE` 定义（优先 `_RUNTIME_ROOT/data/memory/`，回退 `~/.floodmind/`）；测试通过 monkeypatch 这两个模块级常量重定向到临时目录。

## 与旧 DualMemory 的关系

- **DualMemory 不动**：仍是会话对话历史的唯一历史源（暂停 = abort，已完成轮原子落 memory）。
- **旧长期记忆（long_term_memory）冻结**：门面构造时 `import_legacy()` 幂等导入一次到 `semantic_memory.json`，此后旧文件只读。
- **新语义事实只经固化管线写入**：任何"直写长期记忆"的旧路径均被四类操作管线取代；判定失败全丢弃，保证语义事实的唯一入口与失效语义完整。
- **core memory 从会话级升为项目级**：`CoreMemoryAppend`/`CoreMemoryRead` 工具已由会话 JSON 文件重定向到项目级 `CoreMemoryStore`；会话级 `_core_memory_path` 等辅助函数暂时保留（其他调用方可能引用），P1 清理。

## 配置（settings）

- `memory_recall.*`：enabled / top_k / min_score / decay_scale_days / max_chars
- `memory_consolidation.*`：enabled / conflict_candidate_top_k
- `core_memory.*`：enabled / max_chars / migrate_legacy
