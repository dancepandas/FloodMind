# FloodMind

**基于大语言模型的智能洪水预报系统**

FloodMind 是一个面向洪水预报业务的智能体协同处理系统。基于自研 Native Agent Runtime，利用大语言模型的规划与推理能力，结合水文模型、时序预测、RAG 知识检索和文档自动生成，实现从自然语言需求到预报产出的全流程自动化。

<p align="center">
  <img src="figure/floodmind-icon.png" width="80" alt="FloodMind">
</p>

---

## 核心功能

- **Native Agent Runtime** — 自研 Agent 执行引擎，支持工具调用循环、流式输出、规划与委派、DAG 工作流
- **Skill 系统** — 自动发现式技能注册，13 个内置 Skill（水文模型、数据科学、文档生成、创意设计）
- **MCP 协议接入** — 标准 FastMCP Server（知识库检索 / 文档入库），作为 MCP 客户端通过 stdio/SSE 连接外部工具，支持运行时动态接入（LoadMcpServer）
- **任务经验树** — 树状层级经验组织，渐进压缩摘要、去重合并、热度衰减、经验→Skill 自动生成
- **双层记忆系统** — 短期对话记忆 + 长期记忆 + LLM 压缩 + 心跳归纳
- **DAG 工作流** — 拓扑排序的分层并行任务执行，支持步骤依赖声明
- **水文模型集成** — 敖江水文模型、TSLM / Chronos 时序预测
- **RAG 知识库** — 独立 FastAPI REST 服务（ChromaDB + BGE Embedding），通过 MCP 协议接入，可快速插拔更换
- **Plan 任务规划** — 多步骤任务自动创建执行计划，实时跟踪执行进度和状态
- **Token 用量统计** — 实时展示单条消息和会话级 prompt / completion / total tokens
- **定时任务调度** — 每日重复 / 一次性定时任务，后台自动执行并记录产物
- **文档自动生成** — 支持 Excel、Word、PDF、PPT 等格式
- **多界面支持** — React Web 前端 + 终端 TUI + 纯文本 CLI
- **多模型支持** — 配置即用：任意 OpenAI 兼容接口均可接入
- **DOOM LOOP 检测** — 连续相同工具+相同参数 3 次自动终止
- **自动重试** — LLM 调用失败（网络/503）指数退避重试
- **Plugin 系统** — Python 原生插件扩展，注册工具/hook/Agent 初始化
- **SyncEvent 溯源** — 事件持久化到 SQLite，支持断线回放
- **Cursor 分页** — 消息和事件游标分页 API
- **精简系统提示词** — 8 段核心指导，工具描述由 ToolRegistry 自动注入

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| Agent Runtime | 自研 Native Agent（流式 + Queue 驱动 + DOOM LOOP + 自动重试） |
| Web 框架 | Flask + NDJSON 流式响应 + SyncEvent 事件溯源 |
| 前端 | React 19 + TypeScript + TailwindCSS 4 + Vite 7 |
| LLM | OpenAI 兼容接口（DashScope、DeepSeek、OpenAI 等） |
| 存储 | SQLite（WAL，SyncEvent 事件回放日志） + JSON（会话历史 / 配置） |
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
| `floodmind tui` | 终端 TUI（直连 Agent，零延迟） |
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

### Python SDK（嵌入式 Agent）

将 Agent 嵌入到任意 Python 系统，自定义工具、提示词和前端：

```python
from floodmind import Agent, ModelClient, build_agent_tool

# 1. 创建 LLM 客户端（任意 OpenAI 兼容接口）
llm = ModelClient(
    api_key="sk-xxx",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    model_name="deepseek-v4-flash",
)

# 2. 将系统模块封装为工具
def query_station(station: str) -> str:
    """查询监测站实时数据"""
    return f"{station} 水位 32.5m, 流量 120m³/s"

def run_model(station: str, model_type: str = "xinanjiang") -> str:
    """运行水文预报模型"""
    return f"预报结果: 洪峰流量 350m³/s, 到达时间 +6h"

tools = [
    build_agent_tool(func=query_station, name="QueryStation", description="查询监测站实时数据"),
    build_agent_tool(func=run_model, name="RunModel", description="运行水文预报模型"),
]

# 3. 创建 Agent
agent = Agent(
    llm=llm,
    tools=tools,
    system_prompt="你是水文预报助手，帮用户查询监测数据并运行预报模型。",
)

# 4. 非流式调用 — 拿结果展示
result = agent.run("查一下霍口水库水位，然后跑一下新安江模型")
print(result)

# 5. 流式调用 — 推送给自建前端
for event in agent.stream("查一下霍口水库水位"):
    if event["type"] == "answer_delta":
        # 文本增量 → 前端渲染
        print(event["content"], end="", flush=True)
    elif event["type"] == "action_start":
        # 工具调用状态 → 前端展示
        print(f"\n[调用工具] {event['tool_name']}")
    elif event["type"] == "final_text":
        # 最终完整回答
        print(f"\n[完成] {event['content']}")
```

