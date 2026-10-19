# 11. 业界方案矩阵

> 所属：Agent Harness 建设蓝图 / 第三部分 对照与采用
> 版本：v1.0-draft（2026-10-18）
> 读法：本表按“它替代 harness 的哪一层”组织，而不是按厂商热度排名。所有产品的能力都以其官方文档为准；本文只做结构性归纳。

## 1. 分层对照表

| 方案 | 主层 | 是否自带持久化 | 是否自带沙箱 | 是否自带权限 | 备注 |
|---|---|---|---|---|---|
| Claude Agent SDK | L1/L3/L4 | 会话续接 | 依赖宿主 | 六步判定 | 循环、工具、子代理、权限完整 |
| Claude Managed Agents | L5/L6 平台 | 是（session 日志） | 是（容器） | 是 | brain/hands 分离 |
| OpenAI Agents SDK | L1/L4 | Sessions | 无（需自建） | Guardrails | handoff、tracing |
| OpenAI Codex | L1/L5 | 会话 | 是（三档） | 审批策略 | 终端编码代理 |
| Google ADK | L1/L4 | SessionService | 依赖部署 | 有限 | Runner + 事件模型 |
| Gemini CLI | L0/L5 | 检查点（shadow git） | 多后端 | 确认机制 | 已被新 CLI 替代的报道需核实 |
| AWS AgentCore | L5/L6 平台 | Memory | Runtime 隔离 | Identity | 七件套 |
| Azure Foundry | L5/L6 平台 | Threads | 托管 | Entra Agent ID | 企业集成强 |
| Vertex Agent Engine | L5/L6 平台 | Sessions + Memory Bank | 托管 | IAM | ADK 集成 |
| LangGraph | L1/L6 | Checkpointer | 无 | interrupt | 图编排 + 持久化 |
| OpenHands | L1/L5 | 事件流 | 可选 | 有限 | CodeAct、SDK 化 |
| SWE-agent | L1/L4 | 有限 | 依赖运行环境 | 有限 | ACI 设计典范 |
| Aider | L1/L4 | git | 无 | 宽松 | 无工具调用，文本编辑 |
| Cline | L0/L1 | 检查点 | 依赖 IDE | 三层审批 | IDE 内人机协同 |
| Goose | L1/L4 | 会话 | 有限 | SmartApprove | MCP 原生 |
| OpenCode | L0/L1 | 会话 | 有限 | 使用点权限 | 多 provider、子代理 |
| Temporal | L6 | 是 | 无 | 无 | 工作流引擎 |
| Restate | L6 | 是 | 无 | 无 | 低延迟、虚拟对象 |
| Inngest | L6 | 是 | 无 | 无 | 事件驱动、serverless |
| DBOS | L6 | 是（Postgres） | 无 | 无 | 与业务数据同事务 |
| Mem0 | L4 记忆 | 外部存储 | 无 | 无 | 抽取-更新 |
| Zep/Graphiti | L4 记忆 | 图数据库 | 无 | 无 | 双时间知识图 |
| Letta | L1/L4 记忆 | 是 | 有限 | 无 | 分层分页、自编辑 |
| E2B | L5 | 无 | microVM | 无 | 代码执行沙箱 |
| Modal | L5 | 无 | gVisor | 无 | 弹性执行 |

## 2. 官方 Harness 与 SDK

### 2.1 Claude Agent SDK

关键能力 [A: SDK 文档]：

- 循环内建 `max_turns`、`max_budget_usd`；
- 工具权限六步判定：hooks → deny → ask → mode → allow → canUseTool；
- 权限模式：default、acceptEdits、dontAsk、bypassPermissions、plan、auto；
- 子代理拥有独立上下文，只返回最终结果；
- 会话支持 continue、resume、fork；
- 只读工具可并行，修改型串行。

值得借鉴的设计：

1. deny 规则在 bypass 模式下仍生效；
2. 审批规则可以按工具 + 参数模式表达；
3. plan 模式下写操作强制走回调；
4. 子代理返回蒸馏结果，而不是完整轨迹。

局限：

- 沙箱由宿主提供，SDK 不内建强隔离；
- 与 Claude 模型绑定较深，跨供应商需要适配。

### 2.2 Claude Managed Agents

把 Agent 拆成三个可替换接口 [C: Anthropic, 2026-04]：

| 接口 | 职责 |
|---|---|
| session | 追加事件日志，`getEvents()` 可按位置切片读取 |
| harness | 调模型、路由工具，无状态，可 `wake(sessionId)` 重启 |
| sandbox | 执行环境，`execute(name, input) → string` |

工程结论：

- harness 与沙箱分离后，容器是“牛”不是“宠物”；
- 凭证放在沙箱外的 vault，通过代理调用；
- session 不是上下文窗口，而是可查询的外部上下文对象；
- p50 TTFT 下降约 60%，p95 下降超过 90%（官方数据）。

