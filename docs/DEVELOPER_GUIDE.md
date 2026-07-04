# FloodMind 二次开发指南 v2

> **更新**: 2026-07-04 — MCP 统一 (A/B/C)、Skill 统一 (A/B/C/D)、C-core/C-deep 完成后重写

FloodMind 是基于大语言模型的智能水文预报 Agent 系统。本文档面向开发者，介绍如何将 FloodMind 集成到第三方系统、构建自定义界面、扩展模型支持和开发 Skill/Plugin。

---

## 目录

1. [架构概述](#1-架构概述)
2. [环境搭建](#2-环境搭建)
3. [Python API 集成](#3-python-api-集成)
   - 3.1 [Quick Start: Agent SDK](#31-quick-start-agent-sdk)
   - 3.2 [流式事件协议](#32-流式事件协议)
   - 3.3 [工具架构](#33-工具架构)
   - 3.4 [编程式 Skill 注册](#34-编程式-skill-注册)
   - 3.5 [记忆与会话管理](#35-记忆与会话管理)
   - 3.6 [Advanced: NativeFloodAgent & create_flood_agent](#36-advanced-nativefloodagent--create_flood_agent)
4. [HTTP API 集成](#4-http-api-集成)
5. [MCP 集成](#5-mcp-集成)
   - 5.1 [MCP Server 配置](#51-mcp-server-配置)
   - 5.2 [运行时 MCP 管理](#52-运行时-mcp-管理)
   - 5.3 [MCP Client API](#53-mcp-client-api)
6. [Skill 系统](#6-skill-系统)
   - 6.1 [创建 Skill](#61-创建-skill)
   - 6.2 [SKILL.md 格式](#62-skillmd-格式)
   - 6.3 [Skill 发现机制](#63-skill-发现机制)
   - 6.4 [Skill CRUD 工具](#64-skill-crud-工具)
   - 6.5 [Skill 维护 (SkillCurator)](#65-skill-维护-skillcurator)
7. [系统提示词与身份定制](#7-系统提示词与身份定制)
8. [模型与 Provider 扩展](#8-模型与-provider-扩展)
9. [构建自定义 Web 界面](#9-构建自定义-web-界面)
10. [TUI 界面扩展](#10-tui-界面扩展)
11. [Plugin 系统开发](#11-plugin-系统开发)
12. [测试与调试](#12-测试与调试)
13. [项目结构参考](#13-项目结构参考)

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
│  ┌──────────────────────────────────────────────┐   │
│  │  orchestrator_executor (NativeAgentExecutor)  │   │
│  │  specialist_executor  (NativeAgentExecutor)   │   │
│  │  ├─ 状态机: created→awaiting_llm↔awaiting_tool │   │
│  │  ├─ 双 registry: orchestrator / specialist    │   │
│  │  └─ EventBus → SSE / 流式回调                  │   │
│  └──────────────────────────────────────────────┘   │
└────────────────────────┬─────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────┐
│                   服务层                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐ │
│  │ModelClient│ │  Memory  │ │ ToolExecutionService │ │
│  │ LLM 服务  │ │  记忆系统 │ │ 权限/沙箱/日志       │ │
│  └──────────┘ └──────────┘ └──────────────────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐ │
│  │McpClient │ │ Skill    │ │ SkillCurator         │ │
│  │ Pool     │ │ Registry │ │ 使用统计/巡检/归档    │ │
│  └──────────┘ └──────────┘ └──────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **NativeFloodAgent** | `floodmind/agent/native/native_flood_agent.py` | Agent 生命周期、双 registry（orchestrator/specialist）、MCP/Skill 管理工具、流式输出、并行委派 |
| **NativeAgentExecutor** | `floodmind/agent/native/executor.py` | 状态机驱动的 LLM↔Tool 循环、排队消息注入、上下文压缩 |
| **ModelClient** | `floodmind/agent/native/model_client.py` | 统一的 LLM 服务接口（stream_chat / chat / invoke） |
| **DualMemory** | `floodmind/memory/dual_memory.py` | 扁平 `_turns` 对话历史 + LLM 压缩 + 持久化 |
| **SkillRegistry** | `floodmind/skills/registry.py` | Skill 单例注册表（3 发现根、CWD 无关、线程安全） |
| **SkillCurator** | `floodmind/skills/skill_curator.py` | Skill 生命周期管理（使用追踪/stale 检测/归档/巡检） |
| **McpClientPool** | `floodmind/agent/mcp_client.py` | MCP 连接池（热插拔、连接/注册解耦） |
| **Tools** | `floodmind/tools/` | AgentTool↔ToolSpec 双抽象 + 内置工具（Glob/Grep/Bash/Read/Write/Edit 等） |

**Agent 双执行器说明**：NativeFloodAgent 内部维护两个 `NativeAgentExecutor` 实例——`orchestrator_executor`（主代理，拥有全部工具和管理权限）和 `specialist_executor`（子代理，白名单工具，无委派/管理权限）。不存在独立的 Planner/Orchestrator 类——规划功能通过 `create_plan`/`update_plan`/`exit_plan_mode` 工具实现，编排通过 `SubAgent`/`ParallelTask` 工具实现。

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

配置文件位于 `~/.floodmind/` 目录下，按职责分为独立文件：

| 文件 | 说明 |
|------|------|
| `settings.json` | 主配置（模型、Provider、Agent 参数） |
| `mcp.json` | MCP Server 连接配置（独立管理） |
| `search.json` | WebSearch 搜索引擎配置 |
| `SOUL.md` | 智能体身份定义 |
| `AGENTS.md` | 全局行为规则 |

首次启动自动创建模板。最小配置示例（DashScope）：

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

MCP Server 配置独立存储在 `~/.floodmind/mcp.json`：

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

---

## 3. Python API 集成

### 3.1 Quick Start: Agent SDK

推荐使用 `Agent` SDK 类将 FloodMind 嵌入已有系统。不需要 `settings.json`，纯代码配置：

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

tools = [
    build_agent_tool(
        func=query_station,
        name="QueryStation",
        description="查询监测站实时数据",
        is_readonly=True,
    ),
]

# 3. 创建 Agent（bare 模式，不加载内置工具/权限/MCP）
agent = Agent(
    llm=llm,
    tools=tools,
    system_prompt="你是水文预报助手，帮用户查询监测数据并运行预报模型。",
    session_id="my-system-001",
)

# 4. 非流式
result = agent.run("查一下霍口水库水位")

# 5. 流式 — 对接自建前端
for event in agent.stream("查一下霍口水库水位"):
    if event["type"] == "answer_delta":
        print(event["content"], end="", flush=True)
    elif event["type"] == "action_start":
        print(f"\n[调用工具] {event['tool_name']}")
    elif event["type"] == "final_text":
        print(f"\n[最终结果] {event['content']}")
```

**Agent 构造参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `llm` | `ModelClient` | (必填) | LLM 客户端 |
| `tools` | `list[AgentTool\|ToolSpec]` | `None` | 自定义工具列表 |
| `system_prompt` | `str` | `None` | 自定义系统提示词 |
| `memory` | `DualMemory` | `None` | 记忆系统（不传自动创建） |
| `session_id` | `str` | `""` | 会话 ID（默认 `"sdk-agent"`） |
| `enable_search` | `bool` | `False` | 启用 WebSearch |
| `enable_reasoning` | `bool` | `False` | 启用推理模式 |
| `on_event` | `Callable[[dict], None]` | `None` | 流式事件回调 |
| `permission_handler` | `Callable[[str, dict], bool]` | `None` | 工具审批钩子 |
| `max_iterations` | `int` | `50` | 最大循环轮数 |

**结果访问：** 每次 `run()`/`stream()` 后自动重置。

| 属性 | 类型 | 说明 |
|------|------|------|
| `agent.last_usage` | `dict` | token 用量（`prompt_tokens`/`completion_tokens`/`total_tokens`） |
| `agent.artifacts` | `list[dict]` | `file_generated`/`image_generated` 事件 |
| `agent.raw` | `NativeFloodAgent` | 底层实例（高级用法） |

### 3.2 流式事件协议

`agent.stream()` 产出结构化 dict，`on_event` 回调同样收到：

**回答 / 思考：**

| event.type | 含义 |
|------------|------|
| `answer_delta` | 回答文本增量 |
| `thought_delta` | 思考过程增量（reasoning 时） |
| `final_text` | 最终完整回答 |

**工具 / 计划：**

| event.type | 含义 |
|------------|------|
| `action_start` | 工具调用开始（`tool_name`, `status`, `call_id`） |
| `action_end` | 工具调用结束（`tool_name`, `content`） |
| `workflow_plan` | 执行计划（`title`, `steps`） |
| `workflow_step` | 步骤进度（`step_key`, `status`） |

**生命周期 / 系统：**

| event.type | 含义 |
|------------|------|
| `llm_step_start` / `llm_step_end` | LLM 调用边界 |
| `retry_attempt` | 模型重试（`attempt`） |
| `context_compress_start` / `_done` | 上下文压缩 |
| `token_usage` | token 用量累计 |
| `file_generated` / `image_generated` | 产物事件 |
| `heartbeat` | 心跳（可忽略） |
| `error` / `llm_token_error` | 错误 |

### 3.3 工具架构

FloodMind 工具体系有两层抽象：

```
编写层（开发者使用）              运行时层（Agent 使用）
┌──────────────────┐            ┌──────────────────┐
│ AgentTool        │  to_tool_  │ ToolSpec          │
│ (pydantic)       │──spec()──→│ (dataclass)       │
│                  │            │                  │
│ name, description│            │ name, description│
│ func, parameters │            │ func, parameters │
│ is_readonly      │            │ permission_policy│
│ is_destructive   │            │ is_readonly      │
│ is_concurrency_  │            │ is_destructive   │
│ safe             │            │ is_concurrency_  │
└──────────────────┘            │ safe             │
                                └──────────────────┘
```

`AgentTool.to_tool_spec()` 是唯一的转换入口。`Agent` SDK 接受两种类型——内部自动归一化。

**`build_agent_tool()` 完整签名：**

```python
def build_agent_tool(
    func: Callable,                        # 工具函数
    name: Optional[str] = None,            # 默认 func.__name__
    description: Optional[str] = None,     # 默认 func.__doc__
    args_schema: Optional[Type[BaseModel]] = None,  # Pydantic 参数模型
    parameters: Optional[Dict[str, Any]] = None,    # 原始 JSON Schema
    is_readonly: bool = True,              # 只读工具（plan 模式可用）
    is_destructive: bool = False,          # 破坏性操作
    is_concurrency_safe: bool = True,      # 并发安全
    check_permissions_fn: Optional[Callable] = None,   # 自定义权限检查
    validate_input_fn: Optional[Callable] = None,      # 输入校验
    permission_policy: Optional[ToolPermissionPolicy] = None,  # 权限策略
) -> AgentTool:
```

**权限策略** (policy_type)：

| 策略 | 含义 | plan 模式 |
|------|------|-----------|
| `readonly` | 纯读取 | ✅ 允许 |
| `state_write` | 状态写入（文件/配置） | ❌ 拒绝 |
| `exec` | 系统命令执行 | ❌ 拒绝 |
| `network` | 网络访问（MCP/搜索） | ❌ 拒绝 |
| `ask` | 需要用户确认 | ❌ 拒绝 |

### 3.4 编程式 Skill 注册

不需要 SKILL.md 文件，直接用代码注册。`register_skill()` 委托 `SkillRegistry` 单例：

```python
from floodmind import Skill, register_skill

skill = Skill(
    name="water-forecast",
    description="TRIGGER when: 用户要求进行水位预报时",
    prompt="## 水位预报流程\n1. 读取监测数据\n2. 运行新安江模型\n3. 输出预报结果",
)
register_skill(skill)
```

编程式注册的 skill 不落盘，重启后消失。持久化 skill 用 [Skill CRUD 工具](#64-skill-crud-工具) 或直接写 `SKILL.md`。

### 3.5 记忆与会话管理

```python
from floodmind.memory import DualMemory, SessionManager

# 独立使用记忆系统
memory = DualMemory(session_id="my-session-001", context_window=32768)
memory.add_user_message("查水位")

# SessionManager 管理会话生命周期
sm = SessionManager({
    "max_active_sessions": 16,
    "idle_timeout_minutes": 30,
    "data_dir": "./data",
})
sid = sm.create_session()
```

> **注意**: `DualMemory` 的 `max_short_term` 和 `max_long_term` 参数已弃用（`_short_term` 子系统已删除）。传入非默认值会触发 `DeprecationWarning`。

### 3.6 Advanced: NativeFloodAgent & create_flood_agent

SDK `Agent` 类封装了 `NativeFloodAgent(bare=True)`。如需完整功能（内置工具、MCP、权限、Skill 系统），使用 `create_flood_agent`：

```python
from floodmind.agent.native.model_client import ModelClient
from floodmind.memory import DualMemory
from floodmind import create_flood_agent

llm = ModelClient.from_settings()
memory = DualMemory(session_id="full-mode-001", context_window=32768)

agent = create_flood_agent(llm_service=llm, memory=memory, session_id="full-mode-001")

# 流式 — 同 SDK Agent 协议
for chunk in agent.stream("分析敖江流域霍口水库的流量数据"):
    chunk_type = chunk.get("type", "")
    if chunk_type == "answer_delta":
        print(chunk.get("content", ""), end="", flush=True)

# 非流式
result = agent.run("生成敖江流域水文预报报告")
```

直接使用 LLM（不通过 Agent）：

```python
llm = ModelClient.from_settings()
response = llm.invoke("什么是洪水预报模型？")
response = llm.chat([
    {"role": "system", "content": "你是水文领域的专家。"},
    {"role": "user", "content": "什么是新安江模型？"},
])
```

---

## 4. HTTP API 集成

启动服务：`floodmind serve --port 8000`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat` | POST | 流式聊天（SSE/NDJSON） |
| `/api/init` | POST | 初始化会话 Agent |
| `/api/sessions` | GET | 列出所有会话 |
| `/api/sessions/<id>` | GET / DELETE | 会话详情 / 删除 |
| `/api/upload` | POST | 上传文件（multipart） |
| `/api/files/<id>/download` | GET | 文件下载 |
| `/api/models` | GET | 模型列表 |
| `/api/health` | GET | 健康检查 |

**流式聊天示例：**

```python
import httpx, json

def chat(session_id, message):
    url = "http://localhost:8000/api/chat"
    with httpx.stream("POST", url, json={
        "session_id": session_id,
        "message": message,
        "enable_reasoning": True,
    }, timeout=300) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                t = event.get("type")
                if t == "answer_delta":
                    print(event["content"], end="", flush=True)
                elif t == "stream_end":
                    print()
                elif t == "error":
                    print(f"\n[错误] {event['content']}")
```

---

## 5. MCP 集成

FloodMind 的 MCP 集成遵循**运行时热插拔**准则：系统运行状态下随时接入随时发现，不需要重启。连接与注册解耦——`McpClientPool` 只管理连接，`build_mcp_tool_specs()` 构造 ToolSpec，调用方自行注册到 registry。

### 5.1 MCP Server 配置

`~/.floodmind/mcp.json`（独立于 settings.json）：

```json
{
  "servers": [
    {
      "name": "rag-server",
      "transport": "sse",
      "url": "http://localhost:9000/sse",
      "enabled": true
    },
    {
      "name": "data-tool",
      "transport": "stdio",
      "command": "python",
      "args": ["./mcp/data_server.py"],
      "enabled": true
    }
  ]
}
```

Agent 启动时自动连接所有 `enabled: true` 的 server。

### 5.2 运行时 MCP 管理

Agent 自身可调用以下管理工具（仅 orchestrator 可用）：

**LoadMcpServer** — 运行时动态接入 MCP server：

```
Agent 调用 LoadMcpServer(name="new-server", transport="sse", url="http://...")
  → 连接 → 发现工具 → 注册到双 registry → 立即可用
```

**ListMcpServers** — 列举所有已接入的 MCP server（含工具数、连接状态）。

**DisconnectMcpServer** — 断开指定 server 并清理其工具：

```
Agent 调用 DisconnectMcpServer(name="rag-server")
  → 断开连接 → unregister_prefix("mcp:rag-server:") → 清理完成
```

### 5.3 MCP Client API

SDK 中直接使用 MCP 客户端：

```python
from floodmind import get_mcp_client_pool, build_mcp_tool_specs

pool = get_mcp_client_pool()

# 连接 server
conn = pool.connect_server({
    "name": "my-mcp",
    "transport": "sse",
    "url": "http://localhost:9000/sse",
})

# 构造 ToolSpec（连接与注册解耦——调用方自行注册）
specs = build_mcp_tool_specs(conn, "my-mcp", pool.call_tool)
# specs 是 List[ToolSpec]，可直接传给 Agent(tools=specs)

# 列举 / 断开
for s in pool.list_servers():
    print(f"{s['name']}: {len(s['tools'])} tools, connected={s['connected']}")

pool.disconnect_server("my-mcp")
```

---

## 6. Skill 系统

### 6.1 创建 Skill

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

### 6.2 SKILL.md 格式

```markdown
---
name: my-skill
description: "TRIGGER when: 用户输入中提到'水位预报'时"
version: 1.0
category: execution
---

# My Skill

## 触发条件
- 用户明确要求进行水位预报

## 执行步骤
1. 读取用户提供的数据文件
2. 运行预报模型（使用 `scripts/forecast.py`）
3. 生成预报报告

## 注意事项
- 数据必须为连续的时间序列
```

### 6.3 Skill 发现机制

FloodMind 从 **3 个根目录**自动发现 Skill（CWD 无关，基于包定位）：

| 根目录 | 用途 |
|--------|------|
| `floodmind/skills/` | 内置 Skill（随包发布） |
| `<项目根>/skills/` | 用户/项目 Skill（**CreateSkill 落盘目标**） |
| `<项目根>/.claude/skills/` | Claude Code 兼容 |

Skill 加载时自动进行威胁扫描（`scan_content_threats`）。12 个内置 Skill：aojiang-hydro、chronos、csv、data-analysis、doc-coauthoring、docx、mcp-builder、pdf、plotting、pptx、skill-creator、xlsx。

### 6.4 Skill CRUD 工具

Agent 可通过以下工具**自维护 Skill**（仅 orchestrator 可用，全部 `state_write` 除 ListSkills）：

| 工具 | 功能 | 示例 |
|------|------|------|
| **ListSkills** | 列出所有 skill（name/version/category/source） | 盘点现有 skill |
| **CreateSkill** | 创建新 skill，写 `SKILL.md` 到 writable_root | `CreateSkill(name="my-skill", description="...", body="## 流程\n...")` |
| **UpdateSkill** | 修改已有 skill（append/replace_body/replace_section/remove_section） | `UpdateSkill(name="my-skill", action="append", content="## 备注\n...")` |
| **RemoveSkill** | 归档 skill → `.archived/`（可恢复，非硬删） | `RemoveSkill(name="old-skill")` |
| **RefreshSkills** | 重扫所有发现根 + 重建 system prompt | 新增/编辑文件后使其生效 |

**安全**：所有写操作经过 `_validate_skill_name`（拒绝 `/`、`\\`、`..`、`.` 开头），防止路径穿越。

### 6.5 Skill 维护 (SkillCurator)

`SkillCurator` 自动追踪 skill 使用情况并定期巡检：

```
GetSkill 调用 → record_skill_usage(name, success=True/False)
  ├─ 累计 total_uses / success_count / failure_count
  └─ 自动 re-activate（若之前为 stale/archived）

定期巡检（每 6 小时，Agent 启动时触发）：
  ├─ 标记 stale: active + 30 天未使用
  ├─ 归档: stale + 90 天未使用 → archive_skill → .archived/
  └─ 重复检测: Jaccard bigram similarity ≥ 0.7

恢复: curator.restore_skill(name) → .archived/ → writable_root
```

SDK 中使用：

```python
from floodmind.skills.skill_curator import get_skill_curator

curator = get_skill_curator()
curator.record_usage("my-skill", success=True)   # 手动记录
stats = curator.get_stats()                       # 使用统计
stale = curator.find_stale_skills()               # 长期未用
dups = curator.find_duplicates(threshold=0.7)     # 重复检测
curator.run_maintenance()                         # 手动巡检
```

---

## 7. 系统提示词与身份定制

### 7.1 提示词分层架构

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

### 7.2 编辑 SOUL.md

`~/.floodmind/SOUL.md`（首次启动自动生成），直接编辑即可替换智能体身份。

### 7.3 Agent 类型系统

FloodMind 内置 4 种 Agent 类型，各有不同的工具权限集：

| 类型 | 权限 | 用途 |
|------|------|------|
| `build` | 全部工具 + 委派 + MCP 管理 + Skill CRUD | 默认，完整访问 |
| `plan` | 只读工具 + `create_plan`/`update_plan` | 规划模式，禁写 |
| `general` | 通用工具，无委派/管理权限 | 子代理角色 |
| `explore` | 只读搜索工具 | 代码探索 |

通过 `~/.floodmind/settings.json` 为特定类型覆盖 prompt：

```json
{
  "agent": {
    "agents": {
      "build": {
        "prompt": "你是专注于代码审查的 Agent...\n{skill_catalog}"
      }
    }
  }
}
```

可用占位符：`{skill_catalog}`、`{tool_descriptions}`、`{project_context}`、`{session_env}`、`{current_time_context}`。

### 7.4 代码级定制

`floodmind/profile/guidance.py` 提供 12 个独立行为指导常量（`WORK_METHOD_GUIDANCE`、`TOOL_EXECUTION_GUIDANCE`、`WORKFLOW_GUIDANCE` 等），子类化 `NativeFloodAgent` 可自由组合：

```python
from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.profile.soul import load_soul_md
from floodmind.profile.guidance import WORK_METHOD_GUIDANCE, WORKFLOW_GUIDANCE

class MyAgent(NativeFloodAgent):
    @classmethod
    def _build_stable_prompt(cls, skill_catalog, tool_descriptions, tool_registry=None):
        return "\n\n".join([
            load_soul_md(),
            WORK_METHOD_GUIDANCE,
            WORKFLOW_GUIDANCE,
            f"## 可用技能\n{skill_catalog}",
            f"## 可用工具\n{tool_descriptions}",
        ])
```

---

## 8. 模型与 Provider 扩展

在 `~/.floodmind/settings.json` 中添加 Provider：

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
          "maxTokens": 8192,
          "supportsReasoning": true
        }
      }
    }
  }
}
```

Python 中使用：

```python
from floodmind import ModelClient

# 从 settings 按 key 选择
llm = ModelClient.from_settings_with_preset("my-model")

# 直接用连接信息
llm = ModelClient(
    api_key="sk-xxx",
    base_url="https://api.my-platform.com/v1",
    model_name="my-model",
    temperature=0.3,
    max_tokens=8192,
)

# 非流式
response = llm.invoke("你好")

# 流式（Agent 内部使用）
for event in llm.stream_chat([{"role": "user", "content": "你好"}]):
    if event.type == "token":
        print(event.content, end="", flush=True)
```

---

## 9. 构建自定义 Web 界面

内置前端是 React 19 + TypeScript + Vite 7（`web/` 目录）：

```bash
cd web && npm install
npm run dev        # 开发模式 (localhost:5173)
npm run build      # 生产构建 → web/dist/
```

构建全新前端只需调用 HTTP API（见 [§4 HTTP API 集成](#4-http-api-集成)）。

---

## 10. TUI 界面扩展

```bash
floodmind tui                # 启动 TUI（后台自动启动 web server）
floodmind tui --port 8080    # 指定端口
```

自定义 CSS 主题：编辑 `floodmind/tui/tui.css`。

---

## 11. Plugin 系统开发

Plugin 是比 Skill 更强大的 Python 代码扩展机制，可直接注册工具到 Agent、hook 事件。

### 11.1 创建 Plugin

```python
# ~/.floodmind/plugins/my_plugin.py
from floodmind.plugin import FloodmindPlugin
from floodmind import build_agent_tool

class MyPlugin(FloodmindPlugin):
    @property
    def version(self) -> str:
        return "1.0.0"

    def get_tools(self) -> list:
        def _hello(name: str = "World") -> str:
            return f"Hello, {name}!"
        return [build_agent_tool(func=_hello, name="hello", description="Say hello")]

    def get_hooks(self) -> dict:
        def on_tool_done(event: dict):
            if event.get("type") == "action_end":
                print(f"Tool completed: {event.get('tool_name')}")
        return {"action_end": on_tool_done}

    def on_agent_init(self, agent) -> None:
        """Agent 初始化后调用"""
        pass
```

### 11.2 Plugin 发现与加载

两种格式：

```
# 单文件
~/.floodmind/plugins/my_plugin.py

# 目录
~/.floodmind/plugins/my_plugin/
├── plugin.json           # {"name":"...","version":"...","entry":"main"}
├── main.py
└── requirements.txt
```

`PluginLoader` 自动发现并加载：

```python
from floodmind.plugin import PluginLoader

loader = PluginLoader()
for p in loader.discover():
    print(f"{p.name} v{p.version}: {p.description}")
```

Plugin 在 `NativeFloodAgent._init_tools()` 期间加载，工具注册到 `_orchestrator_registry`。

### 11.3 Plugin / Skill / MCP 对比

| 扩展方式 | 编写难度 | 能力 | 适用场景 |
|---------|--------|------|---------|
| **Skill** | 零代码（SKILL.md） | 指令 + 脚本 | 领域知识、工作流模板 |
| **Plugin** | Python 代码 | 工具 + hook + Agent 配置 | 深度集成、自定义逻辑 |
| **MCP** | 独立进程 | 跨语言、标准化协议 | 外部服务、多 Agent 共享 |

---

## 12. 测试与调试

### 12.1 SDK Agent 测试

参考 `tests/test_sdk_agent.py`（40 tests），覆盖 SDK 全路径。关键模式：

```python
from unittest.mock import MagicMock
from floodmind import Agent, ModelClient

# Mock LLM 避免真实网络调用
mock_llm = MagicMock(spec=ModelClient)
mock_llm.stream_chat.return_value = [...]  # 预设流事件

agent = Agent(llm=mock_llm, system_prompt="test")
result = agent.run("hello")
assert result is not None

# 流式测试
events = list(agent.stream("hello"))
assert any(e["type"] == "final_text" for e in events)
```

### 12.2 工具测试

`AgentTool` → `ToolSpec` 转换可独立测试：

```python
from floodmind import AgentTool
from floodmind.agent.runtime.contracts.tools import ToolSpec

tool = AgentTool(name="TestTool", description="d", func=lambda: "ok")
spec = tool.to_tool_spec()
assert isinstance(spec, ToolSpec)
assert spec.name == "TestTool"
```

### 12.3 Skill 系统测试

参考 `tests/test_skill_registry.py`（9 tests）和 `tests/test_skill_curator.py`（17 tests）：

```python
from floodmind.skills.registry import get_skill_registry, SkillRegistry
from pathlib import Path

# 隔离测试：自定义 roots
reg = SkillRegistry(roots=[Path("/tmp/test_skills")], writable_root=Path("/tmp/test_skills"))
assert len(reg.list_skills()) == 0
```

### 12.4 运行全部测试

```bash
pytest tests/ -q          # 397 tests
pytest tests/test_sdk_agent.py -v   # SDK 相关
pytest tests/test_skill_registry.py tests/test_skill_curator.py -v  # Skill 系统
pytest tests/test_mcp_client.py -v  # MCP 客户端
```

---

## 13. 项目结构参考

```
FloodMind/
├── floodmind/                        # Python 主包
│   ├── agent/                        # Agent 核心
│   │   ├── native/                   #   Native Agent Runtime
│   │   │   ├── native_flood_agent.py #     Agent 主体（双 registry、MCP/Skill 管理、流式）
│   │   │   ├── executor.py           #     状态机 LLM↔Tool 循环
│   │   │   ├── model_client.py       #     统一 LLM 服务
│   │   │   ├── model_router.py       #     模型路由/降级
│   │   │   ├── event_bus.py          #     EventBus + StepEventBus
│   │   │   ├── message_builder.py    #     消息组装
│   │   │   ├── tool_runtime.py       #     AgentTool→ToolSpec 桥接
│   │   │   ├── context_compressor.py #     上下文压缩
│   │   │   ├── artifact_watcher.py   #     产物检测
│   │   │   ├── tool_guardrails.py    #     工具护栏（重复/螺旋检测）
│   │   │   ├── retry.py              #     LLM 重试 + 指数退避
│   │   │   ├── error_classifier.py   #     错误分类 + 恢复策略
│   │   │   ├── background_review.py  #     后台对话回顾
│   │   │   └── types.py              #     数据类型定义
│   │   ├── runtime/                  #   Runtime 服务
│   │   │   ├── contracts/            #     数据契约 (tools, messages, events, permissions)
│   │   │   ├── services/             #     服务 (tool_execution, permission, ask, checkpoint, journal, sandbox, tracing, workspace)
│   │   │   └── adapters/             #     适配器 (Flask SSE/checkpoint/permission/tracing API)
│   │   ├── mcp_client.py             #   MCP 客户端池 + build_mcp_tool_specs
│   │   ├── agent_registry.py         #   Agent 类型注册（build/plan/general/explore）
│   │   ├── api.py                    #   Agent SDK 类
│   │   └── task_runtime.py           #   任务运行时
│   ├── config/                       # 配置
│   ├── profile/                      # 身份与提示词
│   ├── memory/                       # 记忆与经验
│   │   ├── dual_memory.py            #   扁平 _turns 对话历史 + 压缩
│   │   ├── experience_tree.py        #   经验树索引
│   │   ├── task_experience.py        #   任务经验
│   │   ├── session_manager.py        #   会话管理
│   │   ├── session_store.py          #   SQLite 存储
│   │   └── skill_generator.py        #   经验→Skill 自动生成
│   ├── skills/                       # Skill 系统
│   │   ├── base.py                   #   Skill dataclass + 发现 + catalog
│   │   ├── registry.py               #   SkillRegistry 单例
│   │   ├── skill_curator.py          #   SkillCurator 生命周期
│   │   ├── aojiang-hydro/ chronos/ csv/ ...  # 12 个内置 Skill
│   ├── tools/                        # Agent 工具层
│   │   ├── agent_tool.py             #   AgentTool + ToolRegistry + build_agent_tool
│   │   ├── base_tools.py             #   内置工具（GetSkill/Bash/WebSearch/...）
│   │   ├── file_tools.py             #   文件工具
│   │   └── memory_tools.py           #   记忆工具
│   ├── plugin/                       # Plugin 系统
│   ├── tui/                          # 终端 TUI (Textual)
│   ├── cli.py                        # CLI 入口（floodmind 命令）
│   └── __init__.py                   # top-level SDK 导出
├── web/                              # React 19 + TypeScript 前端
├── web_server.py                     # Flask Web 服务
├── scheduler.py                      # 定时任务调度
├── tests/                            # 测试（397 tests, 34 files）
├── docs/                             # 文档
│   ├── DEVELOPER_GUIDE.md            #   本文档
│   └── architecture/                 #   架构 Wiki
│       ├── OVERVIEW.md               #     架构知识图谱
│       ├── ASSESSMENT.md             #     系统评估（已完成 vs 待处理）
│       ├── MCP_ARCHITECTURE.md       #     MCP 子系统详解
│       └── SKILL_ARCHITECTURE.md     #     Skill 统详解
├── pyproject.toml                    # 包配置
└── start.py                          # 统一启动入口
```

---

## 更多资源

- **架构 Wiki**: `docs/architecture/OVERVIEW.md` — 完整知识图谱 + MCP/Skill/Tool 架构详解
- **系统评估**: `docs/architecture/ASSESSMENT.md` — 已完成批次 vs 待处理项
- **README**: 项目概述、快速开始、CLI 参考
- **settings 模板**: `floodmind/config/settings_template.json`
- **SDK 测试参考**: `tests/test_sdk_agent.py` — 40 个 SDK 用例
