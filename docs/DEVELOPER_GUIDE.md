# FloodMind SDK 开发指南 v3.1

> **更新**: 2026-08-06 — SDK v1.1.9；五项健壮性修复——① `exec_bash` 子进程关 stdin（裸 python/交互命令不再挂起）；② Bash 描述带 shell 类型 + stdin 已关；③ 完整模式注册宿主自定义 tools；④ 完整模式保留宿主 system_prompt；⑤ 未声明 permission_policy 回退 is_readonly（只读放行）。v1.1.7 彻底修复 MiniMax `tool id not found (2013)` 三层叠加根因——① 流式 tool call 空 id 时历史 id 不一致（fallback id 写回 accumulator）；② `ContextCompressor` 机械切尾部拆散工具调用原子组留下孤儿 tool（现按原子组对齐切分，head 至少保留首条 user）；③ `context_window` 误用全局默认模型窗口（现跟随注入模型 preset）。v1.1.6 移除 `SearchTools` 工具：工具发现与 skill 一致——`## 可用工具` 提示目录直接列出全部工具名称与基本描述，参数统一由 `GetTool` 查看并加载，模型无需搜索；移除工具输出静默字符截断（8000 字符 `_finalize_tool_output` 上限 + 1000 字符 journal 内联阈值），模型始终看到完整工具结果，上下文由 token 级 `ContextCompressor` 兜底；`short_description` 剥离 `[必填]/[可选]` 参数提示前缀。v1.1.5 含四项健壮性/权限收敛：① 工具调用参数键名统一清洗（模型偶发畸形键如 `{"tool_name"": ...}` 不再 `**kwargs` 崩）；② exec 命令体写目标检查（`>`/`Set-Content`/`Copy-Item` 等越权写 DENY，堵住"只读授权被 Bash 绕过"）；③ folder-first 读白名单加入已装 skill 注册表；④ PathService 读取拒绝原因附可操作引导。v1.1.4 含 create() 连接阶段 LLM 流式重试

