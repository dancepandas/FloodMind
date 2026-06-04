# FloodMind

**基于大语言模型的智能洪水预报系统**

FloodMind 是一个面向洪水预报业务的智能体协同处理系统。基于自研 Native Agent Runtime，利用大语言模型的规划与推理能力，结合水文模型、时序预测、RAG 知识检索和文档自动生成，实现从自然语言需求到预报产出的全流程自动化。

---

## 核心功能

- **Native Agent Runtime** — 自研 Agent 执行引擎，支持工具调用循环、流式输出、规划与委派、DAG 工作流
- **Skill 系统** — 自动发现式技能注册，13 个内置 Skill（水文模型、数据科学、文档生成、创意设计）
- **MCP 协议接入** — 内置知识库管理和数字人 MCP Server，作为 MCP 客户端连接外部工具
- **任务经验树** — 树状层级经验组织，渐进压缩摘要、去重合并、热度衰减、经验→Skill 自动生成
- **双层记忆系统** — 短期对话记忆 + 长期记忆 + LLM 压缩 + 心跳归纳
- **DAG 工作流** — 拓扑排序的分层并行任务执行，支持步骤依赖声明
- **水文模型集成** — 敖江水文模型、TSLM / Chronos 时序预测
- **RAG 知识库** — 基于 ChromaDB + BGE Embedding 的向量检索
- **定时任务调度** — 每日重复 / 一次性定时任务，后台自动执行并记录产物
- **文档自动生成** — 支持 Excel、Word、PDF、PPT 等格式
- **多界面支持** — React Web 前端 + 终端 TUI + 纯文本 CLI
- **多模型支持** — 配置即用：任意 OpenAI 兼容接口均可接入（DashScope、DeepSeek、OpenAI、Ollama、Groq 等），通过 settings.json 自由定义 provider 和模型

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| Agent Runtime | 自研 Native Agent（异步流式 + Queue 驱动） |
| Web 框架 | Flask + SSE/NDJSON 流式响应 |
| 前端 | React 19 + TypeScript + TailwindCSS 4 + Vite 7 |
| LLM | OpenAI 兼容接口（DashScope、DeepSeek、OpenAI 等） |
| 向量库 | ChromaDB |
| Embedding | sentence-transformers（BAAI/bge-base-zh-v1.5） |
| 时序预测 | Chronos 2、TSLM、PyTorch |
| 容器化 | Docker + NVIDIA GPU 支持 |

---

## 快速开始

### 环境要求

- Python 3.10+
- NVIDIA GPU（可选，用于时序预测加速）

### 1. 安装

```bash
# 从 Git 仓库安装
pip install git+https://github.com/your-org/FloodMind.git

# 或本地开发模式
git clone https://github.com/your-org/FloodMind.git
cd FloodMind
pip install -e .

# 安装可选依赖
pip install "floodmind[web,doc]"    # Web服务 + 文档处理
pip install "floodmind[all]"        # 全部依赖（含GPU）
```

### 2. 配置 API 密钥

首次启动 FloodMind 会自动创建配置文件 `~/.floodmind/settings.json`。

编辑该文件，在对应 provider 的 `options.apiKey` 中填入密钥：

```json
{
  "provider": {
    "dashscope": {
      "name": "DashScope (Alibaba)",
      "options": {
        "apiKey": "sk-你的密钥",
        "baseURL": "https://dashscope.aliyuncs.com/compatible-mode/v1"
      },
      "models": {
        "deepseek-v4-flash": { "name": "DeepSeek V4 Flash", "maxTokens": 65536 }
      }
    }
  },
  "model": {
    "provider": "dashscope",
    "model": "deepseek-v4-flash"
  }
}
```

### 3. 启动

```bash
floodmind              # 交互菜单
floodmind tui          # 终端 TUI（推荐）
floodmind web          # Web 服务 + 浏览器
floodmind chat         # 纯文本命令行对话
floodmind --help       # 查看更多命令
```

访问 `http://localhost:13014` 进入 Web 界面。

### 4. Docker 部署

```bash
docker build -t floodmind .
docker run -p 13014:13014 -v ~/.floodmind:/root/.floodmind floodmind
```

---

