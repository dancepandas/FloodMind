"""
Native Agent Runtime

自研 Agent 执行层，不依赖 LangChain Agent/Executor/Tool/Message。
"""

from floodmind.agent.native.types import (
    AgentLoopState,
    AgentResult,
    ArtifactRecord,
    Attachment,
    ExecutionPlan,
    ModelEvent,
    PlanStep,
    RunContext,
)
from floodmind.agent.runtime.contracts.tools import (
    ToolCall,
    ToolResult,
    ToolSpec,
)
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.tool_runtime import (
    native_from_agent_tool,
    register_agent_tools,
    tool_spec_from_agent_tool,
)
from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.artifact_watcher import ArtifactWatcher
from floodmind.agent.native.planner import Planner

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
    from floodmind.agent.native.native_flood_agent import NativeFloodAgent
    return NativeFloodAgent(
        llm_service=llm_service,
        memory=memory,
        session_id=session_id,
        **kwargs,
    )