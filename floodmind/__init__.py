"""
FloodMind — 基于大语言模型的智能 Agent 框架

快速开始:
    from floodmind import Agent, ModelClient, build_agent_tool

    llm = ModelClient(api_key="sk-xxx", base_url="https://...", model_name="my-model")
    agent = Agent(llm=llm, tools=[build_agent_tool(func=my_func, name="MyTool", description="...")])
    result = agent.run("你好")

完整导出:
    from floodmind import (
        Agent,                # SDK 嵌入式 Agent
        ModelClient,          # LLM 客户端
        resolve_model,        # 模型配置解析（单一入口，SDK 稳定契约）
        route_pipeline,       # 厂商 Pipeline 路由（llm.pipeline 内省/自检）
        ProviderPipeline,     # 厂商 Pipeline 扩展基类
        ToolLoadingConfig,    # 渐进式工具加载配置
        ToolLoader,           # 渐进式工具加载状态（高级）
        Workspace,            # Harness 工作区契约
        build_folder_workspace, # folder-first 工作区工厂
        build_agent_tool,     # 工具构造器
        DualMemory,           # 记忆系统
        AgentTool,            # 工具基类
        Skill,                # Skill 数据结构
        register_skill,       # 编程式 Skill 注册
        FloodmindPlugin,      # Plugin 基类
        NativeFloodAgent,     # 底层 Agent（高级）
        create_flood_agent,   # Agent 工厂函数
        get_mcp_client_pool,  # MCP 连接池
        build_mcp_tool_specs, # MCP ToolSpec 构造
    )
"""

__version__ = "1.1.5"

# ── SDK 公共 API ──

def __getattr__(name):
    """懒加载 SDK 公共 API，避免 import 时触发 settings 初始化。"""
    _exports = {
        "Agent": "floodmind.agent.api",
        "ModelClient": "floodmind.agent.native.model_client",
        "resolve_model": "floodmind.config.model_resolver",
        "ResolvedModel": "floodmind.config.model_resolver",
        "route_pipeline": "floodmind.agent.native.providers",
        "ProviderPipeline": "floodmind.agent.native.providers",
        "StreamState": "floodmind.agent.native.providers",
        "TokenUsage": "floodmind.agent.native.providers",
        "OpenAICompatiblePipeline": "floodmind.agent.native.providers",
        "DashScopePipeline": "floodmind.agent.native.providers",
        "DeepSeekPipeline": "floodmind.agent.native.providers",
        "KimiPipeline": "floodmind.agent.native.providers",
        "MiniMaxPipeline": "floodmind.agent.native.providers",
        "ToolLoadingConfig": "floodmind.agent.native.tool_loading",
        "ToolCatalogEntry": "floodmind.agent.native.tool_loading",
        "ToolLoader": "floodmind.agent.native.tool_loading",
        "compact_prompt_catalog": "floodmind.agent.native.tool_loading",
        "Workspace": "floodmind.agent.runtime.contracts.workspace",
        "build_folder_workspace": "floodmind.agent.runtime.services.workspace_service",
        "build_agent_tool": "floodmind.tools.agent_tool",
        "DualMemory": "floodmind.memory.dual_memory",
        "AgentTool": "floodmind.tools.agent_tool",
        "Skill": "floodmind.skills.base",
        "register_skill": "floodmind.skills",
        "FloodmindPlugin": "floodmind.plugin.base",
        "NativeFloodAgent": "floodmind.agent.native.native_flood_agent",
        "create_flood_agent": "floodmind.agent",
        "get_mcp_client_pool": "floodmind.agent.mcp_client",
        "build_mcp_tool_specs": "floodmind.agent.mcp_client",
    }
    if name in _exports:
        import importlib
        mod = importlib.import_module(_exports[name])
        attr = getattr(mod, name)
        globals()[name] = attr  # 缓存，下次直接命中
        return attr
    raise AttributeError(f"module 'floodmind' has no attribute {name!r}")


__all__ = [
    "Agent",
    "ModelClient",
    "resolve_model",
    "ResolvedModel",
    "route_pipeline",
    "ProviderPipeline",
    "StreamState",
    "TokenUsage",
    "OpenAICompatiblePipeline",
    "DashScopePipeline",
    "DeepSeekPipeline",
    "KimiPipeline",
    "MiniMaxPipeline",
    "ToolLoadingConfig",
    "ToolCatalogEntry",
    "ToolLoader",
    "compact_prompt_catalog",
    "Workspace",
    "build_folder_workspace",
    "build_agent_tool",
    "DualMemory",
    "AgentTool",
    "Skill",
    "register_skill",
    "FloodmindPlugin",
    "NativeFloodAgent",
    "create_flood_agent",
    "get_mcp_client_pool",
    "build_mcp_tool_specs",
]
