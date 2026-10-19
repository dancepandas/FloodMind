# 附录 A. 术语速查

> 用途：写作与评审时的快速对照。完整定义见 `00-terms-and-scope.md`。

## 1. 系统与架构

| 中文 | 英文 | 一句话定义 |
|---|---|---|
| 模型 | model | 产生文本或工具调用请求的推理服务，无执行权 |
| 宿主 | host | 调用 Agent SDK 的程序，负责界面与生命周期 |
| Harness | harness | 模型之外的工程层：循环、上下文、工具、权限、持久化、观测 |
| Scaffold | scaffold | 为特定任务或基准搭建的外围结构，通常不可复用 |
| Runtime | runtime | harness 中负责执行与状态的部分 |
| 框架 | framework | 提供抽象与组件的库 |
| 平台 | platform | 托管 harness 的云服务 |
| 协议 | protocol | 跨进程或跨组织的接口约定 |
| 控制面 | control plane | 决策、组装、路由，无状态或弱状态 |
| 数据面 | data plane | 执行、存储、记忆，有状态 |
| 保障面 | assurance plane | 追踪、指标、评测、审计，旁路 |
| 脑手分离 | brain/hands split | harness 与沙箱解耦，沙箱作为工具被调用 |
| 元 harness | meta-harness | 对具体 harness 保持中立的接口集合 |

## 2. 执行与控制

| 中文 | 英文 | 一句话定义 |
|---|---|---|
| 轮次 | turn | 一次用户输入到最终响应 |
| 步 | step | 一次模型调用及其工具调用 |
| 尝试 | attempt | 针对同一步的一次模型请求 |
| 调用 | call | 一次工具调用，必须有终态 |
| 循环 | agent loop | 组装、推理、执行、提交的重复过程 |
| 停止条件 | stopping condition | 正常完成、上限、预算、拒绝、取消、错误 |
| 规划 | planning | 生成与修订任务分解 |
| 编排 | orchestration | 决定谁在何时做什么 |
| 交接 | handoff | 移交控制权与上下文 |
| 人机协同 | HITL | 关键动作前暂停等待人类 |
| 无进展检测 | no-progress detection | 连续多步无新信息则停止 |
| 取消 | cancellation | 沿调用树传播的终止信号 |

## 3. 状态与持久化

| 中文 | 英文 | 一句话定义 |
|---|---|---|
| 会话 | session | 跨多个轮次的持久化容器 |
| 运行 | run | 一次从开始到终态的执行 |
| 线程 | thread | 可独立调度的执行分支 |
| 事件 | event | 已发生事实的不可变记录 |
| 日志 | journal / event log | 事件的权威存储 |
| 归约器 | reducer | 从事件确定性推导状态的纯函数 |
| 投影 | projection | 从状态或事件派生的只读视图 |
| 检查点 | checkpoint | 带版本与游标的状态快照 |
| 恢复 | resume | 从持久状态继续，不重复副作用 |
| 重放 | replay | 重新消费事件重建状态 |
| 重试 | retry | 对失败步骤再次请求 |
| 幂等 | idempotency | 多次执行与一次执行效果相同 |
| 幂等键 | idempotency key | 用于去重的稳定标识 |
| 隔离栅栏 | fencing | 用 epoch/token 阻止旧持有者写入 |
| 租约 | lease | 带 TTL 的入场锁 |
| 对账 | reconciliation | 恢复时判定外部动作是否已发生 |
| 不确定 | indeterminate | 无法判定是否执行的工具终态 |
| 补偿 | compensation | 用反向操作抵消已完成动作 |

## 4. 上下文

| 中文 | 英文 | 一句话定义 |
|---|---|---|
| 上下文 | context | 本次请求实际携带的 token 集合 |
| 上下文窗口 | context window | 模型单次可接受的最大 token |
| 上下文腐烂 | context rot | 随长度增加而出现的召回退化 |
| 注意力预算 | attention budget | 可用于高信号内容的有限注意力 |
| 压缩 | compaction | 用摘要替换较早上下文 |
| 修剪 | trimming | 直接删除部分上下文 |
| 卸载 | offload | 移到外部存储，留引用 |
| 摘要 | summarization | 生成新文本概括旧内容 |
| 渐进披露 | progressive disclosure | 先给索引，按需展开 |
| 即时检索 | just-in-time retrieval | 在需要时拉取 |
| 结构化笔记 | structured note-taking | 用文件维护任务状态 |
| 交接包 | handoff package | 上下文重置前的结构化摘要 |
| 提示缓存 | prompt caching | 复用稳定前缀的计算 |
| 原子组 | atomic group | 不可拆分的消息单元（如调用+结果） |

## 5. 工具与技能

