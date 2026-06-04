"""
智能体模块初始化

统一入口：create_flood_agent() 工厂函数。
LangChain FloodAgent 已移除，仅保留 Native Runtime。

使用懒加载避免 import agent 时触发 config/settings 初始化。
"""

__all__ = ["NativeFloodAgent", "create_flood_agent"]


def __getattr__(name):
    if name == "NativeFloodAgent":
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        return NativeFloodAgent
    if name == "create_flood_agent":
        def _create(*, llm_service=None, memory=None, session_id: str = "", **kwargs):
            from floodmind.agent.native.native_flood_agent import NativeFloodAgent
            return NativeFloodAgent(
                llm_service=llm_service,
                memory=memory,
                session_id=session_id,
                **kwargs,
            )
        return _create
    raise AttributeError(f"module 'agent' has no attribute {name!r}")