FloodMind 正在收敛为 **Python SDK + 最小 CLI run**：开发者通过 `Agent`、`ModelClient`、`Workspace`、`build_agent_tool`、Provider Pipeline、MCP 与 Skill API 将能力嵌入自己的平台、桌面助手或业务系统。Web / TUI 代码仅作为迁移期 legacy adapter 保留，不再是 SDK 核心公共面。

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
4. [Legacy HTTP Adapter](#4-legacy-http-adapter)
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
   - 8.1 [厂商 Pipeline（调用方言自动路由）](#81-厂商-pipeline调用方言自动路由)
9. [Legacy Web/TUI 迁移说明](#9-legacy-webtui-迁移说明)
10. [Plugin 系统开发](#10-plugin-系统开发)
11. [测试与调试](#11-测试与调试)
12. [项目结构参考](#12-项目结构参考)

---

## 1. 架构概述

```
┌──────────────────────────────────────────────────────┐
│                   SDK 宿主入口层                      │
│  Python App │ Desktop Assistant │ Platform Service │ CLI run │
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
| **ModelClient** | `floodmind/agent/native/model_client.py` | 统一的 LLM 服务接口（stream_chat / chat / invoke），构造时经 `route_pipeline()` 自动绑定厂商 pipeline |
| **Provider Pipelines** | `floodmind/agent/native/providers/` | 厂商调用方言适配（dashscope/deepseek/kimi/minimax + OpenAI 兜底），详见 §8.1 |
| **Tool Loading** | `floodmind/agent/native/tool_loading.py` | 渐进式工具目录、`GetTool` 按需 schema 加载、未加载工具 fail-closed |
| **Workspace / Harness Paths** | `floodmind/agent/runtime/contracts/workspace.py`, `floodmind/agent/runtime/services/workspace_service.py`, `floodmind/agent/runtime/services/path_service.py` | Harness 级工作区、cwd-first 路径解析、Web session 兼容与 folder-first `.floodmind` 收纳；所有 path/cwd/workdir 统一经 PathService 判权 |
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
- NVIDIA GPU（可选，时序预测加速）

### 源码安装

```bash
git clone <仓库地址> floodmind
cd floodmind
# 安装 SDK 核心（不安装 Web/TUI 旧栈）
pip install -e .

# 如果仍使用 requirements.txt，它同样只代表 SDK/core 默认依赖
pip install -r requirements.txt

# 可选能力
pip install "floodmind[doc]"       # 文档处理
pip install "floodmind[gpu]"       # GPU/时序预测相关能力
pip install "floodmind[legacy]"    # 迁移期旧 Web/TUI 适配器
```

### 配置

配置文件位于 `~/.floodmind/` 目录下，按职责分为独立文件：

| 文件 | 说明 |
|------|------|
| `settings.json` | 主配置（仅 `providers` 服务商与模型目录，OpenCode 层级） |
| `mcp.json` | MCP Server 连接配置（独立管理） |
| `search.json` | WebSearch 搜索引擎配置 |
| `SOUL.md` | 智能体身份定义 |
| `AGENTS.md` | 全局行为规则 |

首次启动自动创建模板。最小配置示例（DashScope）——**配置最小化**，只暴露服务商目录：

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

> - **激活模型**默认 = catalog 第一个；界面切换属会话级，不写回配置。
> - **记忆窗口**取自当前模型 `context_window`；**最大轮次**默认 999（auto-compact 兜底）；**经验系统**始终开启——均不入配置。

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
# provider 可选：显式指定服务商以精确路由厂商 pipeline（§8.1）；
# 不传则按 base_url/模型名自动推断
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
| `permission_handler` | `Callable[[str, dict], bool]` | `None` | 工具审批钩子（同步 allow/deny，bare 与 full runtime 均生效） |
| `permission_decision_hook` | `Callable[[str, dict, PermissionDecision, ToolPermissionPolicy], PermissionDecision]` | `None` | host-level 权限决策钩子：SDK 基础判断后调整最终决策（只能收紧不能放开），见下文 |
| `max_iterations` | `int` | `999` | 最大循环轮数 |
| `workspace` | `Workspace` | `None` | 工作区对象。嵌入式宿主（桌面端）可显式注入；未传时 SDK 默认构造 `Workspace.from_cwd(session_id="sdk-agent")`，保持启动目录即工作区。构造时或通过 `bind_workspace()` 传入均可。 |
| `tool_loading` | `ToolLoadingConfig\|bool\|None` | `None` | 工具加载策略；`None` 使用 settings 默认，`False` 为 eager 旧行为，`True` 为默认 progressive，或传入自定义 `ToolLoadingConfig` |
| `bare` | `bool` | `True` | 是否裸嵌入模式。`True` 仅注册自定义工具；`False` 走完整 runtime（内置工具/MCP/Skill/权限 ASK/workspace），见下文 |

**结果访问：** 每次 `run()`/`stream()` 后自动重置。

| 属性 | 类型 | 说明 |
|------|------|------|
| `agent.last_usage` | `dict` | token 用量（`prompt_tokens`/`completion_tokens`/`total_tokens`） |
| `agent.artifacts` | `list[dict]` | `file_generated`/`image_generated` 事件 |
| `agent.raw` | `NativeFloodAgent` | 底层实例（高级用法） |

**工作区注入（SDK / 桌面端嵌入）：**

```python
from floodmind import Agent, Workspace

# 推荐：显式把用户打开/启动的目录作为 folder-first 工作区
ws = Workspace.from_folder("E:/MyProject", session_id="sess-1")
agent = Agent(llm=llm, tools=tools, workspace=ws)

# 运行期切换（线程安全，任意线程调用）
agent.bind_workspace(ws)
```

Folder-first 模式下，相对路径默认相对 `ws.default_cwd`，FloodMind 内部状态收纳到
`E:/MyProject/.floodmind/`。Web 服务仍保持 `data/sessions/<sid>/outputs` 兼容布局；SDK
未显式传入 workspace 时默认使用 `Workspace.from_cwd(session_id="sdk-agent")`。所有工具的 path/cwd/workdir 都应经 `PathService` 解析；文件副作用统一经过 `PermissionService`。工作区外目录需通过 `readable_roots` / `writable_roots` 显式授权。

> `bind_workspace()` 存为普通实例属性（非 contextvar），确保 SDK 内部子线程（`_run_loop`）不受
> 宿主线程上下文影响。桌面端 sidecar 推荐使用此 API 替代模块级 `set_workspace()`。

**Host-level 权限决策钩子（permission_decision_hook）：**

`permission_handler` 只能做同步 allow/deny；需要“把 SDK 默认放行的写/执行类调用升级为交互确认（ASK）”
这类产品策略时，使用 `permission_decision_hook`。它在 SDK 完成基础权限判断**之后**调用，收到 SDK 原始
决策与该工具的 `permission_policy`，返回最终决策：

```python
from floodmind import Agent
from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior,
    PermissionDecision,
)

def desktop_permission_hook(tool_name, tool_input, sdk_decision, permission_policy):
    # SDK 的安全拒绝（路径越界/危险命令/子代理分层/planning 硬门）必须保留
    if sdk_decision.behavior == PermissionBehavior.DENY:
        return sdk_decision
    # SDK 已要求确认的保持确认
    if sdk_decision.behavior == PermissionBehavior.ASK:
        return sdk_decision
    # 只读/内部工具直接放行
    policy_type = getattr(permission_policy, "policy_type", None)
    if policy_type in {"readonly", "internal", "read_path"}:
        return sdk_decision
    # 产品策略：其余非只读调用一律交互确认（走 permission_ask 事件）
    return PermissionDecision(
        behavior=PermissionBehavior.ASK,
        reason=f"需要用户确认此操作（{tool_name}）",
    )

agent = Agent(llm=llm, tools=tools, permission_decision_hook=desktop_permission_hook)
```

约束与语义：

- 钩子只能**收紧**不能放开：`DENY` 不可被改成 `ALLOW/ASK`，`ASK` 不可被改成 `ALLOW`；
  试图放宽时 SDK 忽略钩子结果并记录告警，保证 SDK 安全判断不被宿主绕过。
- 钩子抛异常或返回非法值（无 `behavior` 字段）时 fail-safe：保留 SDK 原决策，不影响执行。
- 钩子升级为 `ASK` 时，SDK 通过 `AskService` 发射 `permission_ask` 事件并在
  `awaiting_permission` 状态等待；宿主收到事件后经 `AskService.respond()`（或桌面端自己的
  `respond_to_permission_ask` 封装）批准/拒绝，执行循环自动续上。bare 与 full runtime 均支持。
- 最终决策在 tracing 记录前生效，日志与实际行为一致。
- 桌面端可用该钩子替代对 `_orchestrator_registry` / `ToolSpec.check_permissions_fn` 的 monkey patch。

**公共 Agent 完整 runtime 与桌面能力（v1.1.0 起，v1.1.9 前均为该清单）：**

1. **`Agent(..., bare=False)` 完整 runtime**：`bare` 默认 `True`（裸嵌入，仅自定义工具）；`False` 走
   NativeFloodAgent 完整 runtime（内置工具、MCP、Skill、权限 ASK、workspace 绑定）。完整 runtime 下
   `tools=None` 保留原生默认工具集。验证：`agent.raw._orchestrator_registry.all()` 含内置工具。

2. **兼容代理**（避免宿主访问 `raw` 内部）：
   - `agent.memory` → 底层 memory 对象（`append_instruction` 等只读/操作用）
   - `agent.session_id` → 底层会话 ID
   - `agent.clear_memory()` → 清空底层会话记忆

3. **`agent.stream(msg, **kwargs)` 透传**：`abort_check` / `attachments` / `resume_session_id` 直达底层
   `NativeFloodAgent.stream`，宿主可中断或传附件。

4. **MCP tool-name sanitize**：`build_mcp_tool_specs` 对 model-visible 工具名统一
   `re.sub(r"[^a-zA-Z0-9_-]+", "_", f"mcp:{server}:{tool}")`（如 `mcp:hydro-rag:search:docs` →
   `mcp_hydro-rag_search_docs`），满足 OpenAI 兼容端点 `^[a-zA-Z0-9_-]+$`；bound function 仍向
   `call_tool_fn` 传原始冒号全名。断开清理用 `mcp_tool_prefix(server)` 前缀（与 sanitize 名对齐）。

5. **MCP stdio liveness**：`McpClientConnection.is_connected` 对 stdio 检查 `process.poll() is None`
   （进程退出即返回 `False`）；SSE 无廉价探针，best-effort。

6. **MCP call health**：`McpClientPool.call_tool` 记录最近一次每 server 结果；
   `pool.call_health()` 返回 `{name: {"ok": bool, "error": str|None}}`（未连接/异常/失败标记 → `ok=False`），
   供 UI 展示"调用异常"。

7. **MCP server-connected listener**：`pool.add_server_connected_listener(listener)` 在连接成功且入池后触发
   `listener(server_config, conn)`（异常不阻断，幂等注册）；`remove_server_connected_listener` 移除。
   宿主可据此感知 `LoadMcpServer` 触发的运行时连接。

8. **`_build_model_info` 读取宿主路由模型**：优先 `self._model_client.model_name`（切换模型后提示与实际
   路由一致），无则回退 SDK 默认模型解析。

**v1.1.5 健壮性 / 权限收敛（桌面反馈）：**

1. **工具调用参数键名统一清洗**：`ToolExecutionService` 在权限/校验/执行之前把模型生成的参数键名
   归一化（去边缘引号/空白、去键内控制符与引号、丢弃空键）。MiniMax-M3 等模型偶发畸形键
   （如 `{"tool_name"": "..."}` 键带尾引号）此前会让无 pydantic args_schema 的工具（`GetTool`、
   系统工具、MCP 工具）直接 `**kwargs` 崩成 `TypeError: unexpected keyword argument`；
   现在清洗后正常执行。防御纵深：`TOOL_EXECUTION_ERROR` 对 `unexpected keyword argument` 明示
   "参数名可能有多余引号/空白"，让模型能自纠。

2. **exec 命令体写目标检查**（`floodmind/agent/runtime/services/exec_write_scanner.py`）：`exec_bash` 在
   命令体内执行的 `>`/`>>` 重定向与 PowerShell `Set-Content`/`Out-File`/`New-Item`/`Copy-Item`/
   `Move-Item`/`Remove-Item`/`Set-Item` 等写操作的目标路径，逐个按 `write` 权限解析；不在允许写目录
   内即 `DENY`（堵住"只读授权被 Bash 绕过"漏洞）。保守原则：只认"像绝对/限定路径"的写目标，相对工作
   区内文件名自然可写、字符串字面量里的 `>`/cmdlet 文本不误判；无法静态解析的（如 `$变量` 持绝对
   路径）fail-open，宿主可经 `permission_decision_hook` 收紧。

3. **folder-first 读白名单加入已装 skill 注册表**：`PathService` 允许读 `SkillRegistry` 的发现根 +
   `site-packages/skills`（独立安装的 skill 包），agent 可直接读已装 skill 的 `SKILL.md`/`references/`/
   `scripts/` 源文件，避免"读取已装 skill 反复被拒"的死循环重试。只放开读、不影响写。

4. **PathService 读取拒绝原因附可操作引导**：拒绝文案追加"如为工作区外文件，请先在工作区附件中引用
   该文件以完成授权"。

**渐进式工具加载：**

```python
from floodmind import Agent, ToolLoadingConfig

agent = Agent(
    llm=llm,
    tools=tools,
    tool_loading=ToolLoadingConfig(
        mode="progressive",
        max_search_results=8,
        max_loaded_tools=12,
    ),
)

# 兼容旧行为：每轮请求直接携带全部工具 schema
legacy_agent = Agent(llm=llm, tools=tools, tool_loading=False)
```

| 模式 | 行为 |
|------|------|
| `eager` | 每轮请求暴露全部工具 schema，兼容旧行为 |
| `catalog` | 提示词不重复列完整工具参数，但请求仍暴露全部工具 schema |
| `progressive` | 初始只暴露 `GetTool` / `GetSkill` 等 core tools；提示目录列出全部工具名称与基本描述，模型读目录后调用 `GetTool` 查看并加载目标工具，下一轮请求才携带该工具 schema；未加载工具 fail-closed |

完整配置可写入 `settings.json` 的高级 `tool_loading` 块，也可在 SDK 构造时显式传入；显式参数优先。

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

### 3.7 Checkpoint 与恢复语义

Checkpoint 在 SDK v1.1.9 中只表示 **Agent runtime state**，用于断点恢复执行状态，不负责复制或回滚 workspace 文件。

当前 checkpoint 目录只包含：

```text
<state-root>/sessions/<sid>/checkpoints/<ckpt>/
  state.json
  manifest.json
```

关键约束：

- `CheckpointService.save(state, metadata=None)` 不再接受 `files_dirs` 或任何文件快照参数。
- `NativeAgentExecutor` 不会在工具调用前复制 workspace、artifact、uploads 或 `.floodmind`。
- `manifest.files_snapshot_dir` / `files_snapshot_base_dirs` 仅为读取历史 manifest 的 legacy 字段，新 checkpoint 固定为空。
- `rollback_files()` 只保留为 legacy reader/no-op compatibility；文件回滚、备份或 artifact versioning 若需要，应由独立 change journal / artifact versioning 能力实现。

---

## 4. Legacy HTTP Adapter

HTTP API 属于旧 Web 适配器迁移路径。SDK 核心不依赖 `floodmind.server`、Flask 或 React 前端；新系统应优先直接嵌入 Python SDK，再由宿主自行暴露 HTTP / WebSocket / 桌面 UI。

如迁移期仍需旧 HTTP 层，请安装 legacy extra，并参考 `floodmind/server/` 中的 routes。`floodmind serve` 当前只输出 legacy 提示，不再作为核心启动路径。

旧适配器曾提供以下端点：
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

### 5.4 安装现成 MCP Server

如果已有 MCP Server 代码目录：

```bash
cp -r <来源目录> <项目根>/mcp/<server-name>/
pip install -r <项目根>/mcp/<server-name>/requirements.txt
```

然后接入：写 `mcp.json`（持久）或调 `LoadMcpServer`（热插拔）。Agent 自身的 `mcp-builder` skill 中有完整指引。

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

Skill 加载时自动进行威胁扫描（`scan_content_threats`）。11 个内置 Skill：chronos、csv、data-analysis、doc-coauthoring、docx、mcp-builder、pdf、plotting、pptx、skill-creator、xlsx。

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

### 6.6 安装现成 Skill

如果用户提供了已有 Skill 目录（含 SKILL.md + scripts/ 等），直接复制到 writable_root 并刷新：

```bash
cp -r <来源目录> <项目根>/skills/<skill-name>/
```

然后 Agent 调 `RefreshSkills` 使其立即生效，调 `GetSkill(name)` 验证。Agent 自身的 `skill-creator` skill 中有完整指引。

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

在 `~/.floodmind/settings.json` 的 `providers` 下添加服务商（OpenCode 层级）：

```json
{
  "providers": {
    "my-provider": {
      "name": "我的模型平台",
      "base_url": "https://api.my-platform.com/v1",
      "api_key": "sk-xxx",
      "models": [
        {
          "id": "my-model",
          "name": "我的模型",
          "context_window": 8192,
          "default_max_tokens": 8192,
          "default_temperature": 0.3,
          "supports_reasoning": true
        }
      ]
    }
  }
}
```

Python 中使用——**推荐 `resolve_model()` 单一入口**（桌面端集成无需手解析配置）：

```python
from floodmind import resolve_model, ModelClient

# resolve_model() 返回完整连接+参数（默认激活模型；指定则 resolve_model(model_key="my-model"))
rm = resolve_model(model_key="my-model")
llm = ModelClient(rm.api_key, rm.base_url, rm.id,
                  temperature=rm.temperature, max_tokens=rm.max_tokens,
                  provider=rm.provider)   # 显式服务商 → 精确路由厂商 pipeline（§8.1）

# 或沿用便捷工厂（内部同样走 resolve_model）
llm = ModelClient.from_settings_with_preset("my-model")

# 直接用连接信息（跳过配置）
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

### 8.1 厂商 Pipeline（调用方言自动路由）

各家模型虽都宣称 OpenAI 兼容，但思考开关参数、reasoning 字段位置、usage 位置、多模态 block 各有方言。`ModelClient` 构造时通过 `route_pipeline(provider, model_name, base_url)` 自动绑定厂商专属 pipeline（`floodmind/agent/native/providers/`），之后不再感知厂商差异：

```python
llm = ModelClient(api_key=..., base_url="https://api.minimaxi.com/v1",
                  model_name="MiniMax-M3", provider="minimax")
llm.pipeline.name        # "minimax" —— 自动路由
llm.pipeline.provider_id # "minimax" —— 传入的 provider 上下文
llm.pipeline.model_id    # "MiniMax-M3" —— 传入的模型名
llm.pipeline.base_url    # "https://api.minimaxi.com/v1"
```

路由优先级：**base_url 精确(100) > provider id(60) > 模型名前缀(40) > OpenAI 兜底**。
仅模型名前缀命中（如聚合网关托管 `MiniMax/xxx`）时 pipeline 进入 `conservative` 模式：
解析适配全部启用，请求适配退化为标准 OpenAI 参数，避免网关不认厂商方言。
`provider_id` / `model_id` / `base_url` 会保存在 pipeline 实例上，便于 SDK 调用方做日志、自检和诊断。

已内置 pipeline：

| Pipeline | 请求适配 | 解析适配 |
|---|---|---|
| `dashscope` | `enable_thinking`；`MiniMax/` 模型用 `thinking.type`；`max_completion_tokens`；思考态 tool_choice 降级 | `reasoning_content`；顶层 usage |
| `deepseek` | `thinking.type`；思考态剥离 temperature/top_p | `reasoning_content`；顶层 usage |
| `kimi` | 按代际分支（k3 无开关 / k2.7 强制思考 / k2.6 `thinking.keep`）；k 系列剥离 temperature | `choices[0].usage` 优先；公网 URL 图片早失败 |
| `minimax` | `thinking.type` + `reasoning_split`；M2.x 不可关；temperature 钳制 [0,2] | `reasoning_details` 累积式差分；`<think>` 标签流式剥离 |
| `openai-compatible`（兜底） | 仅 `stream_options` | 标准字段 |

**新增一家厂商**：在 `providers/` 下新建子类并注册到 `__init__.py` 的 `_PIPELINES`：

```python
from .base import ProviderPipeline

class MyProviderPipeline(ProviderPipeline):
    name = "my-provider"

    @classmethod
    def match(cls, provider_id, model_id, base_url):
        if "my-platform" in (base_url or "").lower():
            return 100
        if (provider_id or "").lower() == "my-provider":
            return 60
        return 0

    def prepare_request(self, params, *, enable_thinking, stream):
        params = super().prepare_request(params, enable_thinking=enable_thinking, stream=stream)
        if self.conservative:
            return params
        # 厂商方言注入（一律 setdefault，显式 extra_body 优先）
        ...
        return params
```

思考开关对上层只是一个语义位 `ModelClient.enable_thinking`——`prepare_request` 负责把它翻译成厂商参数；调用方显式传的 `extra_body` 永远优先（pipeline 用 `setdefault` 注入）。

---

## 9. Legacy Web/TUI 迁移说明

Web/TUI 已从 SDK 核心路线隔离：

- `floodmind.server`、`web_server.py`、`web/`、`floodmind.tui` 只作为 legacy adapter/source-tree compatibility 保留。
- SDK 核心模块不得 import legacy adapter；依赖方向只能是 legacy adapter 调用 SDK。
- 基础安装不再要求 Flask、Textual、React/Vite 或 Web server 依赖。
- 新 UI 应直接消费 `Agent.stream()` 事件协议，自行映射到桌面端、WebSocket、HTTP SSE 或平台消息总线。
- `floodmind web` / `serve` / `tui` / `chat --web` / `chat --tui` 仅输出 legacy notice，不启动旧 UI，也不导入 Flask/Textual。

旧前端开发命令仅供 legacy 参考：

```bash
cd web && npm install
npm run build
```

---

## 10. Plugin 系统开发

Plugin 是比 Skill 更强大的 Python 代码扩展机制，可直接注册工具到 Agent、hook 事件。

### 10.1 创建 Plugin

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

### 10.2 Plugin 发现与加载

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

### 10.3 Plugin / Skill / MCP 对比

| 扩展方式 | 编写难度 | 能力 | 适用场景 |
|---------|--------|------|---------|
| **Skill** | 零代码（SKILL.md） | 指令 + 脚本 | 领域知识、工作流模板 |
| **Plugin** | Python 代码 | 工具 + hook + Agent 配置 | 深度集成、自定义逻辑 |
| **MCP** | 独立进程 | 跨语言、标准化协议 | 外部服务、多 Agent 共享 |

---

## 11. 测试与调试

### 11.1 SDK Agent 测试

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

### 11.2 工具测试

`AgentTool` → `ToolSpec` 转换可独立测试：

```python
from floodmind import AgentTool
from floodmind.agent.runtime.contracts.tools import ToolSpec

tool = AgentTool(name="TestTool", description="d", func=lambda: "ok")
spec = tool.to_tool_spec()
assert isinstance(spec, ToolSpec)
assert spec.name == "TestTool"
```

### 11.3 Skill 系统测试

参考 `tests/test_skill_registry.py`（9 tests）和 `tests/test_skill_curator.py`（17 tests）：

```python
from floodmind.skills.registry import get_skill_registry, SkillRegistry
from pathlib import Path

# 隔离测试：自定义 roots
reg = SkillRegistry(roots=[Path("/tmp/test_skills")], writable_root=Path("/tmp/test_skills"))
assert len(reg.list_skills()) == 0
```

### 11.4 运行全部测试

```bash
pytest tests/ -q          # v1.1.9 core-only: 613 passed, 1 skipped
pytest tests/test_sdk_agent.py -v   # SDK 相关
pytest tests/test_skill_registry.py tests/test_skill_curator.py -v  # Skill 系统
pytest tests/test_sdk_purity.py -q  # SDK import/package purity
```

---

## 12. 项目结构参考

```
FloodMind/
├── floodmind/                        # Python 主包
│   ├── agent/                        # Agent 核心
│   │   ├── native/                   #   Native Agent Runtime
│   │   │   ├── native_flood_agent.py #     Agent 主体（双 registry、MCP/Skill 管理、流式）
│   │   │   ├── executor.py           #     状态机 LLM↔Tool 循环
│   │   │   ├── model_client.py       #     统一 LLM 服务
│   │   │   ├── providers/            #     厂商 Pipeline（base/dashscope/deepseek/kimi/minimax/openai_compatible + route_pipeline）
│   │   │   ├── tool_loading.py       #     渐进式工具目录与按需 schema 加载
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
│   │   │   └── adapters/             #     中性 runtime API 适配器；Flask/SSE 旧模块为 legacy shim
│   │   ├── mcp_client.py             #   MCP 客户端池 + build_mcp_tool_specs
│   │   ├── agent_registry.py         #   Agent 类型注册（build/plan/general/explore）
│   │   ├── api.py                    #   Agent SDK 类
│   │   └── task_runtime.py           #   任务运行时
│   ├── config/                       # 配置
│   ├── server/                       # Web 后端模块化
│   │   ├── __init__.py               #   Flask create_app() 工厂
│   │   ├── agent_factory.py          #    Agent 创建/复用 (get_or_create_agent)
│   │   ├── session_state.py          #   运行时状态 (流控/中断/token)
│   │   ├── sanitize.py               #   SSE 脱敏
│   │   ├── config.py                 #   常量&配置
│   │   ├── file_utils.py             #   文件工具&产物提取
│   │   └── routes/                   #   Blueprint 路由
│   │       ├── chat.py               #     聊天 SSE 流式
│   │       ├── sessions.py           #     会话 CRUD
│   │       ├── files.py              #     文件上传/产物
│   │       ├── models.py             #     模型配置
│   │       ├── memory.py             #     记忆读写
│   │       ├── permission.py         #     权限审批
│   │       ├── checkpoints.py        #     检查点
│   │       └── tasks.py              #     定时任务
│   ├── profile/                      # 身份与提示词
│   ├── memory/                       # 记忆与经验
│   │   ├── dual_memory.py            #   扁平 _turns 对话历史 + 压缩
│   │   ├── experience_tree.py        #   经验树索引
│   │   ├── task_experience.py        #   任务经验
│   │   ├── session_manager.py        #   会话管理（含 worktree 隔离）
│   │   ├── session_store.py          #   SQLite 存储
│   │   └── skill_generator.py        #   经验→Skill 自动生成
│   ├── skills/                       # Skill 系统（发现/注册/策展）
│   │   ├── base.py                   #   Skill dataclass + 发现 + catalog
│   │   ├── registry.py               #   SkillRegistry 单例
│   │   └── skill_curator.py          #   SkillCurator 生命周期
│   ├── tools/                        # Agent 工具层
│   │   ├── agent_tool.py             #   AgentTool + ToolRegistry + build_agent_tool
│   │   ├── base_tools.py             #   内置工具（GetSkill/Bash/WebSearch/...）
│   │   ├── file_tools.py             #   文件工具
│   │   └── memory_tools.py           #   记忆工具
│   ├── plugin/                       # Plugin 系统
│   ├── tui/                          # 终端 TUI (Textual)
│   ├── cli.py                        # CLI 入口（floodmind 命令）
│   └── __init__.py                   # top-level SDK 导出
├── contrib/                           # 已外置为 MCP 服务的脚本（chronos 等）
├── web/                              # React 19 + TypeScript 前端
├── web_server.py                     # Flask 入口（日志 + SessionManager + waitress）
├── scheduler.py                      # 定时任务调度
├── tests/                            # 测试（v1.1.9 core-only: 613 passed, 1 skipped）
├── docs/                             # 文档
│   ├── DEVELOPER_GUIDE.md            #   本文档
│   └── architecture/                 #   架构 Wiki
│       ├── OVERVIEW.md               #     架构知识图谱
│       ├── ASSESSMENT.md             #     系统评估（已完成 vs 待处理）
│       ├── MCP_ARCHITECTURE.md       #     MCP 子系统详解
│       ├── SKILL_ARCHITECTURE.md     #     Skill 统详解
│       └── D_STORAGE_PROPOSAL.md     #     存储提案
├── pyproject.toml                    # 包配置
└── start.py                          # 统一启动入口
```

---

## 更多资源

- **架构 Wiki**: `docs/architecture/OVERVIEW.md` — 完整知识图谱 + MCP/Skill/Tool 架构详解
- **系统评估**: `docs/architecture/ASSESSMENT.md` — 已完成批次 vs 待处理项
- **README**: 项目概述、快速开始、CLI 参考
- **settings 模板**: `floodmind/config/settings_template.json`
- **SDK 测试参考**: `tests/test_sdk_agent.py` — 44 个 SDK 用例
- **workspace 测试参考**: `tests/test_native_agent_workspace.py` — 跨线程 contextvar 修复验证