**事件类型**（`agent.stream()` 产出，`on_event` 回调同样收到）：

| 类别 | 事件 | 关键字段 |
|------|------|---------|
| 回答 | `answer_delta` / `final_text` | `content` |
| 思考 | `thought_delta` | `content`（启用 reasoning 时） |
| 工具 | `action_start` / `action_end` | `tool_name`, `status`, `content`, `call_id?`, `step_key?` |
| 计划 | `workflow_plan` / `workflow_step` | `title` / `step_key`, `status`, `subtasks?` |
| 生命周期 | `llm_step_start` / `llm_step_end` / `retry_attempt` | `iteration`, `finish_reason`, `tokens` |
| 上下文 | `context_compress_start` / `context_compress_done` | `content` |
| 产物 | `file_generated` / `image_generated` | `filename`, `download_url?`, `filepath?`, `image_url?`, `size?` |
| 系统 | `token_usage` / `heartbeat` / `error` / `llm_token_error` | token 用量 / 错误内容 |

**构造参数**：`llm`（必填）、`tools`、`system_prompt`、`memory`、`session_id`、`enable_search`、`enable_reasoning`、`on_event`（事件回调）、`permission_handler`（工具审批钩子）、`max_iterations`（默认 50）。

**结果属性**：`agent.last_usage`（本次 token 用量）、`agent.artifacts`（本次产物事件）、`agent.raw`（底层 `NativeFloodAgent`）。

```python
# 进阶：事件回调 + 权限钩子 + 迭代上限
def on_event(event):
    if event["type"] == "token_usage":
        print(f"累计 token: {event['total_tokens']}")

def approve(tool_name, tool_input):
    return tool_name != "DropTable"  # 拒绝危险工具

agent = Agent(
    llm=llm,
    tools=tools,
    on_event=on_event,           # 每个流事件自动推送，无需手动迭代
    permission_handler=approve,  # 工具调用前同步审批，返回 False 即拒绝
    max_iterations=20,           # Agent 循环上限
)
agent.run("查霍口水库水位")
print(agent.last_usage)   # {"prompt_tokens":..,"completion_tokens":..,"total_tokens":..}
print(agent.artifacts)    # 本次生成的文件/图片事件列表
```

> **产物说明**：`agent.artifacts` 收集工具执行过程中产出的 `file_generated`/`image_generated` 事件。嵌入式（bare）模式不启用文件系统自动监控，自定义工具需自行产出文件并在返回结果中声明，才能被识别为产物。

### Python API（完整模式）

使用 FloodMind 内置全套工具（文件读写、Web 搜索、记忆系统等）：

```python
from floodmind.agent.native.model_client import ModelClient
from floodmind.memory import DualMemory
from floodmind.agent.native.native_flood_agent import NativeFloodAgent

llm = ModelClient.from_settings()
memory = DualMemory(session_id="my-session", max_short_term=20, context_window=32768)
agent = NativeFloodAgent(llm_service=llm, memory=memory, session_id="my-session")

# 流式对话
for chunk in agent.stream("分析水位数据"):
    print(chunk.get("content", ""), end="")

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

### 自定义身份与提示词

FloodMind 的系统提示词采用分层可替换架构，支持从配置到代码级的多种定制方式。

#### 方式一：编辑 SOUL.md（推荐，无需改代码）

首次启动后自动在 `~/.floodmind/SOUL.md` 生成默认身份文件，直接编辑即可替换智能体的身份描述：

```markdown
你是 MyBot，一个专注于 XX 领域的智能助手。

## 角色职责
1. 分析用户需求并提供专业解答
2. 调用工具完成数据分析和报告生成

## 核心特质
- 专业严谨，注重数据准确性
- 主动思考，善于引导用户明确需求
```

#### 方式二：编辑 AGENTS.md（项目级行为规则）

在 `~/.floodmind/AGENTS.md`（全局）或 `<项目目录>/AGENTS.md`（项目级）中追加行为约束：

```markdown
## 绘图默认风格
- 必须设置图例
- 中文优先，使用 SimSun 字体