## CLI 参考

| 命令 | 说明 |
|------|------|
| `floodmind` | 交互菜单，选择 TUI / Web / Chat |
| `floodmind tui` | 终端 TUI（后台自动启动 Web 服务） |
| `floodmind web` | Web 服务 + 自动打开浏览器 |
| `floodmind serve` | Web 服务（无浏览器，适合部署） |
| `floodmind chat` | 纯文本终端对话 |
| `floodmind run "任务"` | 单次任务执行，适合脚本/调度调用 |
| `floodmind init` | 初始化项目配置 |
| `floodmind config show` | 查看当前配置 |
| `floodmind config set <key> <val>` | 设置配置项 |
| `floodmind skill create <name>` | 从模板创建新 Skill |
| `floodmind skill list` | 列出已安装的 Skill |
| `floodmind providers` | 列出可用 AI Provider |
| `floodmind session list` | 列出所有会话 |
| `floodmind session fork/export/compact` | 会话管理 |

```bash
# 部署模式（不自动打开浏览器）
floodmind serve --port 8000

# 脚本调用（单次任务）
floodmind run "分析水库水位数据" -m deepseek-v4-flash
```

---

## 二次开发

FloodMind 提供了多种集成方式，可嵌入到第三方系统或构建自定义界面。

### Python API

```python
from floodmind.agent import create_flood_agent
from floodmind.memory import DualMemory
from floodmind.models import get_qwen_llm_service

llm = get_qwen_llm_service(
    api_key="你的密钥",
    model_name="deepseek-v4-flash",
)
memory = DualMemory(max_short_term=20, context_window=32768)
agent = create_flood_agent(llm_service=llm, memory=memory)

# 流式对话
for chunk in agent.stream("分析水位数据"):
    print(chunk["content"], end="")

# 单次执行
result = agent.run("生成水文报告")
```

### HTTP API

启动 `floodmind serve` 后，通过 REST API 调用：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat` | POST | 流式聊天（SSE/NDJSON） |
| `/api/init` | POST | 初始化会话 Agent |
| `/api/sessions` | GET | 列出所有会话 |
| `/api/upload` | POST | 上传文件 |
| `/api/models` | GET | 模型列表 |
| `/api/health` | GET | 健康检查 |

### 自定义 Skill

无需修改核心代码即可扩展能力：

```bash
floodmind skill create my_skill   # 创建 Skill 模板
# 编辑 skills/my_skill/SKILL.md 填写触发条件与执行逻辑
```

### 模型扩展

在 `~/.floodmind/settings.json` 中添加任意 OpenAI 兼容接口：

```json
{
  "provider": {
    "custom": {
      "name": "自定义平台",
      "options": {
        "apiKey": "密钥",
        "baseURL": "https://api.your-provider.com/v1"
      },
      "models": {
        "my-model": { "name": "我的模型", "maxTokens": 8192 }
      }
    }
  }
}
```

---

## 配置说明

配置文件位于 `~/.floodmind/settings.json`，模板参考 `floodmind/config/settings_template.json`。

### OpenAI 兼容接口

FloodMind 支持任意兼容 OpenAI `/v1/chat/completions` 的 API。以下是常见提供商的配置示例：

```json
// DashScope（阿里云百炼）
{ "provider": { "dashscope": { "options": { "baseURL": "https://dashscope.aliyuncs.com/compatible-mode/v1" } } } }

// DeepSeek 官方
{ "provider": { "deepseek": { "options": { "baseURL": "https://api.deepseek.com/v1" } } } }

// OpenAI
{ "provider": { "openai": { "options": { "baseURL": "https://api.openai.com/v1" } } } }

// Ollama 本地模型
{ "provider": { "ollama": { "options": { "baseURL": "http://localhost:11434/v1" } } } }

