# FloodMind

**SDK-first 的智能水文 Agent Runtime — v1.1.9**

FloodMind 正在收敛为面向 Python 宿主系统的纯 SDK：以 `Agent` / `ModelClient` / `Workspace` / `build_agent_tool` 为公共入口，把水文模型、数据分析、文档生成、MCP 工具与 Skill 体系嵌入到业务平台或桌面助手中。Web / TUI 入口已进入 legacy 迁移路径，不再作为核心产品形态推进。

<p align="center">
  <img src="figure/floodmind-icon.png" width="80" alt="FloodMind">
</p>

---

## 核心功能

- **Python SDK 公共入口** — `Agent`、`ModelClient`、`Workspace`、`build_folder_workspace`、`build_agent_tool`、`ToolLoadingConfig`、Provider Pipeline 等可直接顶层导入
- **Native Agent Runtime** — 自研 Agent 执行引擎，支持工具调用循环、流式输出、规划与委派
- **Skill 系统** — 自动发现式技能注册，10 个内置 Skill（数据科学、文档生成、图表绘制、创意设计）；Chronos 等模型能力通过 MCP 协议外置为独立服务 (→ `contrib/`)
- **MCP 协议接入** — 标准 FastMCP Server（知识库检索 / 文档入库），作为 MCP 客户端通过 stdio/SSE 连接外部工具，支持运行时动态接入（LoadMcpServer）
- **任务经验树** — 树状层级经验组织，渐进压缩摘要、去重合并、热度衰减、经验→Skill 自动生成
- **扁平对话记忆** — `_turns` 扁平历史 + LLM 上下文压缩，单一历史源，整轮原子写入
- **Agent 工作区** — Harness 级 `Workspace` 抽象；SDK/CLI 默认 folder-first（启动路径即工作区、相对路径默认 cwd、`.floodmind/` 收纳会话状态/脚本/临时文件/产物索引），工作区外访问必须通过 `readable_roots` / `writable_roots` 显式授权；Web 保持 `data/sessions` legacy 兼容
- **Git Worktree 会话隔离** — `SessionManager` 支持为会话创建 git worktree 分支，自由实验不污染主会话（create/list/remove/fork_to_worktree）
- **水文模型集成** — 敖江水文模型、时序预测；Chronos 已外置为 MCP 服务 (→ `contrib/`)
- **RAG 知识库** — 独立 FastAPI REST 服务（ChromaDB + BGE Embedding），通过 MCP 协议接入，可快速插拔更换
- **Plan 任务规划** — 多步骤任务自动创建执行计划，实时跟踪执行进度和状态
- **State-only Checkpoint** — checkpoint 只冻结 Agent runtime state（`state.json` + `manifest.json`），不复制 workspace 文件；产物通过 artifact/journal 引用，文件回滚/versioning 作为独立能力演进
- **Token 用量统计** — 实时展示单条消息和会话级 prompt / completion / total tokens
- **定时任务调度** — 每日重复 / 一次性定时任务，后台自动执行并记录产物
- **文档自动生成** — 支持 Excel、Word、PDF、PPT 等格式
- **最小 CLI** — `floodmind run` 作为 SDK-oriented 本地执行入口；Web/TUI 命令仅保留 legacy 提示
- **多模型支持** — 配置即用：任意 OpenAI 兼容接口均可接入
- **厂商 Pipeline 自动路由** — 按 base_url / provider / 模型名自动选择厂商专属调用管线（DashScope/DeepSeek/Kimi/MiniMax + OpenAI 兜底），翻译思考开关方言、流式 reasoning 解析、`<think>` 标签剥离、usage 位置兼容
- **渐进式工具加载** — 提示目录直接列出全部工具名称与基本描述，`GetTool` 按需加载目标工具完整 schema，减少请求体与提示词膨胀；未加载工具 fail-closed
- **后台任务** — `Bash run_in_background=True` 启动长任务异步执行，stdout/stderr 直写文件（`.floodmind/sessions/<sid>/background/`），`TaskOutput`/`TaskList`/`TaskKill` 查询与终止，完成时注入 user 消息通知 Agent、EventBus 发 `background_task_completed` 供宿主唤醒；不受同步 120s 超时限制（默认存活上限 30 分钟）
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
| SDK Runtime | 自研 Native Agent（流式 + Queue 驱动 + DOOM LOOP + 自动重试 + 并行委派） |
| CLI | Click；`floodmind run` 为核心本地执行入口 |
| Legacy Web/TUI | Flask / React / Textual 代码仍在仓库中保留，但已从 SDK 核心路径隔离 |
| LLM | OpenAI 兼容接口（DashScope、DeepSeek、OpenAI、Ollama 等） |
| 存储 | SQLite（SyncEvent 回放）+ JSON（会话历史 / 配置）+ 文件系统 |
| 时序预测 | Chronos 2（MCP 外部服务）、TSLM、PyTorch |
| 容器化 | Docker + NVIDIA GPU 支持 |

