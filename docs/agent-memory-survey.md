# Agent 记忆管理方案调研与 FloodMind 适配建议

> 调研日期：2026-09-30。范围：2023–2026 学术界代表性方案 + 工业界主流记忆框架/产品。
> 目的：评估出色的记忆方案能否嵌入 FloodMind SDK 级 Agent Runtime。

---

## 一、FloodMind 现状盘点

当前记忆栈（`floodmind/memory/` + `floodmind/tools/memory_tools.py`）：

| 组件 | 角色（类型学） | 机制 | 注入方式 |
|---|---|---|---|
| Journal → `DualMemory` | 情景记忆 episodic | 全量事件日志派生对话轮次；上下文 >85% 时 LLM 压缩早期轮次、保留最近 6 轮原文 | 主动注入（history block） |
| `LongTermMemory` | 语义记忆 semantic | 扁平 JSON 条目 `{content, category, importance, timestamp, access_count}`，**纯正则关键词打分** | **从不注入**——`ContextRuntime.set_long_term_memory()` 定义了优先级 30 的 block 但全库无调用方，只能工具拉取 |
| `core_memory.json`（CoreMemoryAppend/Read 工具） | 工作记忆 working | Letta 式 core memory 雏形，agent 可自编辑 | **会话级隔离**（按 session 落盘，跨会话失效），也不注入 |
| `ExperienceTree` + `SkillGenerator` | 程序性记忆 procedural | 领域→子领域→任务类型→案例四层树；叶子含坑点/方案/代码片段/成败反馈/热度；渐进摘要（SummaryNode）；密封后自动生成 SKILL.md | 主动注入（版本缓存的整树摘要，priority 25） |
| `BackgroundReview` | 反思 consolidation | 每轮 SSE 结束后轻量 LLM 回顾：偏好→LTM、经验→树、skill 建议→审核队列 | — |
| `GlobalSearch` | 跨会话 episodic 检索 | 跨会话关键词搜索 | 工具拉取 |

**骨架评价**：四种记忆类型 + 反思固化全覆盖，经验树的热度衰减/成败加权/密封生成技能已是工业界第一梯队做法。

**差距清单**：

1. 无向量检索——所有记忆检索均为正则关键词匹配，语义近义失配
2. 无事实演化语义——"改用模型 A" 后旧事实并存，LTM 只有精确去重，无 invalidate/supersede
3. Core memory 会话级、不注入——agent 看不到也改不了跨会话的持久自我
4. 无 user/project/session 分层 scope
5. LTM 与 core memory 只能工具拉取（pull），无相关性主动注入（push）

架构约束（来自 P5 计划，必须遵守）：**Journal 是唯一事实源，Projection 可丢弃可重建**；任何新记忆机制要么 journal-backed，要么是独立 durable 存储。

---

## 二、学术方案综述（2023–2026）

### 1. Generative Agents（Park et al., UIST 2023）
- **机制**：memory stream 全量追加；检索按 recency × relevance × importance 三因子打分取 top-k；重要性累计超阈值时 LLM 向上综合生成高阶洞察（reflection），回流 stream。
- **借鉴**：三因子打分可直接做成检索排序函数；「重要性阈值触发反思」映射为 Checkpoint 后的后台 consolidation pass。

### 2. MemGPT / Letta（Packer et al., 2023）
- **机制**：OS 分页类比——core memory（小而可编辑，永远在上下文）≈ RAM；archival ≈ 磁盘；recall ≈ 日志；context 溢出触发中断式换页。
- **借鉴**：**中断式换页与 Journal/Checkpoint 天然同构**——checkpoint 落盘 ≈ swap out，resume 按需唤起 ≈ swap in。不需要照搬 Letta，在现有断点重续上补「记忆唤起」语义即可。core memory 的「小而可原地编辑且常驻上下文」原则适合装配层。

### 3. A-MEM（Xu et al., NeurIPS 2025）
- **机制**：Zettelkasten 式动态记忆卡片——LLM 生成结构化 note（keywords/tags/context/embedding）；链接 = 向量召回 top-k + LLM 判真实语义关系；新 note 可触发旧 note 上下文重写，网络持续自修正。
- **借鉴**：**与 experience_tree 最接近**——给经验节点加 tags/context/typed links；「新经验触发旧经验摘要更新」可作为 Checkpoint 后的异步维护任务。

