"""
FloodMind Agent — 轻量级 SDK 入口

面向嵌入场景的便捷类，封装 NativeFloodAgent(bare=True)。
开发者只需传入 ModelClient + 自定义工具 + 提示词即可使用。

用法:
    from floodmind import Agent, ModelClient, build_agent_tool

    llm = ModelClient(api_key="sk-xxx", base_url="https://...", model_name="my-model")

    def query_data(station: str) -> str:
        return f"{station} 数据..."

    agent = Agent(
        llm=llm,
        tools=[build_agent_tool(func=query_data, name="QueryData", description="查询数据")],
        system_prompt="你是数据分析助手。",
    )

    result = agent.run("查一下 XX 站的数据")       # 非流式
    for event in agent.stream("查一下 XX 站"):      # 流式
        print(event)
"""

from typing import Any, Dict, Iterator, List, Optional

from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.agent.native.model_client import ModelClient


class Agent:
    """FloodMind 轻量级 Agent — 嵌入式 SDK 入口。

    将 FloodMind Agent 嵌入到任何 Python 系统中：
    - 传入 LLM 客户端和自定义工具
    - 通过 run() 或 stream() 获取结果
    - 流式事件可直接推送给自建前端

    Args:
        llm: ModelClient 实例（必填）
        tools: build_agent_tool() 构建的工具列表
        system_prompt: 自定义系统提示词
        memory: DualMemory 实例（不传则自动创建内存记忆）
        session_id: 会话 ID
        enable_search: 启用 WebSearch 工具
        enable_reasoning: 启用推理模式
    """

    def __init__(
        self,
        llm: ModelClient,
        tools: Optional[List[Any]] = None,
        system_prompt: Optional[str] = None,
        memory: Optional[Any] = None,
        session_id: str = "",
        enable_search: bool = False,
        enable_reasoning: bool = False,
    ):
        if memory is None:
            from floodmind.memory.dual_memory import DualMemory
            sid = session_id or "sdk-agent"
            memory = DualMemory(session_id=sid, max_short_term=20, context_window=32768)

        self._agent = NativeFloodAgent(
            llm_service=llm,
            memory=memory,
            session_id=session_id or "sdk-agent",
            enable_search=enable_search,
            enable_reasoning=enable_reasoning,
            bare=True,
            tools=tools or [],
            system_prompt=system_prompt,
        )

    def run(self, message: str) -> str:
        """非流式执行，返回最终回答文本。"""
        return self._agent.run(message)

    def stream(self, message: str) -> Iterator[Dict[str, Any]]:
        """流式执行，产出结构化事件 dict。

        事件类型:
          - answer_delta:  回答文本增量  {"type": "answer_delta", "content": "..."}
          - thought_delta: 思考过程增量  {"type": "thought_delta", "content": "..."}
          - action_start:  工具调用开始  {"type": "action_start", "tool_name": "...", "status": "running"}
          - action_end:    工具调用结束  {"type": "action_end", "tool_name": "...", "content": "..."}
          - final_text:    最终完整回答  {"type": "final_text", "content": "..."}
          - error:         错误          {"type": "error", "content": "..."}
        """
        yield from self._agent.stream(message)

    def chat(self, message: str) -> str:
        """run() 的别名。"""
        return self.run(message)

    @property
    def raw(self) -> NativeFloodAgent:
        """访问底层 NativeFloodAgent 实例（高级用法）。"""
        return self._agent

    def __repr__(self) -> str:
        return f"<Agent tools={len(self._agent._orchestrator_registry.all())} session={self._agent.session_id}>"
