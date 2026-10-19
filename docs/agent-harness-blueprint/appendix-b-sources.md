# 附录 B. 来源清单

> 快照日期：2026-10-18
> 说明：等级定义见 `README.md` 第 3 节。标注“检索摘要”的条目表示本文参考了搜索结果对该来源的归纳，未逐字核验原文；引用其数字时已在正文标注不确定性。

## 1. Anthropic

| 来源 | 日期 | 等级 | 本文使用点 |
|---|---|---|---|
| Building Effective Agents — https://www.anthropic.com/engineering/building-effective-agents | 2024-12 | C | workflow 与 agent 的区分、五种模式、简单优先 |
| Effective Context Engineering for AI Agents — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents | 2025-09 | C | 上下文腐烂、注意力预算、压缩保留项、工具结果清理 |
| Writing Tools for Agents — https://www.anthropic.com/engineering/writing-tools-for-agents | 2025-09 | C | ACI 六原则、合并工具、返回高信号、评测闭环 |
| Harness Design for Long-Running Apps — https://www.anthropic.com/engineering/harness-design-long-running-apps | 2026 | C | 上下文焦虑、重置、planner/generator/evaluator、文件交接 |
| Scaling Managed Agents — https://www.anthropic.com/engineering/managed-agents | 2026-04-08 | C | session/harness/sandbox 三接口、brain/hands 分离、凭证 vault、TTFT |
| How We Built Our Multi-Agent Research System — https://www.anthropic.com/engineering/multi-agent-research-system | 2025-06 | C | 15x token、90% 提升、80% 方差、子代理蒸馏 |
| Claude Agent SDK 文档 — https://platform.claude.com/docs/en/agent-sdk/overview | 持续更新 | A | 循环、max_turns、max_budget_usd、工具并发 |
| Agent SDK Permissions — https://platform.claude.com/docs/en/agent-sdk/permissions | 持续更新 | A | 六步判定、权限模式、deny 优先级、审批回调 |
| Agent SDK Sessions / Subagents / Hooks | 持续更新 | A | 会话续接、子代理上下文隔离、钩子 |
| Context Editing — https://platform.claude.com/docs/en/build-with-claude/context-editing | 持续更新 | A | clear_tool_uses、clear_thinking、缓存友好 |
| Compaction — https://platform.claude.com/docs/en/build-with-claude/compaction | 持续更新 | A | 服务端压缩、触发与返回块 |
| Memory Tool — https://platform.claude.com/docs/en/build-with-claude/tool-use/memory-tool | 持续更新 | A | 文件式记忆、与清理联动 |

## 2. OpenAI

| 来源 | 日期 | 等级 | 本文使用点 |
|---|---|---|---|
| Agents SDK — Running Agents — https://openai.github.io/openai-agents-python/running_agents/ | 持续更新 | A | 循环、max_turns、具名错误、持久化执行集成 |
| Agents SDK — Guardrails — https://openai.github.io/openai-agents-python/guardrails/ | 持续更新 | A | 输入/输出/工具 guardrail、并行与阻塞 |
| Agents SDK — Sessions — https://openai.github.io/openai-agents-python/sessions/ | 持续更新 | A | 跨运行历史 |
| Agents SDK — Human-in-the-loop — https://openai.github.io/openai-agents-python/human_in_the_loop/ | 持续更新 | A | RunState 暂停与恢复 |
| Agents SDK — Tracing | 持续更新 | A | 内建追踪 |
| A Practical Guide to Building Agents（PDF）— https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf | 2025 | C | 单代理优先、manager vs handoffs、guardrail 类型 |
| Codex Sandbox 文档 — https://github.com/openai/codex/blob/main/docs/sandbox.md | 持续更新 | A | read-only / workspace-write / danger-full-access、网络默认关闭 |
| Codex Security — https://developers.openai.com/codex/security | 持续更新 | A | 审批策略与沙箱组合 |

## 3. Google

| 来源 | 日期 | 等级 | 本文使用点 |
|---|---|---|---|
| ADK 文档首页 — https://google.github.io/adk-docs/ | 持续更新 | A | Agent、Runner、Session、Memory、Artifact、Event |
| ADK Memory Service — https://google.github.io/adk-docs/sessions/memory/ | 持续更新 | A | add_session_to_memory、search_memory、记忆非自动 |
| ADK Runtime / Events | 持续更新 | A | 事件流、workflow agents |
| Gemini CLI 文档 — https://github.com/google-gemini/gemini-cli/tree/main/docs | 持续更新 | A | 客户端/核心分层、沙箱后端、shadow git 检查点、扩展 |
| Vertex AI Memory Bank — https://cloud.google.com/blog/products/ai-machine-learning/vertex-ai-memory-bank-in-public-preview | 2025-07 | C | 异步抽取、冲突消解、按用户作用域 |
| Vertex AI Agent Engine — https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview | 持续更新 | A | 托管运行时、Sessions |