| 中文 | 英文 | 一句话定义 |
|---|---|---|
| 工具 | tool | 模型可调用的确定性能力 |
| 工具契约 | tool contract | 输入、输出、副作用、权限、生命周期的完整约定 |
| ACI | Agent-Computer Interface | 面向模型的动作粒度与反馈设计 |
| 命名空间 | namespace | 工具名称的分组前缀 |
| 副作用分类 | side-effect class | 只读、可逆、不可逆 |
| 输出上限 | output limit | 单次结果允许进入上下文的最大量 |
| 技能 | skill | 可复用的任务知识与流程说明 |
| 技能草案 | skill draft | 由经验生成、待审核的技能文件 |
| MCP | Model Context Protocol | Agent 与工具/资源/提示的连接协议 |
| 工具投毒 | tool poisoning | 通过工具描述或结果注入恶意指令 |
| 工具评测 | tool evaluation | 用真实任务衡量工具可用性 |

## 6. 记忆

| 中文 | 英文 | 一句话定义 |
|---|---|---|
| 工作记忆 | working memory | 当前任务内的临时状态 |
| 情景记忆 | episodic memory | 具体经历与事件 |
| 语义记忆 | semantic memory | 去情境化的事实与偏好 |
| 程序记忆 | procedural memory | 如何做某类任务 |
| 固化 | consolidation | 把经历抽取、去重、合并为长期条目 |
| 双时间 | bi-temporal | 同时记录事件时间与写入时间 |
| 时间有效性 | temporal validity | 事实在某个区间内成立 |
| 衰减 | decay | 随时间的可用性下降 |
| 遗忘 | forgetting | 归档或删除不再需要的内容 |
| 记忆污染 | memory poisoning | 错误事实进入长期存储并被反复使用 |
| 作用域 | scope | user / project / session / global |

## 7. 安全

| 中文 | 英文 | 一句话定义 |
|---|---|---|
| 提示注入 | prompt injection | 用输入改变模型意图 |
| 间接注入 | indirect injection | 通过工具结果、网页、文件注入 |
| 过度代理 | excessive agency | 功能、权限或自主性超出需要 |
| 权限闸门 | permission gate | 执行前的 allow/ask/deny 判定 |
| 审批 | approval | 人类对具体动作的授权 |
| 动作指纹 | action fingerprint | 工具+参数+资源+会话的稳定摘要 |
| 能力 | capability | 最小权限集合 |
| 凭证代理 | credential broker | 不暴露密钥的代调用组件 |
| 沙箱 | sandbox | 限制影响范围的执行环境 |
| 微虚拟机 | microVM | 独立内核的轻量虚拟机 |
| 用户态内核 | user-space kernel | 拦截系统调用的隔离层 |
| 审计 | audit | 可追溯、不可篡改的动作记录 |
| 数据外带 | exfiltration | 通过合法通道传出敏感数据 |

## 8. 评测与观测

| 中文 | 英文 | 一句话定义 |
|---|---|---|
| 追踪 | trace | 一次执行的因果树 |
| span | span | 追踪中的一个操作单元 |
| 指标 | metric | 可聚合的数值 |
| 评测 | evaluation | 用数据集与判定器衡量能力 |
| 判定器 | grader / verifier | 判断结果是否正确的组件 |
| 轨迹评测 | trajectory evaluation | 评判执行路径而非仅最终答案 |
| 金标集 | golden set | 内部维护的回归用例集 |
| 留出集 | held-out set | 不参与调优的评测集 |
| 灰度 | canary | 小流量验证变更 |
| 门禁 | gate | 发布前必须满足的条件 |
| pass^k | pass^k | 重复 k 次全部成功的概率 |
| 奖励黑客 | reward hacking | 通过绕过任务而非完成任务获得高分 |

## 9. 协议

| 中文 | 英文 | 一句话定义 |
|---|---|---|
| 代理卡 | Agent Card | A2A 中描述代理能力的元数据 |
| 任务 | Task | A2A 中的协作单元 |
| 产物 | Artifact | 任务产生的可校验输出 |
| 推送通知 | push notification | 服务端向客户端回调事件 |
| 事件流 | SSE | 服务器发送事件的单向流 |
| 状态快照 | state snapshot | AG-UI 中的完整状态 |
| 状态增量 | state delta | AG-UI 中的增量更新 |
| 能力协商 | capability negotiation | 连接建立时声明支持的功能 |

## 10. 常见缩写

| 缩写 | 全称 | 含义 |
|---|---|---|
| ACI | Agent-Computer Interface | 面向模型的工具界面 |
| ADR | Architecture Decision Record | 架构决策记录 |
| HITL | Human-in-the-Loop | 人机协同 |
| MCP | Model Context Protocol | 模型上下文协议 |
| A2A | Agent-to-Agent | 代理间协作协议 |
| AG-UI | Agent-User Interaction | 代理与前端交互协议 |
| OTel | OpenTelemetry | 可观测性标准 |
| RAG | Retrieval-Augmented Generation | 检索增强生成 |
| RRF | Reciprocal Rank Fusion | 倒数排名融合 |
| MMR | Maximal Marginal Relevance | 最大边际相关性 |