// 其他 OpenAI 兼容平台（硅基流动 / Groq / 讯飞星辰 等）
{ "provider": { "custom": { "options": { "baseURL": "https://api.your-provider.com/v1" } } } }
```

> 不需要 `apiKey` 的平台（如 Ollama 本地）可省略该字段。

### Provider 配置

```json
{
  "provider": {
    "<provider-id>": {
      "name": "显示名称",
      "options": {
        "apiKey": "密钥",
        "baseURL": "API 地址"
      },
      "models": {
        "<model-id>": {
          "name": "模型显示名",
          "description": "描述",
          "maxTokens": 65536,
          "temperature": 0.3,
          "supportsReasoning": true,
          "supportsVision": false
        }
      }
    }
  }
}
```

### 主要配置项

| 键 | 说明 | 默认值 |
|------|------|--------|
| `model.provider` | 默认 provider | dashscope |
| `model.model` | 默认模型 | deepseek-v4-flash |
| `model.enableReasoning` | 启用推理模式 | false |
| `model.maxTokens` | 最大 token 数 | 65536 |
| `agent.maxHistory` | 最大历史轮数 | 20 |
| `agent.contextWindow` | 上下文窗口 | 32768 |
| `rag.enabled` | 启用 RAG | true |
| `rag.embeddingModel` | Embedding 模型 | BAAI/bge-base-zh-v1.5 |
| `rag.topK` | 检索返回条数 | 10 |
| `mcpServers` | MCP Server 配置 | [] |

---


## 项目结构

```
FloodMind/
├── floodmind/
│   ├── agent/                  # Agent 编排核心
│   │   ├── native/             #   Native Agent Runtime
│   │   │   ├── executor.py     #     工具调用循环
│   │   │   ├── planner.py      #     任务规划器
│   │   │   ├── types.py        #     数据类型 + DAG 拓扑
│   │   │   └── native_flood_agent.py  # Agent 主体
│   │   ├── runtime/            #   Runtime 服务
│   │   ├── mcp_client.py       #   MCP 客户端
│   │   └── scheduled_task_runtime.py  # 定时任务
│   ├── config/                 # 全局配置
│   │   ├── settings.py         #   配置模型
│   │   ├── model_presets.py    #   模型预设
│   │   └── settings_template.json  # 初始模板
│   ├── memory/                 # 记忆与经验系统
│   │   ├── dual_memory.py      #   双层记忆
│   │   ├── experience_tree.py  #   经验树索引
│   │   └── task_experience.py  #   任务经验
│   ├── MCP/                    # MCP Server
│   │   ├── knowledge_mcp/      #   知识库管理（15工具）
│   │   └── metahuman_mcp/      #   数字人服务（4工具）
│   ├── models/                 # LLM 服务
│   ├── rag/                    # RAG 知识检索
│   ├── skills/                 # Skill 技能包（13个）
│   ├── tools/                  # Agent 工具层
│   ├── tui/                    # 终端 TUI
│   ├── server/                 # SSE 服务器
│   └── cli.py                  # CLI 入口
├── web/                        # React 前端
├── tests/                      # 测试
├── web_server.py               # Web 服务
├── scheduler.py                # 后台调度
├── main.py                     # CLI 交互
├── start.py                    # 统一启动
├── Dockerfile                  # Docker 构建
└── pyproject.toml              # 包配置
```

---

## API 概览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat` | POST | 流式聊天（SSE/NDJSON） |
| `/api/init` | POST | 初始化会话 Agent |
| `/api/sessions` | GET | 列出所有会话 |
| `/api/sessions/<id>` | GET / DELETE | 会话详情 / 删除 |
| `/api/session/config` | POST | 更新会话配置（模型切换） |
| `/api/upload` | POST | 上传文件 |
| `/api/files` | GET | 列会话文件 |
| `/api/files/<id>/download` | GET | 文件下载 |
| `/api/models` | GET | 模型列表 |
| `/api/scheduled-tasks` | GET / PATCH / DELETE | 定时任务管理 |
| `/api/memory/search` | POST | 搜索记忆 |
| `/api/permission/respond` | POST | 工具权限确认 |
| `/api/health` | GET | 健康检查 |

---

## 开发指南

```bash
# 前端开发
cd web && npm run dev      # Vite 开发服务器 (:5173)

# 运行测试
python -m pytest tests/ -v

# 前端构建
cd web && npm run build

# 新增 Skill（无需改代码）
mkdir skills/my-skill
echo -e '---\nname: my-skill\ndescription: ...\n---\n...' > skills/my-skill/SKILL.md
```