## 4. 云平台与协议

| 来源 | 日期 | 等级 | 本文使用点 |
|---|---|---|---|
| AWS Bedrock AgentCore — https://aws.amazon.com/bedrock/agentcore/ | 2025-07 预览 | A | Runtime/Memory/Gateway/Identity/Code Interpreter/Browser/Observability |
| AWS What's New（AgentCore 预览）— https://aws.amazon.com/about-aws/whats-new/2025/07/amazon-bedrock-agentcore-preview/ | 2025-07-16 | A | 发布时间与区域 |
| Azure AI Foundry Agent Service — https://learn.microsoft.com/en-us/azure/foundry/agents/overview | 持续更新 | A | Threads、Memory、托管代理 |
| MCP Specification 2025-06-18 — https://modelcontextprotocol.io/specification/2025-06-18 | 2025-06-18 | A | tools/resources/prompts、sampling/roots/elicitation、授权 |
| MCP Changelog 2025-11-25 — https://modelcontextprotocol.io/specification/2025-11-25/changelog | 2025-11-25 | A | sampling 工具调用、URL elicitation |
| A2A Specification — https://github.com/a2aproject/A2A/blob/main/docs/specification.md | 持续更新 | A | Agent Card、Task、Artifact、SSE、推送通知 |
| AG-UI 文档 — https://docs.ag-ui.com/ | 持续更新 | A | 事件族、状态快照/增量、中断 |
| Agent Protocol — https://github.com/AI-Engineer-Foundation/agent-protocol | 2024-2025 | B | 通用任务 API（生态活跃度需自行核实） |
| OpenTelemetry GenAI SemConv — https://opentelemetry.io/docs/specs/semconv/gen-ai/ | development | A | invoke_agent、execute_tool、gen_ai.provider.name 等 |
| OpenTelemetry GenAI 仓库 — https://github.com/open-telemetry/semantic-conventions-genai | 持续更新 | A | agent span 定义与稳定状态 |

## 5. 开源 Harness

| 来源 | 日期 | 等级 | 本文使用点 |
|---|---|---|---|
| OpenHands — https://github.com/All-Hands-AI/OpenHands | 持续更新 | B | 事件流、CodeAct、V1 设计原则 |
| OpenHands SDK 设计 — https://docs.openhands.dev/sdk/arch/design | 持续更新 | A/B | 可选隔离、单一可变状态、层分离 |
| SWE-agent — https://github.com/SWE-agent/SWE-agent | 持续更新 | B | ACI、bash + 编辑器、观察裁剪 |
| SWE-agent 论文 — arXiv:2405.15793 | 2024-05 | B | ACI 设计与基准结果 |
| Aider — https://aider.chat/docs/ | 持续更新 | A | repo map、编辑格式、git 撤销 |
| Cline — https://github.com/cline/cline | 持续更新 | B | 三层审批、检查点、Plan/Act |
| Goose — https://github.com/block/goose | 持续更新 | B | MCP 原生、recipes、SmartApprove |
| OpenCode — https://github.com/sst/opencode | 持续更新 | B | 多 provider、子代理、按请求工具面 |

## 6. 框架与持久化

| 来源 | 日期 | 等级 | 本文使用点 |
|---|---|---|---|
| LangGraph Durable Execution — https://docs.langchain.com/oss/python/langgraph/durable-execution | 持续更新 | A | exit/async/sync、确定性重放 |
| LangGraph Checkpointers — https://docs.langchain.com/oss/python/langgraph/checkpointers | 持续更新 | A | thread_id、ULID、存储后端 |
| LangGraph Human-in-the-Loop — https://docs.langchain.com/oss/python/langgraph/human-in-the-loop | 持续更新 | A | interrupt、Command(resume)、重跑与幂等 |
| AutoGen — https://github.com/microsoft/autogen | 持续更新 | B | 多代理对话 |
| CrewAI — https://docs.crewai.com/ | 持续更新 | A | 角色与任务 |
| Pydantic AI — https://ai.pydantic.dev/ | 持续更新 | A | 类型安全、结构化输出 |
| smolagents — https://github.com/huggingface/smolagents | 持续更新 | B | 代码代理 |
| Semantic Kernel — https://learn.microsoft.com/semantic-kernel/ | 持续更新 | A | 企业集成 |
| Temporal — https://docs.temporal.io/ | 持续更新 | A | 工作流/活动、事件历史、版本化 |
| Restate — https://docs.restate.dev/ | 持续更新 | A | 虚拟对象、durable promises、AI SDK 集成 |
| Inngest — https://www.inngest.com/docs | 持续更新 | A | step 模型、step.ai.infer |
| DBOS — https://docs.dbos.dev/ | 持续更新 | A | Postgres 工作流、同事务 |