---

## 快速开始

### 环境要求

- Python 3.10+
- NVIDIA GPU（可选，用于时序预测加速）

### 1. 安装

```bash
# 安装 SDK 核心（不安装 Web/TUI 旧栈）
pip install -e .

# 如果仍使用 requirements.txt，它同样只代表 SDK/core 默认依赖
pip install -r requirements.txt

# 按需安装可选能力
pip install "floodmind[doc]"       # 文档处理
pip install "floodmind[gpu]"       # GPU/时序预测相关能力
pip install "floodmind[legacy]"    # 临时兼容旧 Web/TUI 适配器
```

### 2. 配置 API 密钥

首次启动 FloodMind 会自动创建配置文件 `~/.floodmind/settings.json`。

配置采用 **OpenCode 风格的层级结构**——服务商 → 连接信息 → 模型列表 → 模型参数，层层递进。
只需在 `providers.<服务商>.api_key` 填入密钥：

```json
{
  "providers": {
    "dashscope": {
      "name": "DashScope (Alibaba)",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "sk-你的密钥",
      "models": [
        {
          "id": "deepseek-v4-flash",
          "name": "DeepSeek V4 Flash",
          "context_window": 65536,
          "default_max_tokens": 65536,
          "default_temperature": 0.3,
          "supports_reasoning": true
        }
      ]
    }
  }
}
```

> **配置最小化原则**：`settings.json` 只保留服务商目录。其余子系统参数（记忆窗口、最大轮次、经验系统等）均为代码合理默认。
> - **激活模型**默认选 catalog 第一个；在界面里切换模型属于**会话级**选择，不会写回配置文件。
> - **记忆窗口**直接取自当前模型的 `context_window`，无需额外配置。
> - **最大轮次**默认 999（auto-compact + DOOM LOOP 兜底），不入配置。
> - **经验系统**始终开启。

### 3. 使用 SDK / CLI

Python SDK 是主入口：

```python
from floodmind import Agent, ModelClient, Workspace, build_agent_tool

llm = ModelClient(
    api_key="sk-xxx",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    model_name="deepseek-v4-flash",
    provider="dashscope",
)

workspace = Workspace.from_folder("D:/work/project-a", session_id="sdk-agent")
agent = Agent(llm=llm, workspace=workspace)
print(agent.run("分析当前工作区的数据并生成摘要"))
```

本地一次性任务可用最小 CLI：

```bash
floodmind run "分析水库水位数据并生成报告"
floodmind providers
floodmind config show
```

`floodmind web` / `floodmind serve` / `floodmind tui` 当前只输出 legacy 迁移提示；如确需旧界面，请安装 legacy extra 或使用兼容分支。

### 4. Legacy Web / TUI

Web/TUI 代码仍保留在仓库中，便于迁移期兼容和参考，但不再属于 SDK 核心公共面。新集成应优先使用 Python SDK 或 `floodmind run`，自行对接桌面端、平台端或业务系统界面。

---

## CLI 参考

| 命令 | 说明 |
|------|------|
| `floodmind run "任务"` | 单次 SDK/folder-first 任务执行，适合脚本/调度/桌面助手调用 |
| `floodmind chat` | 纯文本终端对话（保留兼容，使用 folder-first workspace） |
| `floodmind init` | 初始化项目配置 |
| `floodmind config show` | 查看当前配置 |
| `floodmind config set <key> <val>` | 设置配置项 |
| `floodmind skill create <name>` | 从模板创建新 Skill |
| `floodmind skill list` | 列出已安装的 Skill |
| `floodmind providers` | 列出可用 AI Provider |
| `floodmind web` / `serve` / `tui` | Legacy 入口提示；不再启动核心 Web/TUI |