### 4. MemoryOS（Kang et al., EMNLP 2025 Oral）
- **机制**：双系统——显式记忆 MM（结构化画像+知识库，按需检索）与隐式记忆 AM（历史对话经持续预训练固化进参数），先查显式、缺失走隐式。
- **借鉴**：参数化路线对 SDK 不现实；借其「**先查显式、fallback 隐式**」的分层路由思想——经验树（显式）+ Journal 原始上下文（近似隐式）。

### 5. 其他
- **MemoryBank（AAAI 2024）**：艾宾浩斯衰减公式 `R = exp(-Δt/S)`，几行代码即可加入经验节点权重。
- **LongMem（NeurIPS 2023）**：模型层方案 SDK 不可直接借鉴，但其「冻结快照、旁路读取」与只读 Checkpoint 哲学一致。
- **HippoRAG / HippoRAG 2**：KG + Personalized PageRank 多跳检索；一般 SDK 场景属过度工程，留给外部服务。

### 横切面结论
- **分层而非单一存储**是 MemGPT 与 MemoryOS 的共同结论，与我们现状一致。
- 写入共识：「先全量追加 → 异步压缩 → 周期固化」；遗忘用时间衰减 + 引用频次优于硬删除；写入触发用规则（重要性阈值/轮次间隔/checkpoint 边界），不必每轮 LLM 判定。
- 检索：纯向量弱于「向量召回候选 + 结构化过滤/重排」混合方案。
- 自我演化（2025–2026 前沿）方向一致：**记忆系统在空闲时主动整理、合并、纠错**——挂到 Checkpoint/暂停钩子做后台 pass，不进在线路径。

---

## 三、工业界框架对比（2024–2026）

| 框架 | 存储模型 | 记忆类型 | 写入时机 | 自演化 | 嵌入难度 | 可借鉴点 |
|---|---|---|---|---|---|---|
| Mem0 | 向量(+图) | 语义事实 | 每轮后异步 | 中（ADD/UPDATE/DELETE/NOOP） | **低** | 四类操作抽取管线 |
| Zep/Graphiti | 时序知识图谱 | 事实+时序 | episode 增量 | 高（失效不改写） | 高 | **双时间戳 invalidate** |
| LangMem | 向量+文本 | 语义+经验prompt | 每轮/离线优化 | 高 | 中 | 经验编译成 prompt |
| Letta | 块+向量+全文 | 全类型 | agent 主动（含 heartbeat） | **最高（自编辑）** | 中 | core block 自编辑 |
| Claude Code | 分层 markdown | 指令/经验 | 会话结束/手动 | 低 | **极低** | 分层注入 + 索引文件 |
| OpenHands | 事件流+压缩 | 会话内 | 80% 阈值触发 | 低 | 低 | Condenser 可 replay |

### 关键机制拆解

**Mem0**：每轮后异步抽取候选记忆，LLM 判 ADD/UPDATE/DELETE/NOOP 四类操作后写入。单条事实粒度、延迟低、即插即用。局限：UPDATE 是覆盖式，事实演化无历史。→ 与我们「每轮落盘 Journal」天然对齐，抽成独立 `MemoryService` 挂每轮 hook 即可。

**Zep/Graphiti**：真时序知识图谱。矛盾检测命中时旧边被 **invalidate 而非删除**（`t_invalid` = 新边 `t_valid` + `expired_at`），支持时点查询。检索 = 语义 embedding + BM25 + 图遍历 + 社区摘要重排。→ 双时间戳失效模型一个 SQLite 表就能实现，**无需引入图库**；完整 KG 方案留给外部服务。

**LangMem**：prompt memory 用优化器从历史轨迹生成「经验 prompt」直接注入系统提示（对编码 agent 收益最大）；semantic memory 与 Mem0 雷同可跳过。

**Letta**：core memory 两个固定大小块永远在上下文，agent 经工具自编辑；DMR 事实召回 32%→92.5%。局限：token 开销恒定偏大、长任务易自我污染。

**Claude Code**：CLAUDE.md 三层（user/project/local）启动注入；auto-memory = MEMORY.md 索引 + 单文件单事实，**会话结束时**由模型判断值得沉淀的才写入。文件即记忆：零基础设施、可 git 审计、可人工编辑。

**写入时机结论**：每轮后异步（Mem0/Zep）适合事实层；会话结束（Claude Code）适合经验沉淀层；agent 主动（Letta）适合 working memory。三层各用各的触发时机，不要统一。

