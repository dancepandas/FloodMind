"""语义层：子系统节点与依赖边。

结构图回答“哪个文件 import 了哪个文件”，语义图回答“系统由哪些职责边界
组成、它们之间是什么关系”。这里维护唯一的人工约定源。
"""

from __future__ import annotations

from typing import Any

SUBSYSTEMS: tuple[dict[str, Any], ...] = (
    {
        "id": "sdk",
        "title": "SDK 公共入口",
        "layers": ["sdk"],
        "paths": ["floodmind/__init__.py", "floodmind/agent/api.py", "floodmind/agent/sdk_events.py", "floodmind/agent/agent_registry.py"],
        "responsibility": "宿主集成边界：创建 Agent、注入模型/工具/工作区，暴露 run/stream/chat/resume 与 committed 事件。",
    },
    {
        "id": "cli-host",
        "title": "CLI 与宿主入口",
        "layers": ["cli"],
        "paths": ["main.py", "floodmind/cli.py", "floodmind/cli_interactive.py"],
        "responsibility": "命令行任务、交互式终端、定时任务工具入口。",
    },
    {
        "id": "agent-core",
        "title": "Agent 执行核心",
        "layers": ["agent-core"],
        "paths": ["floodmind/agent/native/"],
        "responsibility": "显式状态机执行器、NativeFloodAgent 装配、消息/提示词构建、流式消费、压缩、工具加载、后台回顾。",
    },
    {
        "id": "runtime-contracts",
        "title": "运行时契约",
        "layers": ["runtime-contracts"],
        "paths": ["floodmind/agent/runtime/contracts/"],
        "responsibility": "Canonical Event/Part/Message、RunState、ToolSpec、权限/审批、Checkpoint、Sandbox、ChildThread、Workspace 等稳定数据契约。",
    },
    {
        "id": "runtime-kernel",
        "title": "Reducer 与投影",
        "layers": ["runtime-kernel"],
        "paths": ["floodmind/agent/runtime/reducer.py"],
        "responsibility": "事件日志到 RunState 的确定性归约，以及历史/消息投影。",
    },
    {
        "id": "runtime-services",
        "title": "运行时服务",
        "layers": ["runtime-services"],
        "paths": ["floodmind/agent/runtime/services/"],
        "responsibility": "Journal 权威源、工具事务、权限/ASK、路径/工作区、Checkpoint/Resume、沙箱、后台任务、子线程、产物、追踪与对账。",
    },
    {
        "id": "runtime-adapters",
        "title": "运行时适配器",
        "layers": ["runtime-adapters"],
        "paths": ["floodmind/agent/runtime/adapters/"],
        "responsibility": "把运行时服务暴露为 Flask/SSE 等宿主协议。",
    },
    {
        "id": "memory",
        "title": "记忆与会话",
        "layers": ["memory"],
        "paths": ["floodmind/memory/", "floodmind/domain/memory/"],
        "responsibility": "Journal 投影出的会话历史、core memory、任务经验树、巩固/回顾、语义存储、会话隔离。",
    },
    {
        "id": "tools",
        "title": "工具层",
        "layers": ["tools"],
        "paths": ["floodmind/tools/"],
        "responsibility": "AgentTool/ToolSpec 双契约、内置文件/记忆/命令工具、会话上下文注入。",
    },
    {
        "id": "skills",
        "title": "Skill 体系",
        "layers": ["skills"],
        "paths": ["floodmind/skills/", "skills/"],
        "responsibility": "SkillRegistry/Curator、SKILL.md 发现与 CRUD、内置技能包。",
    },
    {
        "id": "mcp",
        "title": "MCP 集成",
        "layers": ["mcp"],
        "paths": ["floodmind/agent/mcp_client.py"],
        "responsibility": "MCP 客户端池、连接/工具发现/热插拔、ToolSpec 构造。",
    },
    {
        "id": "model",
        "title": "模型与配置",
        "layers": ["model-config"],
        "paths": ["floodmind/agent/native/model_client.py", "floodmind/agent/native/model_gateway.py", "floodmind/agent/native/providers/", "floodmind/config/"],
        "responsibility": "统一模型门面、供应商 Codec/方言、流式解码、usage/reasoning、能力表、模型解析与 settings、回退链与子代理路由。",
    },
    {
        "id": "extension",
        "title": "扩展与辅助",
        "layers": ["extension", "agent-services", "common"],
        "paths": ["floodmind/profile/", "floodmind/plugin/", "floodmind/agent/context_runtime.py", "floodmind/agent/guardrail.py", "floodmind/agent/handoff.py", "floodmind/common/"],
        "responsibility": "人格/指导语、插件、上下文块、输入输出护栏、控制权移交与通用工具。",
    },
    {
        "id": "gateway",
        "title": "Gateway（HTTP/SSE 适配）",
        "layers": ["gateway"],
        "paths": ["floodmind/gateway/"],
        "responsibility": "活跃的 FastAPI/SSE 网关与内置单文件 UI，可选 Chainlit；进程内调用 SDK Agent。",
    },
    {
        "id": "tests",
        "title": "测试体系",
        "layers": ["tests"],
        "paths": ["tests/"],
        "responsibility": "按 P0-P8 契约、运行时服务、记忆、工具、模型供应商组织的回归套件。",
    },
    {
        "id": "evals",
        "title": "内部评测集",
        "layers": ["evals"],
        "paths": ["evals/"],
        "responsibility": "脚本化模型轨迹驱动真实执行器，覆盖冒烟、真实工具与安全/契约回归，作为 CI 门禁。",
    },
    {
        "id": "benchmarks",
        "title": "收益基线",
        "layers": ["benchmarks"],
        "paths": ["benchmarks/"],
        "responsibility": "single-agent 与 delegated 两种策略的对照任务集与判定规则，默认离线运行，--live 才调用模型。",
    },
    {
        "id": "tooling",
        "title": "工程脚本",
        "layers": ["tooling"],
        "paths": ["scripts/"],
        "responsibility": "冒烟、端到端验证与知识图谱生成。",
    },
)


