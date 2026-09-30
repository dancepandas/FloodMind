# FloodMind 记忆增强 P0 设计文档

- 日期：2026-09-30
- 状态：已确认（用户已拍板 4 项决策）
- 调研依据：`docs/agent-memory-survey.md`
- 范围：P0 三项增强（LTM 主动注入 / 四类操作管线 / core memory 持久化），P1/P2 只预留接口

## 1. 背景与目标

FloodMind 记忆栈骨架已齐（episodic=Journal、semantic=LTM、procedural=经验树、reflection=BackgroundReview），但存在四个机制缺陷：LTM 从不注入上下文（`set_long_term_memory()` 无调用方）、写入只追加无演化语义、core memory 会话级隔离、无向量检索。本设计实施调研报告中的 P0 三项，全部以独立服务落地，不在 DualMemory/ContextRuntime 既有类内部打补丁。

## 2. 已拍板决策

| # | 问题 | 决策 |
|---|---|---|
| 1 | 旧 `long_term_memory.json` 条目 | **一次性导入** SemanticMemoryStore（补 `entry_id` / `status=active`） |
| 2 | LLM 判 DELETE 生效方式 | **直接生效**，软删除（`status=deleted`）可回滚 |
| 3 | consolidation 判定模型 | **复用 BackgroundReviewer 的 model_client**，配置项预留独立覆盖 |
| 4 | core memory 注入优先级 | **新增 `ContextPriority.CORE_MEMORY = 15`** |
| 5 | recall 范围 | **严格限定 LTM 语义事实**；经验树重排留 P1 |

## 3. 整体架构

三个新服务 + 薄门面，全部位于 `floodmind/memory/` 与装配层：

```python
# floodmind/memory/services.py —— 门面：仅装配与生命周期，无业务逻辑
class MemoryServices:
    def __init__(self, settings, model_client=None): ...
    recall: MemoryRecallService              # 读路径：三因子打分注入
    consolidation: MemoryConsolidationService  # 写路径：四类操作管线
    core: CoreMemoryStore                    # durable working memory
    def on_turn_completed(self, ctx: TurnContext) -> None   # 挂 BackgroundReview 挂点
    def on_run_completed(self, run: RunSummary) -> None     # P1 预留，本次空实现
    def on_checkpoint(self, cp: CheckpointRef) -> None      # P2 预留，本次空实现
```

新增 durable 存储为独立文件，不污染 Journal projection。Agent 初始化时构造门面一次，注入装配层与 BackgroundReview 挂点。门面只做依赖注入和钩子转发。

## 4. 数据模型与存储

### 4.1 SemanticMemoryStore（新）

- 文件：`PROJECT_ROOT/data/memory/semantic_memory.json`
- 沿用 tmp+fsync+os.replace 原子替换 + 进程锁（与 LongTermMemory 同款模式，在新 store 内独立实现）

```python
class LTMEntryV2(BaseModel):
    entry_id: str                                  # mem_<uuid8>
    content: str
    category: str                                  # preference/decision/rule/note/...
    importance: float = 0.5
    valid_at: datetime
    invalid_at: datetime | None = None
    superseded_by: str | None = None               # 指向新条目 entry_id
    status: Literal["active", "superseded", "deleted"] = "active"
    access_count: int = 0
```

- **UPDATE = Graphiti 式失效**：新条目正常 ADD（`valid_at=now`），旧条目 `invalid_at=now; superseded_by=新id; status=superseded`。永不覆盖内容，支持时点回溯。
- **DELETE = 软删除**（`status=deleted`，检索默认过滤，可回滚）。
- **旧 LTM 迁移**：门面初始化时若新文件不存在，读取 `long_term_memory.json` 全量导入（补 `entry_id`、`status="active"`、`valid_at=条目 timestamp`）。导入后旧文件冻结只读（既有 `MemoryAdd` 等工具重定向到新 store）。

### 4.2 旧 store 处置

`DualMemory.long_term_memory` 冻结为只读兼容层，P1 再清理。`tests/test_memory_dual.py` 零改动。

## 5. MemoryRecallService（P0-1）

- **注入点**：每轮装配 system prompt 时由装配层调用一次（补上 `set_long_term_memory()` 的调用方）。LTM 百级条目 + 正则打分毫秒级，每轮重算不缓存；P1 换 embedding 后再加 TTL 缓存。
- **打分**：`score = relevance × recency × importance`
  - `relevance = |query_terms ∩ entry_terms| / |query_terms|`（复用现有 `LongTermMemory.search` 的 term 逻辑，抽为纯函数）
  - `recency = exp(-Δt / S)`，S=30 天（MemoryBank 艾宾浩斯公式）
  - `importance` = 条目字段原值（access_count 归一化留 P1）
- **预算**：注入内容整体作为一个 ContextBlock（复用 priority 30），走 ContextRuntime 现有 budget 机制，不新造预算系统；block 生成叠加 `max_chars=1500` 硬上限。
- **注入格式**：
  ```
  [长期记忆] 与当前请求可能相关的历史事实（已按相关度排序）：
  - (preference) 用户要求输出包含置信区间
  - (decision) 敖江方案采用新安江三水源模型
  ```
- **与 ExperienceTree（priority 25）关系**：经验树是程序性记忆（怎么做），LTM 是语义事实（是什么），两 block 并存、25 在前。recall 不重排经验树。

```python
class MemoryRecallService:
    def __init__(self, store: SemanticMemoryStore, *, top_k=5, min_score=0.05,
                 decay_scale_days=30, max_chars=1500): ...
    def build_block(self, user_input: str, now: datetime) -> str: ...
    @staticmethod
    def score(entry: LTMEntryV2, query_terms: set, now: datetime,
              decay_scale_days: float) -> float: ...
```