```bash
# 脚本调用（单次任务）
floodmind run "分析水库水位数据" -m deepseek-v4-flash
```

---

## 二次开发

FloodMind 的核心集成方式是 Python SDK；旧 Web/TUI 适配器已进入 legacy 迁移期，不建议新系统继续基于 HTTP UI 层扩展。

### Python SDK（嵌入式 Agent）

将 Agent 嵌入到任意 Python 系统，自定义工具、提示词和前端：

```python
from floodmind import Agent, ModelClient, build_agent_tool

# 1. 创建 LLM 客户端（任意 OpenAI 兼容接口）
# provider 可选：显式指定服务商以精确路由厂商 Pipeline；不传则按 base_url/模型名自动推断
llm = ModelClient(
    api_key="sk-xxx",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    model_name="deepseek-v4-flash",
    provider="dashscope",
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

**构造参数**：`llm`（必填）、`tools`、`system_prompt`、`memory`、`session_id`、`enable_search`、`enable_reasoning`、`on_event`（事件回调）、`permission_handler`（工具审批钩子）、`permission_decision_hook`（host-level 权限决策钩子，见下）、`max_iterations`（默认 999）、`workspace`（嵌入式工作区注入）、`tool_loading`（工具加载策略：`None` 用配置默认，`False` 为 eager 旧行为，`True` 或 `ToolLoadingConfig` 为渐进式加载）。

**Host-level 权限决策钩子**：`permission_decision_hook(tool_name, tool_input, sdk_decision, permission_policy) -> PermissionDecision`。SDK 完成基础权限判断后调用，宿主可基于 SDK 原始决策调整最终行为——保留 DENY/ASK、把 ALLOW 升级为 ASK（走 `permission_ask` 事件交互确认）或 DENY。钩子只能收紧不能放开：SDK 的安全拒绝（路径越界 / 危险命令 / 子代理分层 / planning 硬门）不可被覆盖；钩子异常或返回非法值时保留 SDK 原决策。桌面端可借此实现 always-trust / trust-once / always-ask 权限模式，无需 patch 内部 registry。

**公共 Agent 完整 runtime**：`Agent(..., bare=False)` 走 NativeFloodAgent 完整 runtime（内置工具、MCP、Skill、权限 ASK、workspace 绑定），`bare` 默认 `True` 保持裸嵌入行为不变。公共入口还提供 `agent.memory` / `agent.session_id` / `agent.clear_memory()` 代理，以及 `agent.stream(msg, abort_check=..., attachments=...)` 透传，宿主无需访问 `raw` 内部。

**MCP 运行时能力**：`build_mcp_tool_specs` 对 model-visible 工具名统一 sanitize（`mcp:<server>:<tool>` → `mcp_<server>_<tool>`，满足 OpenAI 兼容端点 `^[a-zA-Z0-9_-]+$`），实际调用仍用原始冒号全名；`McpClientConnection.is_connected` 对 stdio 做进程存活探测；`McpClientPool.call_health()` 记录最近一次每 server 调用结果；`add_server_connected_listener()` 感知运行时 MCP 连接成功事件。

**SDK 工作区**：嵌入式宿主推荐显式传入 `Workspace`，不要依赖进程 cwd；CLI/桌面入口可用 folder-first 模式把启动/打开目录作为工作区。

```python
from floodmind import Agent, Workspace

workspace = Workspace.from_folder("D:/work/project-a", session_id="sdk-agent")
agent = Agent(llm=llm, tools=tools, workspace=workspace)
```

Folder-first 下相对路径默认相对 `workspace.default_cwd`，FloodMind 内部状态收纳到 `<workspace>/.floodmind/`；如需访问工作区外目录，请通过 `readable_roots` / `writable_roots` 显式授权。SDK 未显式传入 `workspace` 时会默认绑定 `Workspace.from_cwd(session_id="sdk-agent")`，保持“在哪个路径启动，就在哪个路径工作”的语义。

**渐进式工具加载**：SDK 默认跟随 `settings.tool_loading`，当前默认 `progressive`。提示目录直接列出全部工具名称与基本描述，模型看到目录后调用 `GetTool` 查看并加载目标工具的完整参数；未加载工具不会被执行。

```python
from floodmind import Agent, ToolLoadingConfig