### 2.3 OpenAI Agents SDK

关键能力 [A: SDK 文档]：

- 循环有三种结果：最终输出、handoff、工具调用；
- `max_turns` 与具名错误（`MaxTurnsExceeded`、`ModelRefusalError`、`InvalidFinalOutput`）；
- 输入/输出/工具三类 guardrail，可并行或阻塞；
- Sessions 管理跨运行历史；
- 内建 tracing；
- 可用 Dapr、Temporal、Restate、DBOS 做持久化执行；
- `RunState` 支持人机协同暂停与恢复。

值得借鉴：

1. 把“循环为何结束”建模为封闭枚举，而不是异常；
2. guardrail 区分“评审输入”“校验输出”“拦截工具”，各有触发时机；
3. 持久化执行不自己造，接入成熟引擎。

### 2.4 Google ADK

核心原语 [A: ADK 文档]：

- Agent（LlmAgent 与 workflow agents：Sequential / Parallel / Loop）；
- Session 与 State（事件追加、状态前缀 user:/app:/temp:）；
- MemoryService（`add_session_to_memory`、`search_memory`，需显式写入）；
- Artifacts；
- Runner（编排执行、产出 Event 流）；
- Callbacks。

关键设计：

- 会话状态与长期记忆明确分离；
- 记忆不是自动的，必须显式提交完成的会话；
- 工作流代理是确定性控制器，与 LLM 驱动的动态路由区分。

### 2.5 云平台

| 平台 | 核心服务 | 值得注意 |
|---|---|---|
| AWS AgentCore | Runtime、Memory、Gateway、Identity、Code Interpreter、Browser、Observability | Gateway 把 API 转成 MCP 工具；Runtime 支持长时任务 |
| Azure AI Foundry | Agent Service、Threads、Memory（预览）、Entra Agent ID | 企业身份与连接器强 |
| Vertex Agent Engine | Sessions、Memory Bank、托管运行时 | 与 ADK/LangGraph/CrewAI 集成；Memory Bank 异步抽取与冲突消解 |

平台采用的核心问题是：状态与身份是否可导出。不可导出即锁定。

## 3. 开源编码 Harness

### 3.1 架构差异

| 项目 | 循环 | 工具接口 | 上下文策略 | 权限/审批 | 特色 |
|---|---|---|---|---|---|
| OpenHands | 事件流 + Action | CodeAct（代码即动作） | 事件史 + 压缩 | 可选沙箱 | 事件流为权威；V1 强调可选隔离 |
| SWE-agent | 单循环 | ACI（bash + 编辑器） | 观察裁剪 | 有限 | ACI 设计；无显式 think 工具 |
| Aider | 每轮一次模型调用 | 无工具调用，文本编辑 | repo map | 宽松 | git 为撤销系统；architect/editor 分工 |
| Cline | IDE 内循环 | 工具 + diff | 检查点 | 三层审批 | 人机协同最强 |
| Goose | 状态机迁移中 | 全部工具走 MCP | 会话 | SmartApprove（LLM 评审） | MCP 原生、recipes |
| OpenCode | 双运行时 | 每请求重建工具面 | 会话 | 使用点权限 | 子代理、LSP 反馈 |

来源：各项目文档与社区架构分析，证据等级 B/C；具体版本行为需以仓库为准。

### 3.2 可借鉴的模式

1. **事件流作为权威**（OpenHands）：所有动作与观察按序记录，支持重放。
2. **ACI 优先**（SWE-agent）：工具的输出格式与截断策略是性能关键。
3. **代码即动作**（CodeAct）：用代码表达多步操作，减少工具数量。
4. **仓库地图**（Aider）：用结构化索引替代全量文件读取。
5. **审批分层**（Cline）：读、写、执行分别有不同默认。
6. **LLM 评审审批**（Goose）：低风险动作由模型判断，高风险仍给人。
7. **工具面按请求重建**（OpenCode）：只暴露当前需要的工具。

### 3.3 共同趋势

- MCP 成为工具接入的事实标准；
- 子代理用于隔离上下文，而不是追求“多智能体”；
- 检查点与回滚从 git 扩展到会话状态；
- 权限从“全允许/全询问”走向按动作分级。

## 4. 框架

| 框架 | 抽象 | 强项 | 弱项 |
|---|---|---|---|
| LangGraph | 状态图 + 节点/边 | 持久化、HITL、可视化 | 图复杂度上升快 |
| AutoGen / AG2 | 多代理对话 | 协作模式丰富 | 状态与恢复较弱 |
| CrewAI | 角色 + 任务 | 上手快 | 控制粒度粗 |
| Pydantic AI | 类型安全 Agent | 结构化输出、可测试 | 生态较新 |
| smolagents | 轻量代码代理 | 简单、可读 | 生产能力有限 |
| Semantic Kernel | 企业集成 | .NET/Java 生态 | 概念层多 |
| Google ADK | Agent + Runner + 服务 | 事件模型清晰 | 与 GCP 绑定较深 |

