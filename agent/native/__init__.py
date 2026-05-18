"""
Native Agent Runtime

自研 Agent 执行层，不依赖 LangChain Agent/Executor/Tool/Message。
"""

from agent.native.types import (
    AgentLoopState,
    AgentResult,
    ArtifactRecord,
    Attachment,
    ExecutionPlan,
    ModelEvent,
    PlanStep,
    RunContext,
)
from agent.runtime.contracts.tools import (
    ToolCall,
    ToolResult,
    ToolSpec,
)
from agent.native.model_client import ModelClient
from agent.native.message_builder import MessageBuilder
from agent.native.tool_runtime import (
    native_from_agent_tool,
    register_agent_tools,
    tool_spec_from_agent_tool,
)
from agent.runtime.services.tool_execution_service import ToolExecutionService
from agent.native.executor import NativeAgentExecutor
from agent.native.event_bus import EventBus
from agent.native.artifact_watcher import ArtifactWatcher
from agent.native.planner import Planner

__all__ = [
    "AgentLoopState",
    "AgentResult",
    "ArtifactRecord",
    "Attachment",
    "ExecutionPlan",
    "ModelEvent",
    "PlanStep",
    "RunContext",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "ModelClient",
    "MessageBuilder",
    "native_from_agent_tool",
    "tool_spec_from_agent_tool",
    "register_agent_tools",
    "ToolExecutionService",
    "NativeAgentExecutor",
    "EventBus",
    "ArtifactWatcher",
    "Planner",
]


def create_flood_agent(*, llm_service=None, memory=None, session_id: str = "", **kwargs):
    """统一 Agent 工厂：创建 NativeFloodAgent。"""
    from agent.native.native_flood_agent import NativeFloodAgent
    return NativeFloodAgent(
        llm_service=llm_service,
        memory=memory,
        session_id=session_id,
        **kwargs,
    )