agent = Agent(
    llm=llm,
    tools=tools,
    tool_loading=ToolLoadingConfig(mode="progressive", max_search_results=8, max_loaded_tools=12),
)

# 兼容旧行为：每轮请求直接暴露全部工具 schema
legacy_agent = Agent(llm=llm, tools=tools, tool_loading=False)
```

模式说明：`eager` 每轮暴露全部工具 schema；`catalog` 不在系统提示词重复完整参数但请求仍带全部 schema；`progressive` 初始只暴露 core tools，`GetTool` 加载后下一轮才暴露目标工具 schema。

**模型配置解析（桌面端集成推荐）**：无需手动解析 `settings.json`，调用单一入口 `resolve_model()` 即可拿到完整的连接与参数：

```python
from floodmind import resolve_model, ModelClient, Agent

rm = resolve_model()                       # 默认激活模型；指定则 resolve_model(model_key="...")
llm = ModelClient(rm.api_key, rm.base_url, rm.id,
                  temperature=rm.temperature, max_tokens=rm.max_tokens,
                  provider=rm.provider)    # 显式指定服务商 → 自动绑定厂商 Pipeline
# rm.context_window 可直接用于自建记忆：DualMemory(context_window=rm.context_window)
# llm.pipeline.name 可内省当前路由到的厂商管线（"minimax"/"kimi"/"dashscope"/"deepseek"/"openai-compatible"）
# llm.pipeline.provider_id / model_id / base_url 记录本次路由上下文；conservative=True 表示仅模型名前缀命中，采用保守 OpenAI 请求方言
agent = Agent(llm=llm)
```

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

### Advanced: Native Runtime

`NativeFloodAgent` / `create_flood_agent` 仍可作为高级 runtime 入口，用于复用内置工具、MCP、Skill 和完整执行循环；普通 SDK 用户优先使用 `Agent`。

```python
from floodmind.agent.native.model_client import ModelClient
from floodmind.memory import DualMemory
from floodmind.agent.native.native_flood_agent import NativeFloodAgent

llm = ModelClient.from_settings()
memory = DualMemory(session_id="my-session", context_window=65536)
agent = NativeFloodAgent(llm_service=llm, memory=memory, session_id="my-session")

# 流式对话
for chunk in agent.stream("分析水位数据"):
    print(chunk.get("content", ""), end="")