## 6. MemoryConsolidationService（P0-2）

- **候选来源**：BackgroundReviewer 抽取 prompt 增加 `facts` 字段（本轮事实性陈述：参数取值、方案选择、决策结论），连同既有 `user_preferences` 作为候选。**Reviewer 只抽取不写入**——产出 `ReviewResult` 后交给本服务判定，替换现在 `apply_review_result` 直接 `add_long_term_memory` 的追加逻辑（经验树/skill 建议部分不变）。
- **冲突检测（两阶段）**：
  1. relevance 打分对每个候选在现有 active 条目中取 top-3 冲突候选（LLM 前过滤，复用打分函数）；
  2. 单次 LLM 批量判定：输入「候选 + 各自冲突候选」，输出每条 `{action: ADD|UPDATE|DELETE|NOOP, target_id?, reason}`。
- **失败回退**：LLM 判定失败 / JSON 解析失败 / 非法 action → **本轮全部候选丢弃不写入，下轮重试**；绝不 fallback 成无条件 ADD。
- **模型**：复用 BackgroundReviewer 的 model_client；settings 预留独立模型配置位（本次不实现切换逻辑）。

```python
class MemoryConsolidationService:
    def __init__(self, store: SemanticMemoryStore, model_client, *,
                 conflict_candidate_top_k: int = 3): ...
    def consolidate(self, result: ReviewResult) -> ConsolidationOutcome: ...
```

## 7. CoreMemoryStore（P0-3）

- **存储**：`PROJECT_ROOT/data/memory/core_memory.json`（项目级，跟随 `_RUNTIME_ROOT` 重定向模式）。
- **迁移**：启动时若项目级文件不存在，扫描 `data/sessions/*/core_memory.json` 只读聚合导入（按 (category, fact) 去重），之后只写新路径。
- **scope**：本次只做 project 层；schema 预留 `scope: "user"|"project"|"session"`，接口默认 `scope="project"`。
- **注入**：启动时装配独立 ContextBlock，新增 `ContextPriority.CORE_MEMORY = 15`（PROJECT_RULES 10 之后、SKILL_CATALOG 20 之前）；预算 `max_chars=800`。
- **工具接线**：`CoreMemoryAppend/Read` 改指向 CoreMemoryStore（project scope），build_agent_tool 注册模式不变。

```python
class CoreMemoryStore:
    def load(self) -> dict: ...
    def append(self, category: str, fact: str, *, scope="project") -> bool: ...
    def render(self, *, max_chars=800) -> str: ...
```

## 8. 钩子接线

| 钩子 | 挂点 | 本次 |
|---|---|---|
| 每轮后异步 | 现有 `spawn_background_review` 挂点（流结束分支，daemon 线程）：reviewer 产出 ReviewResult 后追加 `consolidation.consolidate(result)` | 实现 |
| run 正常结束 | SSE 正常完成分支（与 GeneratorExit/abort 路径区分） | 只定义接口 + 空实现（P1 填充） |
| Checkpoint 后 | 不接线 | 门面留 `on_checkpoint()` 签名（P2 预留） |

## 9. 配置

settings 新增三段（默认值保守，沿用 `_cfg` 读取模式）：

```json
"memory_recall":        { "enabled": true, "top_k": 5, "min_score": 0.05, "decay_scale_days": 30, "max_chars": 1500 },
"memory_consolidation": { "enabled": true, "conflict_candidate_top_k": 3 },
"core_memory":          { "enabled": true, "max_chars": 800, "migrate_legacy": true }
```

## 10. 测试策略

- `tests/test_memory_recall.py`：打分纯函数确定性（注入 `now`，验证 exp 衰减单调性、三因子乘积、top-k 截断、空命中返回 ""）；超长输入截断。
- `tests/test_memory_consolidation.py`：mock LLM 返回固定 JSON，覆盖 ADD / UPDATE（验证 valid_at/invalid_at/superseded_by 失效链闭环）/ DELETE（软删 + 检索过滤）/ NOOP / 解析失败回退（断言零写入）。
- `tests/test_core_memory_store.py`：legacy 迁移聚合去重、原子写、scope 字段预留。
- `tests/test_memory_dual.py` 零改动（旧 store 冻结只读）。

## 11. 落地顺序

```
T0 contracts: LTMEntryV2 + SemanticMemoryStore 读写（含 legacy 导入）
 ├─ T1 MemoryRecallService + 装配接线        （纯读，与 T2 可并行）
 ├─ T2 CoreMemoryStore + 启动注入 + 工具重定向 （与 T1 可并行）
 └─ T3 MemoryConsolidationService + BackgroundReview 改接线（依赖 T0）
T4 门面 MemoryServices + on_run_completed 空钩子接线
```

每项全量 `pytest -q` 绿后单独提交。

## 12. dsh 迁移边界

落地**前**需查 dsh 确认三个 seam：`ctx.systemPrompt`（system prompt 装配注册表）、`ctx.storage`/`ctx.sessionPersistence`（durable 存储落点）、`session-projection` 文档（确认记忆注入块不算 projection 污染）。

纯 FloodMind 上层、不受迁移影响：打分函数、四类操作判定、条目模型与失效语义、legacy 迁移逻辑——dsh 换底后原样保留，只换门面接线。

## 13. 明确不做

- 向量检索（P1）、会话结束沉淀评审（P1）、经验树演化与重排（P2）、时序知识图谱（外部服务）
- 参数化记忆、重型 KG + PPR、每轮主动自编辑 core memory（Letta 全量版）