## 7. 记忆

| 来源 | 日期 | 等级 | 本文使用点 |
|---|---|---|---|
| MemGPT — arXiv:2310.08560 | 2023-10 | B | 分层分页、自编辑记忆 |
| Mem0 — arXiv:2504.19413 | 2025-04 | B | 抽取-更新、ADD/UPDATE/DELETE/NOOP（数字为厂商自报） |
| Zep / Graphiti — arXiv:2501.13956 | 2025-01 | B | 双时间知识图、混合检索（数字为厂商自报） |
| LoCoMo — arXiv:2402.17753 | 2024-02 | B | 长对话记忆基准与局限 |
| LongMemEval — arXiv:2410.10813 | 2024-10 | B | 长期记忆评测 |
| A-MEM — arXiv:2502.12110 | 2025-02 | B | 动态链接记忆网络 |
| Generative Agents — arXiv:2304.03442 | 2023-04 | B | 记忆流与反思 |
| Letta 文档 — https://docs.letta.com/ | 持续更新 | A | 分层记忆与工具 |
| Cognee — https://github.com/topoteretes/cognee | 持续更新 | B | 图 + 向量管道 |
| LangMem — https://langchain-ai.github.io/langmem/ | 持续更新 | A | 命名空间与记忆类型 |

## 8. 评测与安全

| 来源 | 日期 | 等级 | 本文使用点 |
|---|---|---|---|
| SWE-bench — arXiv:2310.06770 | 2023-10 | B | 真实仓库任务与测试判定 |
| τ-bench — arXiv:2406.12045 | 2024-06 | B | 多轮工具、策略遵循、pass^k |
| GAIA — arXiv:2311.12983 | 2023-11 | B | 通用助理多步任务 |
| OSWorld — arXiv:2404.07972 | 2024-04 | B | 桌面 GUI 任务 |
| Terminal-Bench — https://github.com/laude-institute/terminal-bench | 持续更新 | B | 终端端到端任务 |
| OWASP Top 10 for LLM Applications 2025 — https://genai.owasp.org/llm-top-10/ | 2025 | A | LLM01/LLM06/LLM08/LLM10 等 |
| Firecracker — https://firecracker-microvm.github.io/ | 持续更新 | A | microVM 隔离 |
| gVisor — https://gvisor.dev/ | 持续更新 | A | 用户态内核 |
| E2B — https://e2b.dev/docs | 持续更新 | A | Firecracker 沙箱与 SDK |
| Modal — https://modal.com/docs | 持续更新 | A | 弹性执行与 gVisor |

## 9. 观测工具

| 来源 | 日期 | 等级 | 本文使用点 |
|---|---|---|---|
| Langfuse — https://langfuse.com/docs | 持续更新 | A | 追踪、评测、提示管理、MIT 核心 |
| Arize Phoenix — https://docs.arize.com/phoenix | 持续更新 | A | OTel 原生、Elastic License 2.0 |
| Braintrust — https://www.braintrust.dev/docs | 持续更新 | A | 数据集、实验、CI 门禁 |
| Comet Opik — https://github.com/comet-ml/opik | 持续更新 | A | Apache 2.0 评测与追踪 |

## 10. 引用规范

1. 正文中的 `[A]`、`[B]`、`[C]`、`[D]` 对应本附录的等级。
2. 引用具体数字时，若来源为厂商自报，正文必须标注“厂商自报”或“官方数据”。
3. 若同一结论只有单一二手来源，正文使用“据报道”并降级为 D。
4. 产品功能清单不保证时效；采用前必须核对官方文档当前版本。
5. 论文引用不保证复现；基准分数仅用于方向判断。

## 11. 本次调研的访问方式说明

- Anthropic 工程博客与平台文档通过本地代理直接抓取正文后阅读。
- OpenAI Agents SDK 文档、Google ADK 文档、MCP 规范、OWASP、LangGraph 文档等通过直接抓取或搜索结果摘要交叉核对。
- OpenHands、Cline、Goose、OpenCode、Aider 的部分架构描述来自社区分析文章，正文已标注为 B/C 级。
- 少数平台对比（Azure、Vertex）依赖二手对比报道，已在正文标注 D 级。
- 所有 URL 在 2026-10-18 可访问；后续可能变更或迁移。