# 单次执行
result = agent.run("生成水文报告")
```

### Legacy HTTP API

HTTP API 属于旧 Web 适配器迁移路径。新系统请优先直接嵌入 Python SDK；如迁移期仍需 HTTP 层，可参考 `floodmind/server/` 中的 legacy routes，并安装 `floodmind[legacy]`。

旧 Web 适配器曾提供以下端点：

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

在 `~/.floodmind/settings.json` 的 `providers` 下添加任意 OpenAI 兼容服务商：

```json
{
  "providers": {
    "custom": {
      "name": "自定义平台",
      "base_url": "https://api.your-provider.com/v1",
      "api_key": "密钥",
      "models": [
        {
          "id": "my-model",
          "name": "我的模型",
          "context_window": 8192,
          "default_max_tokens": 4096,
          "default_temperature": 0.3
        }
      ]
    }
  }
}
```

> **厂商 Pipeline 自动路由**：`ModelClient` 构造时按 base_url / provider id / 模型名前缀自动绑定厂商专属调用管线（`floodmind/agent/native/providers/`）。已内置 DashScope、DeepSeek、Kimi、MiniMax 四家——自动翻译思考开关方言（`enable_thinking` / `thinking.type` / `reasoning_split`）、按厂商位置解析流式 reasoning 与 usage、剥离 `<think>` 标签、剥离厂商禁传参数。未命中的服务商走 OpenAI 兼容兜底；聚合网关按模型名前缀命中时只启用解析适配（请求保持标准参数），不会向网关发送厂商方言。

---

## 配置说明

配置文件位于 `~/.floodmind/settings.json`，模板参考 `floodmind/config/settings_template.json`。

### 配置文件一览

`~/.floodmind/` 目录下的关键文件：

| 文件 | 说明 |
|------|------|
| `settings.json` | 主配置文件（仅 providers 服务商与模型目录，OpenCode 层级） |
| `mcp.json` | MCP Server 连接配置（独立管理，首次启动自动从旧 settings.json 迁移） |
| `search.json` | WebSearch 搜索引擎配置（API Key、URL、Provider） |
| `SOUL.md` | 智能体身份定义（首次启动自动生成，可直接编辑） |
| `AGENTS.md` | 全局行为规则与偏好约束（项目级放在工作目录下的 `AGENTS.md`） |

### OpenAI 兼容接口

FloodMind 支持任意兼容 OpenAI `/v1/chat/completions` 的 API。各服务商只需配 `base_url` + `api_key`：

```json
// DashScope（阿里云百炼）
{ "providers": { "dashscope": { "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "sk-...", "models": [{"id":"deepseek-v4-flash","context_window":65536}] } } }

// DeepSeek 官方
{ "providers": { "deepseek": { "base_url": "https://api.deepseek.com/v1", "api_key": "sk-...", "models": [{"id":"deepseek-chat","context_window":65536}] } } }

// OpenAI
{ "providers": { "openai": { "base_url": "https://api.openai.com/v1", "api_key": "sk-...", "models": [{"id":"gpt-4o","context_window":128000}] } } }

// MiniMax
{ "providers": { "minimax": { "base_url": "https://api.minimaxi.com/v1", "api_key": "sk-...", "models": [{"id":"MiniMax-M3","context_window":1000000}] } } }

// Ollama 本地模型
{ "providers": { "ollama": { "base_url": "http://localhost:11434/v1", "models": [{"id":"llama3","context_window":8192}] } } }
```

> 不需要 `api_key` 的平台（如 Ollama 本地）可省略该字段。

### Provider 配置（完整字段）

```json
{
  "providers": {
    "<provider-id>": {
      "name": "显示名称",
      "base_url": "API 地址",
      "api_key": "密钥",
      "models": [
        {
          "id": "<model-id>",
          "name": "模型显示名",
          "description": "描述",
          "context_window": 65536,
          "default_max_tokens": 65536,
          "default_temperature": 0.3,
          "supports_reasoning": true,
          "supports_vision": false
        }
      ]
    }
  }
}
```

### 配置项说明

`settings.json` 采用 OpenCode 层级，**只暴露 `providers`**。其余为代码默认：

| 项 | 说明 | 默认 |
|------|------|--------|
| `providers.<id>.{base_url,api_key}` | 服务商连接 | — |
| `providers.<id>.models[].id` | 模型标识 | — |
| `providers.<id>.models[].context_window` | 模型上下文窗口（记忆窗口取此值） | 32768 |
| `providers.<id>.models[].default_max_tokens` | 默认最大输出 token | 8192 |
| `providers.<id>.models[].default_temperature` | 默认采样温度 | 0.3 |
| 激活模型 | 默认 catalog 第一个，界面切换为会话级 | 第一个 |
| 最大轮次 | auto-compact + DOOM LOOP 兜底 | 999 |
| 经验系统 | 始终开启 | 开 |

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
│   ├── agent/                     # Agent 编排核心
│   │   ├── native/                #   Native Agent Runtime
│   │   │   ├── native_flood_agent.py  # Agent 主体（双 registry、MCP/Skill、流式）
│   │   │   ├── executor.py        #     状态机 LLM↔Tool 循环
│   │   │   ├── model_client.py    #     统一 LLM 服务
│   │   │   ├── providers/         #     厂商 Pipeline（dashscope/deepseek/kimi/minimax + OpenAI 兜底，自动路由）
│   │   │   ├── model_router.py    #     模型路由/降级
│   │   │   ├── event_bus.py       #     EventBus + StepEventBus
│   │   │   ├── message_builder.py #     消息组装
│   │   │   ├── tool_runtime.py    #     AgentTool→ToolSpec 桥接
│   │   │   ├── context_compressor.py #  上下文压缩
│   │   │   ├── artifact_watcher.py #    产物检测
│   │   │   ├── background_review.py #   后台对话回顾
│   │   │   └── types.py           #     数据类型定义
│   │   ├── runtime/               #   Runtime 服务
│   │   │   ├── contracts/         #     数据契约（tools, messages, events, permissions, workspace）
│   │   │   ├── services/          #     工具执行/权限/询问/路径/检查点/日志/追踪/沙箱/工作区
│   │   │   └── adapters/          #     中性 runtime API 适配器；Flask/SSE 旧模块仅为 legacy shim
│   │   ├── mcp_client.py          #   MCP 客户端池 + build_mcp_tool_specs
│   │   ├── agent_registry.py      #   Agent 类型注册
│   │   ├── api.py                 #   Agent SDK 类（嵌入式 bare 模式入口）
│   │   └── scheduled_task_runtime.py  # 定时任务运行时
│   ├── config/                    # 全局配置
│   │   ├── settings.py            #   主配置模型
│   │   ├── search_config.py       #   WebSearch 配置
│   │   ├── model_presets.py       #   模型预设
│   │   └── settings_template.json #   初始模板
│   ├── server/                    # Web 后端模块化
│   │   ├── __init__.py            #   Flask create_app() 工厂
│   │   ├── agent_factory.py       #   Agent 创建/复用
│   │   ├── session_state.py       #   运行时状态（流控/中断/token）
│   │   ├── sanitize.py            #   SSE 脱敏
│   │   ├── config.py              #   常量 & 配置
│   │   ├── file_utils.py          #   文件工具 & 产物提取
│   │   └── routes/                #   Blueprint 路由（chat/sessions/files/models/memory/permission/checkpoints/tasks）
│   ├── profile/                   # 身份与提示词定制
│   │   ├── soul.py                #   SOUL.md 加载与种子
│   │   └── guidance.py            #   行为指导常量
│   ├── plugin/                    # Plugin 系统
│   │   ├── base.py                #   FloodmindPlugin 基类
│   │   └── loader.py              #   自动发现式 PluginLoader
│   ├── memory/                    # 记忆与经验系统
│   │   ├── dual_memory.py         #   扁平 _turns 对话历史
│   │   ├── experience_tree.py     #   经验树索引
│   │   ├── task_experience.py     #   任务经验
│   │   ├── session_manager.py     #   会话管理（含 worktree 隔离）
│   │   ├── session_store.py       #   SQLite 存储（SyncEvent）
│   │   └── skill_generator.py     #   经验→Skill 自动生成
│   ├── models/                    # 已弃用的 model 目录
│   ├── skills/                    # Skill 系统（10 个内置 Skill）
│   │   ├── base.py                #   Skill dataclass + 发现 + catalog
│   │   ├── registry.py            #   SkillRegistry 单例
│   │   ├── skill_curator.py       #   SkillCurator 生命周期
│   │   └── <skill-name>/          #   各 Skill 子目录（csv/docx/pdf/pptx/xlsx/...）
│   ├── tools/                     # Agent 工具层
│   │   ├── agent_tool.py          #   AgentTool + ToolRegistry
│   │   ├── base_tools.py          #   内置工具（GetSkill/Bash/Write/Read/...）
│   │   ├── file_tools.py          #   文件工具
│   │   └── memory_tools.py        #   记忆工具
│   ├── tui/                       # 终端 TUI (Textual)
│   └── cli.py                     # CLI 入口
├── contrib/                       # 已外置为 MCP 服务的脚本（chronos/hydro_case_client）
├── web/                           # React 前端
├── tests/                         # 633 passed / 1 legacy Web optional skip（v1.1.9 core-only 环境）
├── web_server.py                  # Flask 入口（日志 + SessionManager + waitress）
├── scheduler.py                   # 后台调度
├── main.py                        # CLI 交互入口
├── start.py                       # 统一启动入口
├── Dockerfile                     # Docker 构建
├── docs/                          # 文档
│   ├── DEVELOPER_GUIDE.md         #   二次开发指南
│   └── architecture/              #   架构 Wiki
├── pyproject.toml                 # 包配置
└── README.md
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
python -m pytest tests/ -q
# v1.1.9 core-only 验证结果：633 passed, 1 skipped
# skipped = legacy Web adapter 需要可选 floodmind[web] / Flask extra

# 前端构建（legacy Web，仅迁移期参考）
cd web && npm run build

# 新增 Skill（无需改代码）
mkdir skills/my-skill
echo -e '---\nname: my-skill\ndescription: ...\n---\n...' > skills/my-skill/SKILL.md
```
