# FloodAgent 项目指南

## 项目概述
FloodAgent（洪水预报智能体）是一个基于 LangChain 的多 Agent 洪水预报系统。
采用 Dispatcher + Execution Specialist 双层架构：
- **调度 Agent**：负责意图分析、任务规划、分发、结果汇总
- **执行单元**：负责单步任务的落地执行（运行脚本、生成文件等）

## 技术栈
- 语言：Python 3.10+
- LLM：Qwen（通义千问）via DashScope OpenAI 兼容 API
- 框架：LangChain（OpenAI Functions Agent）
- 前端：React 19 + TypeScript + Vite + TailwindCSS
- 向量库：ChromaDB（RAG）
- 部署：Docker（PyTorch + CUDA 基础镜像）

## 目录结构
```
agent/          FloodAgent 核心（调度器、执行单元、Agent Loop）
tools/          工具定义（base_tools.py 为主）
skills/         技能包（每个子目录一个 skill，含 SKILL.md + scripts/）
config/         配置管理（settings.py 从环境变量加载）
models/         LLM 服务封装（qwen_llm_service.py）
memory/         记忆系统（DualMemory：短期对话 + 长期记忆）
rag/            RAG 检索（Embedding + VectorStore + Retriever）
web_server.py   Flask Web 服务（REST API + NDJSON 流式推送）
source/react/   React 前端
data/           运行时数据（sessions、vector_store、tool_error_memory）
```

## Agent 行为约束
- 调度 Agent 不得亲自执行脚本、写文件、构造 JSON 等具体任务
- 执行单元不得重新规划、不得扩展上下游任务
- 必须先调用 `create_plan` 创建执行计划，再分发任务
- 每次只分发给执行单元一个核心动作
- 严禁把超长 JSON 直接塞进工具参数
- 涉及 10 条以上数据时必须整理为 Excel 文件输出
- 最终输出不得包含系统完整路径或会话内部信息

## Skill 开发规范
每个 skill 是 `skills/` 下的一个子目录，必须包含：
- `SKILL.md`：YAML frontmatter（name, description）+ 使用说明
- `scripts/`：可执行 Python 脚本

新增 skill 时：
1. 在 `skills/` 下创建子目录
2. 编写 `SKILL.md`
3. 将脚本放入 `scripts/`
4. skill 会由 `skills/__init__.py` 自动发现并注册

## 安全边界
- `write_text_file` 只允许写入 `data/sessions/` 和项目根目录下的文件
- `exec_bash` 禁止执行 `rm -rf`、`del /s`、`format`、`rmdir /s` 等破坏性命令
- `exec_bash` 禁止访问 `/etc/`、`C:\Windows\`、`C:\Program Files\` 等系统目录
- 工具输出超过 8000 字符时自动截断，完整结果保存至文件
- 工具连续 3 次相同调用失败后触发重试保护

## 代码风格
- Python 代码遵循 PEP 8
- 不添加非必要注释
- 使用 logging 模块记录日志，不用 print

## 常见陷阱
- Qwen 模型的 tool_call 参数有时会以 JSON 字符串形式传入，需要 `_parse_json_if_needed` 兼容
- Chronos 模型首次加载较慢（~30s），需要预热
- Excel sheet 名称最长 31 字符，stationCode 过长时会被截断
- matplotlib 在无头环境必须设置 `MPLBACKEND=Agg`