---

## 四、适配建议（按优先级）

### P0 —— 高价值、低成本、纯装配层

**1. 接通 LTM 主动注入（Generative Agents 三因子打分）**
`ContextRuntime.set_long_term_memory()` 已定义但无调用方。在每轮装配 system prompt 时，用当前用户输入对 LTM 条目做 recency × relevance × importance 打分取 top-k 注入。检索先用现有关键词打分 + 时间衰减（MemoryBank 公式），embedding 后置。改动集中在装配层，不动存储。

**2. Mem0 式四类操作管线，升级 BackgroundReview**
BackgroundReview 目前只做 ADD。让轻量 LLM 对候选记忆判 ADD / UPDATE / DELETE / NOOP；UPDATE 采用 Graphiti 式**失效而非覆盖**——条目加 `superseded_by` 字段保留旧版本，供经验树回溯。这与水利场景强相关（预报参数、方案版本随时间变）。

**3. Core memory 持久化 + 启动注入（Letta 原则）**
`core_memory.json` 从会话级升格为项目级 durable 存储，启动时装配进 system prompt（小而常驻、可工具自编辑）。改动 = 存储路径 + 装配接线，工具面已存在。

### P1 —— 中期

**4. 向量检索层**：给 LTM 条目 + 经验树叶子加 embedding 字段；hybrid = 树导航粗筛 + embedding 细排。注意内网环境，embedding 走本地模型或现有 provider 管线。

**5. 会话结束沉淀评审（Claude Code auto-memory 式）**：挂在 run 正常结束钩子（与暂停=abort 区分——只沉淀完整轮次），把值得长期保留的经验写成独立事实文件 + 索引进项目级 memory.md。可 git 审计、用户可手改。

**6. 三因子打分 + 衰减进经验树排序**：`recompute_importance()` 加入 MemoryBank 衰减项与 relevance 因子。

### P2 —— 后续增强

**7. A-MEM 式经验节点演化**：Checkpoint/暂停后的后台 pass——合并相似经验、新经验触发旧摘要更新、LLM 判 typed links。挂异步任务，不进在线路径。

**8. LangMem 式经验→prompt 编译**：高频命中分支离线蒸馏成热路径 prompt 注入。

**9. 时序知识图谱（Graphiti 全套）**：明确留给外部服务（如 RAG MCP 模式），不进 SDK 核心。

### 明确不做的
- 参数化/持续预训练记忆（MemoryOS-AM、LongMem）——需训练，SDK 不适用
- 重型 KG + PPR 多跳（HippoRAG）——一般场景过度工程
- 每轮主动自编辑 core memory（Letta 全量版）——token 开销与自我污染风险，用「会话结束评审」替代

---

## 五、与项目既有决策的对齐

- **dsh 迁移**：本报告的增强点全部位于 FloodMind SDK 的记忆服务层（`floodmind/memory/` + 装配层），与「SDK 底层迁 dsh」不冲突——迁移后这些服务以同样接口挂在 dsh seams 上。落地时应先查 dsh 是否已有对应能力（ctx/memory seam），避免自造（见 dsh-ctx-check-before-write）。
- **「不自建语义记忆」决策的边界**：该决策针对的是 FloodMind-as-coding-harness 自身的全局记忆（= 项目级 memory.md）。本报告讨论的是 **SDK runtime 为上层业务 Agent 提供的记忆子系统**（DualMemory/经验树），两者是不同层，不冲突。
- **模块化原则**：每项增强独立成服务（MemoryRecallService / MemoryConsolidationService / CoreMemoryStore），走标准接口，不允许在 DualMemory 上打补丁。

## 六、参考来源

学术：A-MEM (arXiv:2502.12110)、MemoryOS (arXiv:2506.06326)、HippoRAG (arXiv:2405.14831)、LongMem (arXiv:2306.07174)、MemOS (arXiv:2507.03724)、Reflective Memory Management (ACL 2025)、Rethinking Memory in LLM-based Agents (arXiv:2505.00675)、SAGE、Memoria (arXiv:2512.12686)
工业：Mem0 (arXiv:2504.19413)、Graphiti (arXiv:2501.13956, github.com/getzep/graphiti)、LangMem (github.com/langchain-ai/langmem)、Letta/MemGPT、OpenHands Condenser、Claude Code 记忆机制
