"""
FloodMind — 基于大语言模型的智能 Agent 框架

快速开始:
    from floodmind import Agent, ModelClient, build_agent_tool

    llm = ModelClient(api_key="sk-xxx", base_url="https://...", model_name="my-model")

    agent = Agent(
        llm=llm,
        tools=[build_agent_tool(func=my_func, name="MyTool", description="...")],
        system_prompt="你是助手。",
    )
    result = agent.run("你好")
"""

__version__ = "1.0.0"

# ── SDK 公共 API ──

def __getattr__(name):
    """懒加载 SDK 公共 API，避免 import 时触发 settings 初始化。"""
    _exports = {
        "Agent": "floodmind.agent.api",
        "ModelClient": "floodmind.agent.native.model_client",
        "build_agent_tool": "floodmind.tools.agent_tool",
        "DualMemory": "floodmind.memory.dual_memory",
        "AgentTool": "floodmind.tools.agent_tool",
        "Skill": "floodmind.skills.base",
        "register_skill": "floodmind.skills",
        "FloodmindPlugin": "floodmind.plugin.base",
        "NativeFloodAgent": "floodmind.agent.native.native_flood_agent",
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
    "build_agent_tool",
    "DualMemory",
    "AgentTool",
    "Skill",
    "register_skill",
    "FloodmindPlugin",
    "NativeFloodAgent",
]
