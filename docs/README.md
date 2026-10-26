# docs — 文档索引

项目文档中心。当前实现以源码与知识图谱为准；历史计划与调研材料不作为现行契约。

## 目录

| 路径 | 内容 |
| --- | --- |
| `DEVELOPER_GUIDE.md` | 二次开发指南：SDK API、事件、工具、Skill、记忆、MCP |
| `SDK_MAINTENANCE_RECOMMENDATIONS.md` | SDK 维护建议 |
| `agent-memory-survey.md` | 记忆专题调研 |
| `adr/` | 已接受的架构决策记录 |
| `agent-harness-blueprint/` | 厂商中立的 Agent Harness 参考架构与差距分析 |
| `knowledge-graph/` | 代码结构事实网、Mermaid 与可交互 HTML |
| `architecture/` | 架构总览与评估（该目录整体被 `.gitignore` 忽略，仅历史文件已入库） |
| `benchmarks/` | 真实模型 Harness 评测记录 |
| `superpowers/` | 历史实施计划与设计规格（非现行契约） |

## 约定

- 结构调整后重新生成知识图谱并提交 `knowledge-graph/graph/` 产物。
- `docs/key.txt` 与各类提示词/模型原始输出可能含敏感信息，不得提交。
- 新增架构文档前先确认是否会被 `.gitignore` 忽略。