EDGES: tuple[dict[str, str], ...] = (
    {"src": "sdk", "dst": "agent-core", "kind": "delegates", "note": "Agent 薄封装 NativeFloodAgent"},
    {"src": "sdk", "dst": "model", "kind": "uses", "note": "ModelClient 注入"},
    {"src": "sdk", "dst": "memory", "kind": "uses", "note": "会话/记忆门面"},
    {"src": "sdk", "dst": "tools", "kind": "uses", "note": "宿主工具注册"},
    {"src": "sdk", "dst": "skills", "kind": "uses", "note": "Skill roots 与 registry"},
    {"src": "cli-host", "dst": "sdk", "kind": "uses", "note": "命令行走 Agent"},
    {"src": "cli-host", "dst": "agent-core", "kind": "uses", "note": "直接构造运行时"},
    {"src": "gateway", "dst": "sdk", "kind": "uses", "note": "网关进程内构造 Agent"},
    {"src": "gateway", "dst": "agent-core", "kind": "uses", "note": "进程内消费 Agent.stream 事件"},
    {"src": "agent-core", "dst": "runtime-contracts", "kind": "depends_on", "note": "事件/消息/工具/权限契约"},
    {"src": "agent-core", "dst": "runtime-services", "kind": "calls", "note": "工具执行、Journal、权限、Checkpoint"},
    {"src": "agent-core", "dst": "runtime-kernel", "kind": "calls", "note": "RunState 投影与 reducer"},
    {"src": "agent-core", "dst": "model", "kind": "calls", "note": "stream_chat/invoke"},
    {"src": "agent-core", "dst": "tools", "kind": "uses", "note": "内置工具注册"},
    {"src": "agent-core", "dst": "skills", "kind": "uses", "note": "Skill 加载与热更新"},
    {"src": "agent-core", "dst": "mcp", "kind": "uses", "note": "MCP 工具发现"},
    {"src": "agent-core", "dst": "memory", "kind": "reads", "note": "历史/经验/核心记忆注入"},
    {"src": "runtime-services", "dst": "runtime-contracts", "kind": "implements", "note": "服务实现契约"},
    {"src": "runtime-services", "dst": "runtime-kernel", "kind": "uses", "note": "Journal 事件归约"},
    {"src": "runtime-services", "dst": "tools", "kind": "calls", "note": "工具执行服务调用工具"},
    {"src": "runtime-services", "dst": "memory", "kind": "feeds", "note": "Journal 投影供记忆读取"},
    {"src": "runtime-adapters", "dst": "runtime-services", "kind": "exposes", "note": "Flask/SSE 适配"},
    {"src": "memory", "dst": "runtime-contracts", "kind": "depends_on", "note": "事件/消息契约"},
    {"src": "memory", "dst": "runtime-services", "kind": "reads", "note": "Journal/会话服务"},
    {"src": "memory", "dst": "model", "kind": "calls", "note": "巩固/经验/技能生成用 LLM"},
    {"src": "tools", "dst": "runtime-contracts", "kind": "depends_on", "note": "ToolSpec/权限"},
    {"src": "tools", "dst": "runtime-services", "kind": "uses", "note": "路径/工作区/会话上下文"},
    {"src": "skills", "dst": "tools", "kind": "exposes", "note": "GetSkill 等技能工具"},
    {"src": "mcp", "dst": "runtime-contracts", "kind": "depends_on", "note": "直接构造 ToolSpec"},
    {"src": "model", "dst": "runtime-contracts", "kind": "depends_on", "note": "CanonicalPart/Message"},
    {"src": "extension", "dst": "agent-core", "kind": "extends", "note": "提示词/护栏/移交"},
    {"src": "tests", "dst": "sdk", "kind": "verifies", "note": ""},
    {"src": "tests", "dst": "agent-core", "kind": "verifies", "note": ""},
    {"src": "tests", "dst": "runtime-services", "kind": "verifies", "note": ""},
    {"src": "tests", "dst": "memory", "kind": "verifies", "note": ""},
    {"src": "tests", "dst": "tools", "kind": "verifies", "note": ""},
    {"src": "tests", "dst": "skills", "kind": "verifies", "note": ""},
    {"src": "tests", "dst": "mcp", "kind": "verifies", "note": ""},
    {"src": "tests", "dst": "model", "kind": "verifies", "note": ""},
    {"src": "evals", "dst": "agent-core", "kind": "verifies", "note": "确定性轨迹驱动真实执行器"},
    {"src": "evals", "dst": "tools", "kind": "verifies", "note": "真实工具链路与权限拒绝"},
    {"src": "evals", "dst": "model", "kind": "uses", "note": "脚本化模型客户端"},
    {"src": "benchmarks", "dst": "sdk", "kind": "uses", "note": "完整 runtime 构造 Agent"},
    {"src": "benchmarks", "dst": "agent-core", "kind": "verifies", "note": "single/delegated 对照与收益判定"},
    {"src": "benchmarks", "dst": "memory", "kind": "uses", "note": "隔离的会话记忆"},
)


def semantic_graph() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "nodes": [dict(item, kind="subsystem") for item in SUBSYSTEMS],
        "edges": [dict(item) for item in EDGES],
    }


def render_semantic_mermaid() -> str:
    lines = ["flowchart LR"]
    for node in SUBSYSTEMS:
        lines.append(f'    {node["id"].replace("-", "_")}["{node["title"]}"]')
    for edge in EDGES:
        src = edge["src"].replace("-", "_")
        dst = edge["dst"].replace("-", "_")
        label = edge["kind"]
        lines.append(f"    {src} -->|{label}| {dst}")
    return "\n".join(lines)