## 文档生成偏好
- Word 文件使用公司标准模板
- 图表配色使用蓝色系
```

#### 方式三：覆盖 Agent 类型提示词

在 `~/.floodmind/settings.json` 中为特定 Agent 类型设置自定义 system prompt：

```json
{
  "agent": {
    "agents": {
      "build": {
        "prompt": "你是一个专注于代码审查的 Agent..."
      }
    }
  }
}
```

#### 方式四：子类化组合 guidance 常量（代码级深度定制）

```python
from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.profile.soul import load_soul_md, DEFAULT_FLOODMIND_IDENTITY
from floodmind.profile.guidance import (
    WORK_METHOD_GUIDANCE,
    TOOL_EXECUTION_GUIDANCE,
    WORKFLOW_GUIDANCE,
)

class MyAgent(NativeFloodAgent):
    @classmethod
    def _build_stable_prompt(cls, skill_catalog, tool_descriptions, tool_registry=None):
        soul = load_soul_md() or "你是 MyAgent，一个自定义智能助手。"
        return "\n\n".join([
            soul,
            WORK_METHOD_GUIDANCE,
            TOOL_EXECUTION_GUIDANCE,
            WORKFLOW_GUIDANCE,
            f"## 可用技能\n{skill_catalog}",
        ])
```

#### 提示词优先级（高→低）

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | `agent.agents.<name>.prompt` | settings.json 中完全覆盖某 Agent 类型 |
| 2 | `~/.floodmind/SOUL.md` | 外部身份文件，替换默认身份描述 |
| 3 | `DEFAULT_FLOODMIND_IDENTITY` | 代码内置的 fallback 身份 |

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

### 配置文件一览

`~/.floodmind/` 目录下的关键文件：

| 文件 | 说明 |
|------|------|
| `settings.json` | 主配置文件（模型、Provider、Agent 参数） |
| `mcp.json` | MCP Server 连接配置（独立管理，首次启动自动从旧 settings.json 迁移） |
| `search.json` | WebSearch 搜索引擎配置（API Key、URL、Provider） |
| `SOUL.md` | 智能体身份定义（首次启动自动生成，可直接编辑） |
| `AGENTS.md` | 全局行为规则与偏好约束（项目级放在工作目录下的 `AGENTS.md`） |

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

### MCP Server 配置

MCP Server 连接配置独立存储在 `~/.floodmind/mcp.json`：

```json
{
  "servers": [
    {
      "name": "knowledge",
      "transport": "stdio",
      "command": "python",
      "args": ["~/.floodmind/mcp/knowledge/server.py"],
      "enabled": true
    }
  ]
}
```

### WebSearch 搜索配置

WebSearch 工具的 API Key 和搜索引擎配置独立存储在 `~/.floodmind/search.json`：

```json
{
  "engine": "baidu_qianfan",
  "url": "https://qianfan.baidubce.com/v2/ai_search/web_search",
  "api_key": "your_key_here"
}
```

也可通过环境变量覆盖：`BAIDU_API_KEY`、`FLOODMIND_SEARCH_API_KEY`、`FLOODMIND_SEARCH_URL`。

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
│   │   ├── settings.py         #   配置模型（主配置 + MCP 独立加载）
│   │   ├── search_config.py    #   WebSearch 搜索配置（独立文件）
│   │   ├── model_presets.py    #   模型预设
│   │   └── settings_template.json  # 初始模板
│   ├── profile/                # 身份与提示词定制
│   │   ├── soul.py             #   SOUL.md 加载与种子
│   │   └── guidance.py         #   行为指导常量（可组合，8段精简版）
│   ├── plugin/                  # Plugin 系统
│   │   ├── base.py             #   FloodmindPlugin 基类
│   │   └── loader.py           #   自动发现式 PluginLoader
│   ├── memory/                 # 记忆与经验系统
│   │   ├── dual_memory.py      #   双层记忆
│   │   ├── experience_tree.py  #   经验树索引
│   │   └── task_experience.py  #   任务经验
│   ├── models/                 # LLM 服务
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
| `/api/sessions/<id>/messages` | GET | 分页获取消息（cursor-based） |
| `/api/sessions/<id>/events` | GET | 事件溯源回放 |
| `/api/upload` | POST | 上传文件 |
| `/api/files` | GET | 列会话文件 |
| `/api/files/<id>/download` | GET | 文件下载 |
| `/api/models` | GET | 模型列表 |
| `/api/scheduled-tasks` | GET / PATCH / DELETE | 定时任务管理 |
| `/api/token-usage` | GET | 获取会话 Token 用量统计 |
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
