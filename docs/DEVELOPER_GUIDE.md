# FloodMind 二次开发指南

## 概述

FloodMind 是基于大语言模型的智能洪水预报 Agent 系统。本文档面向开发者，介绍如何将 FloodMind 集成到第三方系统、构建自定义界面、扩展模型支持和开发新的 Skill。

---

## 目录

1. [架构概述](#1-架构概述)
2. [环境搭建](#2-环境搭建)
3. [Python API 集成](#3-python-api-集成)
4. [HTTP API 集成](#4-http-api-集成)
5. [自定义 Skill 开发](#5-自定义-skill-开发)
6. [系统提示词与身份定制](#6-系统提示词与身份定制)
7. [模型与 Provider 扩展](#7-模型与-provider-扩展)
8. [构建自定义 Web 界面](#8-构建自定义-web-界面)
9. [会话管理与记忆系统](#9-会话管理与记忆系统)
10. [TUI 界面扩展](#10-tui-界面扩展)
11. [项目结构参考](#11-项目结构参考)

---

## 1. 架构概述

```
┌──────────────────────────────────────────────────────┐
│                   用户入口层                          │
│  Web (React) │ TUI (Textual) │ CLI (Click) │ HTTP API │
└────────────────────────┬─────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────┐
│                 NativeFloodAgent                     │
│  ┌─────────┐  ┌──────────┐  ┌────────────────────┐  │
│  │ Planner │  │ Executor │  │ Orchestrator        │  │
│  │  规划器  │  │  执行器  │  │  编排器 (主控循环)   │  │
│  └─────────┘  └──────────┘  └────────────────────┘  │
└────────────────────────┬─────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────┐
│                   服务层                              │
│  ┌──────────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │  ModelClient │ │  Memory  │ │  Tool Registry   │ │
│  │  LLM 服务    │ │  记忆系统  │ │  工具注册表       │ │
│  └──────────────┘ └──────────┘ └──────────────────┘ │
└──────────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **NativeFloodAgent** | `floodmind/agent/native/native_flood_agent.py` | Agent 生命周期管理、token 流式输出、并行委派 |
| **NativeAgentExecutor** | `floodmind/agent/native/executor.py` | 工具调用循环、消息组装、产物检测 |
| **ModelClient** | `floodmind/agent/native/model_client.py` | 统一的 LLM 服务接口（stream_chat / chat / invoke） |
| **DualMemory** | `floodmind/memory/dual_memory.py` | 双层记忆系统（短期 + 长期 + LLM 压缩） |
| **Skills** | `floodmind/skills/` | 自动发现式技能注册（13 个内置 Skill） |
| **Tools** | `floodmind/tools/` | 工具层（Glob、Grep、Bash、Read、Write、Edit 等） |
| **RAG** | `floodmind/rag/` | 向量检索（ChromaDB + BGE Embedding） |

---

## 2. 环境搭建

### 系统要求

- Python 3.10+
- Node.js 18+（前端开发）
- NVIDIA GPU（可选，时序预测加速）

### 源码安装

```bash
git clone <仓库地址> floodmind
cd floodmind
pip install -e .

# 安装可选依赖
pip install "floodmind[web,doc]"    # Web 服务 + 文档处理
pip install "floodmind[all]"        # 全部依赖（含 GPU）
```

### 配置

配置文件 `~/.floodmind/settings.json`。首次启动自动创建模板。

最小配置示例（DashScope）：

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

---

## 3. Python API 集成

### 3.1 最小集成：创建 Agent 并执行任务

```python
from floodmind.agent.native.model_client import ModelClient
from floodmind.memory import DualMemory
from floodmind.agent import create_flood_agent

# 1. 创建 LLM 服务（从 settings.json 读取配置）
llm = ModelClient.from_settings()

# 2. 创建记忆系统
memory = DualMemory(
    session_id="my-session-001",
    max_short_term=20,
    context_window=32768,
)

# 3. 创建 Agent
agent = create_flood_agent(
    llm_service=llm,
    memory=memory,
    session_id="my-session-001",
)

# 4. 流式对话
for chunk in agent.stream("分析敖江流域霍口水库的流量数据"):
    chunk_type = chunk.get("type", "")
    content = chunk.get("content", "")
    if chunk_type == "token":
        print(content, end="", flush=True)
    elif chunk_type == "reasoning":
        print(f"\n[思考] {content}")
    elif chunk_type == "tool_start":
        print(f"\n[调用工具] {chunk.get('tool_name', '')}")
    elif chunk_type == "error":
        print(f"\n[错误] {content}")

# 5. 单次执行（非流式）
result = agent.run("生成敖江流域水文预报报告")
print(result.final_output)
print("产物文件:", result.artifacts)
```

### 3.2 指定模型

```python
# 使用 settings.json 中的某个 preset 模型
llm = ModelClient.from_settings_with_preset(
    model_key="deepseek-v4-flash",
    enable_reasoning=True,
)

# 或者直接指定连接信息
llm = ModelClient(
    api_key="sk-xxx",
    base_url="https://api.deepseek.com/v1",
    model_name="deepseek-chat",
    temperature=0.3,
    max_tokens=8192,
)
```

### 3.3 直接调用 LLM（不通过 Agent）

```python
llm = ModelClient.from_settings()

# 非流式单次调用
response = llm.invoke("什么是洪水预报模型？")
print(response.content)

# 非流式多轮对话
response = llm.chat([
    {"role": "system", "content": "你是水文领域的专家。"},
    {"role": "user", "content": "什么是新安江模型？"},
])
print(response.content)
```

### 3.4 管理多个会话

```python
from floodmind.memory import SessionManager

# SessionManager 自动管理会话生命周期
session_mgr = SessionManager({
    "max_active_sessions": 16,
    "idle_timeout_minutes": 30,
    "data_dir": "./data",
})

# 创建会话
session_id = session_mgr.create_session()

# 恢复会话
agent = create_flood_agent(
    llm_service=llm,
    memory=DualMemory(
        session_id=session_id,
        max_short_term=20,
        context_window=32768,
    ),
    session_id=session_id,
)
```

---

## 4. HTTP API 集成

启动服务：`floodmind serve --port 8000`

### 4.1 基础端点

| 端点 | 方法 | Content-Type | 说明 |
|------|------|-------------|------|
| `/api/chat` | POST | application/json | 流式聊天（SSE/NDJSON） |
| `/api/init` | POST | application/json | 初始化会话 Agent |
| `/api/sessions` | GET | — | 列出所有会话 |
| `/api/sessions/<id>` | GET / DELETE | — | 会话详情 / 删除 |
| `/api/upload` | POST | multipart/form-data | 上传文件 |
| `/api/files/<id>/download` | GET | — | 文件下载 |
| `/api/models` | GET | — | 模型列表 |
| `/api/health` | GET | — | 健康检查 |

### 4.2 流式聊天示例

**请求：**

```json
POST /api/chat
Content-Type: application/json

{
  "session_id": "my-session-001",
  "message": "分析这份水位数据",
  "enable_reasoning": true,
  "model_key": "deepseek-v4-flash"
}
```

**响应（SSE 流）：**

```
data: {"type":"reasoning","content":"让我先查看数据..."}
data: {"type":"token","content":"根据数据"}
data: {"type":"token","content":"分析..."}
data: {"type":"artifact","content":{"path":"/outputs/report.docx","label":"分析报告"}}
data: {"type":"done","content":""}
```

### 4.3 Python 客户端示例

```python
import httpx
import json

def chat(session_id, message):
    url = "http://localhost:8000/api/chat"
    payload = {
        "session_id": session_id,
        "message": message,
        "enable_reasoning": True,
    }

    with httpx.stream("POST", url, json=payload, timeout=300) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                event_type = event.get("type")
                content = event.get("content", "")
                if event_type == "token":
                    print(content, end="", flush=True)
                elif event_type == "reasoning":
                    print(f"\n[思考] {content}")
                elif event_type == "done":
                    print()
                elif event_type == "error":
                    print(f"\n[错误] {content}")
```

---

## 5. 自定义 Skill 开发

Skill 是 FloodMind 的扩展单元。每个 Skill 是一个目录，包含一个 `SKILL.md` 文件和可选的脚本、参考文档。

### 5.1 创建 Skill

```bash
# 从模板创建
floodmind skill create my-skill

# 目录结构
skills/my-skill/
  SKILL.md          # 必须：Skill 定义（含触发条件）
  scripts/          # 可选：Python/JavaScript 脚本
  references/       # 可选：参考文档
  assets/           # 可选：静态资源
```

### 5.2 SKILL.md 格式

```markdown
---
name: my-skill
description: "TRIGGER when: 用户输入中提到'水位预报'或'流量预测'时。DO NOT TRIGGER when: 用户只是询问模型概念而非执行预报。"
version: 1.0
---

# My Skill

## 触发条件
- 用户明确要求进行水位预报
- 用户提供了包含水位数据的文件路径

## 执行步骤
1. 读取用户提供的数据文件
2. 运行预报模型（使用 `scripts/forecast.py`）
3. 生成预报报告

## 输入要求
- CSV 文件，包含 `date`、`water_level` 列

## 输出
- 预报报告（PDF）
- 预报结果（Excel）

## 注意事项
- 数据必须为连续的时间序列
- 结果仅供参考，请人工复核
```

### 5.3 Skill 的脚本路径

Agent 调用脚本时使用 Skill 目录下的绝对路径：

```python
# Agent 会这样执行
python /absolute/path/to/floodmind/skills/my-skill/scripts/forecast.py \
    --input /session/upload_dir/data.csv \
    --output /session/output_dir/forecast.xlsx
```

### 5.4 注册 Skill

FloodMind 启动时自动扫描 `floodmind/skills/` 目录，无需手动注册。系统会提取所有 `SKILL.md` 的 YAML front-matter 中的 `name` 和 `description`，并将 `description` 作为触发条件写入 Agent 的 system prompt。

### 5.5 高级：带 MCP 的 Skill

```yaml
---
name: advanced-hydro
version: 1.0
provides_tools:
  - forecast_water_level
  - analyze_water_quality
---
```

对应脚本：

```python
# scripts/forecast_water_level.py
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="输入数据路径")
    parser.add_argument("--output", required=True, help="输出路径")
    parser.add_argument("--model", default="chronos", help="预报模型")
    args = parser.parse_args()

    # 实现预报逻辑
    ...

if __name__ == "__main__":
    main()
```

---

## 6. 系统提示词与身份定制

FloodMind 的系统提示词采用分层可替换架构，支持从配置文件到代码级的多种定制方式。

### 6.1 架构概述

```
┌─────────────────────────────────────────────┐
│ Slot #0: 身份 (SOUL.md)                      │  ← 外部文件，可替换
├─────────────────────────────────────────────┤
│ Slot #1: 行为指导 (guidance.py 常量组合)      │  ← 可按需取舍
├─────────────────────────────────────────────┤
│ Slot #2: Skill 目录 + 工具目录               │  ← 运行时动态
├─────────────────────────────────────────────┤
│ Slot #3: 项目指令 (AGENTS.md)               │  ← 全局 + 项目级
├─────────────────────────────────────────────┤
│ Slot #4: 会话环境 (时间 + 路径 + OS)         │  ← 每会话不同
└─────────────────────────────────────────────┘
```

### 6.2 编辑 SOUL.md（推荐，无需改代码）

首次启动后在 `~/.floodmind/SOUL.md` 自动生成默认身份文件。编辑此文件即可替换智能体的身份定义：

```markdown
你是 MyBot，一个专注于数据分析的智能助手。

## 角色职责
1. 接收用户的数据文件和分析需求
2. 执行统计分析、可视化、报告生成

## 核心特质
- 严谨的数据驱动型分析
- 输出结果必须附带数据来源
```

当 `SOUL.md` 不存在或为空时，系统使用内置的 `DEFAULT_FLOODMIND_IDENTITY` 作为 fallback。

**代码中的加载逻辑：**

```python
from floodmind.profile.soul import load_soul_md, DEFAULT_FLOODMIND_IDENTITY

soul = load_soul_md() or DEFAULT_FLOODMIND_IDENTITY
```

### 6.3 编辑 AGENTS.md（项目级行为规则）

AGENTS.md 在两个级别读取并按顺序拼接到系统提示词中：

| 路径 | 作用域 | 典型用途 |
|------|--------|----------|
| `~/.floodmind/AGENTS.md` | 全局 | 跨项目的通用规则（字体、配色、文档模板等） |
| `<工作目录>/AGENTS.md` | 项目级 | 特定项目的约束（绘图风格、数据规范等） |

AGENTS.md 内容示例：

```markdown
## 绘图默认风格
- 必须设置图例
- 中文优先，使用 SimSun 字体
- 图表配色使用蓝色系

## 文档生成偏好
- Word 文件使用公司标准模板
- 报告末尾附免责声明
```

### 6.4 覆盖 Agent 类型提示词

通过 `settings.json` 为特定 Agent 类型设置自定义 system prompt，该方式会**完全替换**（而非追加）该 Agent 类型的提示词：

```json
{
  "agent": {
    "agents": {
      "build": {
        "prompt": "你是一个专注于代码审查的 Agent。使用 {tool_descriptions} 等工具...\n{skill_catalog}"
      },
      "plan": {
        "prompt": "你处于规划模式，只读分析和规划，不可修改文件。"
      }
    }
  }
}
```

Prompt 模板中可用的占位符：`{skill_catalog}`、`{tool_descriptions}`、`{project_context}`、`{session_env}`、`{current_time_context}`。

### 6.5 代码级定制：组合 guidance 常量

`floodmind/profile/guidance.py` 提供 11 个独立的行为指导常量，二次开发时可自由组合：

| 常量 | 内容 |
|------|------|
| `WORK_METHOD_GUIDANCE` | 工作方式（自己完成 vs 子代理委派） |
| `SCHEDULED_TASK_GUIDANCE` | 定时任务处理规则 |
| `KNOWLEDGE_GUIDANCE` | 知识入库规则 |
| `PREFERENCE_GUIDANCE` | 用户偏好处理规则 |
| `TOOL_EXECUTION_GUIDANCE` | 工具执行细节 |
| `PARALLEL_AGENT_GUIDANCE` | 并行子代理规则 |
| `WORKFLOW_GUIDANCE` | 工作流 5 步 |
| `WORK_PRINCIPLES_GUIDANCE` | 工作原则 8 条 |
| `ARTIFACT_JUDGMENT_GUIDANCE` | 产物意图判定 + 文档声明 |
| `OUTPUT_FORMAT_GUIDANCE` | 输出规范 |
| `AOJIANG_STATION_GUIDANCE` | 敖江流域站点编码（业务专属） |

子类化示例（只保留核心指导，去除敖江业务逻辑）：

```python
from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.profile.soul import load_soul_md, DEFAULT_FLOODMIND_IDENTITY
from floodmind.profile.guidance import (
    WORK_METHOD_GUIDANCE,
    TOOL_EXECUTION_GUIDANCE,
    WORKFLOW_GUIDANCE,
    WORK_PRINCIPLES_GUIDANCE,
    OUTPUT_FORMAT_GUIDANCE,
)

class MyAgent(NativeFloodAgent):
    @classmethod
    def _build_stable_prompt(cls, skill_catalog, tool_descriptions, tool_registry=None):
        soul = load_soul_md() or DEFAULT_FLOODMIND_IDENTITY
        return "\n\n".join([
            soul,
            WORK_METHOD_GUIDANCE,
            TOOL_EXECUTION_GUIDANCE,
            WORKFLOW_GUIDANCE,
            WORK_PRINCIPLES_GUIDANCE,
            OUTPUT_FORMAT_GUIDANCE,
            f"## 可用技能\n{skill_catalog}",
            f"## 可用工具\n{tool_descriptions}",
        ])
```

### 6.6 提示词优先级

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1（最高） | `agent.agents.<name>.prompt` in settings.json | 完全覆盖某 Agent 类型 |
| 2 | `~/.floodmind/SOUL.md` | 替换身份段，guidance 层不受影响 |
| 3（默认） | `DEFAULT_FLOODMIND_IDENTITY` | SOUL.md 缺失时的 fallback |

AGENTS.md 始终作为独立层（Slot #3）注入，不受上述优先级影响。

### 6.7 Profile 路径隔离

通过环境变量 `FLOODMIND_HOME` 可将整个配置目录重定向，实现不同部署场景的隔离：

```bash
# 开发环境
set FLOODMIND_HOME=C:\dev\floodmind_config
floodmind web

# 生产环境
set FLOODMIND_HOME=C:\prod\floodmind_config
floodmind web
```

`FLOODMIND_HOME` 目录下的文件结构：

```
<FLOODMIND_HOME>/
├── settings.json     # 主配置
├── SOUL.md           # 身份定义
├── AGENTS.md         # 全局指令
├── sessions/         # 会话数据
└── memories/         # 记忆存储
```

---

## 7. 模型与 Provider 扩展

### 7.1 添加新的 LLM Provider

在 `~/.floodmind/settings.json` 中添加：

```json
{
  "provider": {
    "my-provider": {
      "name": "我的模型平台",
      "options": {
        "apiKey": "sk-xxx",
        "baseURL": "https://api.my-platform.com/v1"
      },
      "models": {
        "my-model": {
          "name": "我的模型",
          "description": "性能优秀的通用模型",
          "maxTokens": 8192,
          "temperature": 0.3,
          "supportsReasoning": true,
          "supportsVision": false
        }
      }
    }
  }
}
```

### 7.2 Python 中使用自定义模型

```python
from floodmind.agent.native.model_client import ModelClient

# 方式 1：从 settings.json 中按 key 选择
llm = ModelClient.from_settings_with_preset("my-model")

# 方式 2：直接用连接信息构建
llm = ModelClient(
    api_key="sk-xxx",
    base_url="https://api.my-platform.com/v1",
    model_name="my-model",
    temperature=0.3,
    max_tokens=8192,
    enable_thinking=True,
)

# 非流式调用
response = llm.invoke("你好")
print(response.content)

# 流式调用（Agent 内部使用）
for event in llm.stream_chat([{"role": "user", "content": "你好"}]):
    if event.type == "token":
        print(event.content, end="", flush=True)
```

---

## 8. 构建自定义 Web 界面

### 8.1 集成内置 Web 前端

FloodMind 的 Web 前端（`web/` 目录）是用 React 19 + TypeScript + Vite 7 构建的。前端通过 `/api/*` REST 端点与后端通信。

```bash
cd web
npm install
npm run dev        # 开发模式 (localhost:5173)
npm run build      # 生产构建
```

生产模式下，Flask 后端会自动 serve `web/dist/` 中的静态文件。

### 8.2 构建全新前端（仅使用后端 API）

如果你要构建全新的前端（如 Vue、Next.js、移动端），只需调用 HTTP API：

```javascript
// 示例：初始化会话
const initRes = await fetch('http://localhost:13014/api/init', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session_id: 'my-session-001' })
});

// 示例：流式聊天（EventSource / fetch streaming）
const chatRes = await fetch('http://localhost:13014/api/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    session_id: 'my-session-001',
    message: '分析水位数据',
    enable_reasoning: true,
  })
});

const reader = chatRes.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const text = decoder.decode(value);
  // 解析 SSE 格式的 token 流
  const lines = text.split('\n');
  for (const line of lines) {
    if (line.startsWith('data: ')) {
      const event = JSON.parse(line.slice(6));
      handleStreamEvent(event);
    }
  }
}
```

### 8.3 自定义 Web 服务端口和配置

```python
from web_server import app

# 自定义端口启动
app.run(host="0.0.0.0", port=8080, threaded=True)

# 或使用 waitress（生产模式）
from waitress import serve
serve(app, host="0.0.0.0", port=8080, threads=8)
```

---

## 9. 会话管理与记忆系统

### 9.1 会话生命周期

```
创建 → 活跃（对话中）→ 空闲（超时后）→ 清理（过期后）
```

```python
from floodmind.memory import SessionManager

sm = SessionManager({
    "max_active_sessions": 16,
    "idle_timeout_minutes": 30,
    "session_retention_days": 30,
    "data_dir": "./data",
})

# 创建会话
sid = sm.create_session()

# 获取会话信息
session = sm.get_session(sid)
print(session.title, session.created_at, session.last_active_at)

# 列出所有活跃会话
for s in sm.list_sessions():
    print(f"{s['id']}: {s.get('title', '无标题')}")
```

### 9.2 对话历史导出

```python
from floodmind.memory import export_session_markdown

# 导出为 Markdown
markdown = export_session_markdown("session-xxx")
Path("conversation.md").write_text(markdown, encoding="utf-8")

# CLI 方式
# floodmind session export session-xxx -o conversation.md
```

### 9.3 自定义记忆后端

`DualMemory` 存储对话历史到 `data/sessions/<id>/memory/chat_history.json`。如需自定义存储（如数据库），实现 `DualMemory` 的 `save_chat_history()` 和 `load_chat_history()` 方法即可。

---

## 10. TUI 界面扩展

TUI（Terminal User Interface）基于 Textual 框架，位于 `floodmind/tui/`。

### 启动

```bash
floodmind tui          # 启动 TUI（后台自动启动 web server）
floodmind tui --port 8080  # 指定端口
```

### 自定义 TUI 主题

编辑 `floodmind/tui/tui.css`：

```css
Screen {
  background: #1a1a2e;
}

ChatScreen {
  background: #16213e;
}

AssistantMessage {
  color: #e0e0e0;
  border: solid #0f3460;
}

UserMessage {
  color: #ffffff;
  background: #0f3460;
}
```

---

## 11. 项目结构参考

```
FloodMind/
├── floodmind/                        # Python 主包
│   ├── agent/                        # Agent 核心
│   │   ├── native/                   #   Native Agent Runtime
│   │   │   ├── native_flood_agent.py #     Agent 主体（生命周期、流式输出）
│   │   │   ├── executor.py           #     工具调用循环
│   │   │   ├── model_client.py       #     统一 LLM 服务 (stream_chat/chat/invoke)
│   │   │   ├── planner.py            #     任务规划器
│   │   │   ├── message_builder.py    #     消息组装
│   │   │   └── types.py              #     数据类型 + DAG 拓扑
│   │   ├── runtime/                  #   Runtime 服务
│   │   │   ├── contracts/            #     数据契约 (messages, tools, events)
│   │   │   └── services/             #     服务 (ask, permission, path)
│   │   ├── mcp_client.py             #   MCP 客户端
│   │   └── scheduled_task_runtime.py #   定时任务
│   ├── config/                       # 配置
│   │   ├── settings.py               #   settings.json 读取与模型
│   │   ├── model_presets.py          #   model preset 解析
│   │   └── provider_registry.py      #   Provider 注册与管理
│   ├── profile/                      # 身份与提示词定制
│   │   ├── soul.py                   #   SOUL.md 加载与种子
│   │   └── guidance.py               #   行为指导常量（可组合）
│   ├── memory/                       # 记忆与经验
│   │   ├── dual_memory.py            #   双层记忆（短期 + 长期 + LLM 压缩）
│   │   ├── experience_tree.py        #   经验树索引
│   │   ├── task_experience.py        #   任务经验
│   │   ├── session_manager.py        #   会话管理
│   │   └── skill_generator.py        #   经验到 Skill 自动生成
│   ├── models/                       # 模型模块（旧）
│   ├── MCP/                          # MCP Servers
│   │   ├── knowledge_mcp/            #   知识库管理
│   │   └── metahuman_mcp/            #   数字人服务
│   ├── skills/                       # Skills（13 个）
│   │   ├── aojiang-hydro/            #   敖江水文模型
│   │   ├── TSLM/                     #   时序预测
│   │   ├── csv/ xlsx/ docx/ pptx/ pdf/  # 文件处理
│   │   ├── plotting/                 #   绘图
│   │   ├── data-analysis/            #   数据分析
│   │   ├── canvas-design/            #   创意设计
│   │   ├── doc-coauthoring/          #   文档协作
│   │   ├── mcp-builder/              #   MCP 构建
│   │   └── skill-creator/            #   Skill 创建器
│   ├── tools/                        # Agent 工具层
│   ├── server/                       # SSE 服务器
│   ├── tui/                          # 终端 TUI
│   │   ├── app.py                    #   TUI 应用入口
│   │   ├── screens/chat.py           #   对话界面
│   │   ├── screens/home.py           #   主页
│   │   ├── widgets/                  #   组件（Logo、Message、Prompt、Footer）
│   │   └── dialogs/                  #   对话框（Models、Sessions、MCP）
│   ├── tui-ts/                       # TUI TypeScript 前端
│   ├── rag/                          # RAG 知识检索
│   └── cli.py                        # CLI 入口
├── web/                              # React 前端
│   └── src/
│       ├── features/                 #   功能模块（chat, context, sidebar, scheduler）
│       ├── components/ui/            #   UI 组件（shadcn/ui）
│       └── hooks/                    #   React Hooks
├── main.py                           # CLI 交互（旧入口）
├── start.py                          # 统一启动入口（Web + Scheduler）
├── web_server.py                     # Web 服务（Flask）
├── scheduler.py                      # 定时任务调度器
├── pyproject.toml                    # 包配置
├── Dockerfile                        # Docker 构建
└── docs/                             # 文档
    └── DEVELOPER_GUIDE.md
```

---

## 更多资源

- **README**: 项目概述、快速开始、CLI 参考
- **AGENTS.md**: Agent 行为指令和项目规则
- **settings 模板**: `floodmind/config/settings_template.json`（首次启动自动创建）
- **API 文档**: 见 [HTTP API 集成](#4-http-api-集成) 章节