### 4.1 LangGraph 的持久化模型

- Checkpointer + thread_id；
- 三种 durability：exit / async / sync；
- `interrupt()` 暂停，`Command(resume=...)` 恢复；
- 恢复时节点从头重跑，副作用必须幂等；
- checkpoint_id 为 ULID，便于按序取最新。

这是“图编排 + 事件持久化”的代表实现。适合流程可显式建模的场景；不适合高度动态、图结构频繁变化的场景。

## 5. 持久化执行引擎

| 引擎 | 模型 | 上限/特点 | 适用 |
|---|---|---|---|
| Temporal | 工作流 + 活动，事件历史重放 | 事件数与体积上限，需 Continue-As-New | 成熟企业、强一致 |
| Restate | 虚拟对象 + durable promises | 低延迟，部署版本固定 | 低延迟服务、HITL |
| Inngest | 事件触发的函数 + step | step 数与状态体积上限 | Serverless、事件驱动 |
| DBOS | Postgres 中的工作流 | 上限取决于数据库 | 与业务数据同事务 |

共同约束：**确定性重放**。工作流代码不能直接使用时间、随机数、网络；这些必须封装在 step/activity 中。

对 Agent 的含义：

- LLM 调用必须作为可记录结果的 step；
- 审批、MCP 过滤、流处理是常见的“意外非确定性”来源；
- 代码变更与在途工作流的兼容需要版本策略；
- 日志会随任务增长，长会话需要归档或快照。

本文判断：如果已有自建 journal + reducer，不必引入完整工作流引擎；可借鉴其“确定性边界”和“版本兼容”做法。

## 6. 记忆方案

| 方案 | 模型 | 写入 | 读取 | 适用 |
|---|---|---|---|---|
| Mem0 | 抽取 + ADD/UPDATE/DELETE/NOOP | 自动 | 向量 | 快速个人化 |
| Zep/Graphiti | 双时间知识图 | 事件摄入 | 向量 + BM25 + 图 | 时间与关系 |
| Letta/MemGPT | 分层分页 + 自编辑 | 模型主动 | 工具调用 | 长期自主代理 |
| LangMem | 命名空间 + 语义/情景/程序 | 可配置 | 检索 | LangChain 生态 |
| Cognee | 知识图 + 向量 | 管道 | 图检索 | 文档密集 |
| A-MEM | 记忆网络 + 动态链接 | 自动 | 关联检索 | 研究型 |

选型不看宣传分数，看：

1. 是否支持时间有效性；
2. 是否支持冲突消解；
3. 是否可解释（来源、置信度）；
4. 是否可删除（合规）；
5. 是否可替换存储。

## 7. 协议与观测

已在 `09` 与 `10` 展开，这里只给结论：

- MCP 已成为工具接入标准，但信任与权限仍需自建；
- A2A 适合跨组织，不适合进程内编排；
- AG-UI 适合多后端前端统一；
- OTel GenAI 是观测语义的事实标准，但仍为 development 状态；
- Langfuse（MIT）适合自托管，Phoenix 为 Elastic 许可，Braintrust 为闭源评测优先。

## 8. 沙箱方案

| 方案 | 隔离 | 启动 | 备注 |
|---|---|---|---|
| 本地进程 | 弱 | 极快 | 仅可信代码 |
| Docker | 中 | 秒级 | 运维熟悉 |
| gVisor | 中强 | 中 | 系统调用兼容性需测试 |
| Firecracker | 强 | 百毫秒级，可预热 | E2B 等采用 |
| 托管沙箱 | 强 | 网络往返 | E2B、Modal、Daytona |

选型维度：隔离强度、启动延迟、网络策略、文件持久化、成本、区域。

## 9. 按场景的选型建议

| 场景 | 建议组合 |
|---|---|
| 本地编码助手 | 单进程 harness + 审批 + git 检查点 |
| 团队编码平台 | 脑手分离 + 容器沙箱 + 内部金标集 |
| 企业知识助手 | MCP 工具 + 语义记忆 + 审计 + 平台身份 |
| 长时研究代理 | 编排-工人 + 子代理隔离 + 外部状态文件 |
| 高合规业务 | 自建状态 + 强沙箱 + 凭证代理 + 全量审计 |
| 快速原型 | 框架 + 托管平台，接受锁定，后续再抽象 |

## 10. 时效声明

本矩阵中的产品能力变化很快，尤其是 2025-2026 年间的平台与协议。使用前必须：

1. 核对官方文档的当前版本；
2. 核对许可证；
3. 核对配额与区域；
4. 对关键结论做一次小规模验证。

本文只保证结构与判断，不保证具体功能清单在阅读时仍然完整。
