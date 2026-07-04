"""
Agent 工厂

为 web session 创建 / 复用 NativeFloodAgent 实例。
"""
import logging
from typing import Optional

from floodmind.config.settings import settings
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native import create_flood_agent
from floodmind.config.model_presets import get_preset, get_default_model_key
from floodmind.memory import DualMemory
from floodmind.tools import set_memory_instance, set_session_context
from floodmind.agent.runtime.services.workspace_service import build_workspace, set_workspace
from floodmind.server.session_state import ensure_session_state, clear_session_token_usage

logger = logging.getLogger(__name__)


def create_agent_for_session(
    session_id: str,
    session_manager: "SessionManager",
    enable_search: bool = False,
    enable_rag: Optional[bool] = None,
    enable_reasoning: bool = True,
    model_key: Optional[str] = None,
):
    """为会话创建 Agent 实例（Native Runtime）。"""
    from floodmind.memory.session_manager import validate_session_id
    session_id = validate_session_id(session_id or "default")
    logger.info("创建新的智能体实例: session=%s, runtime=%s", session_id, settings.agent.runtime)

    clear_session_token_usage(session_id)

    if enable_rag is None:
        enable_rag = False  # RAG now via MCP, see settings mcpServers

    if model_key:
        llm_service = ModelClient.from_settings_with_preset(
            model_key,
            enable_reasoning=enable_reasoning,
        )
    else:
        llm_service = ModelClient.from_settings(
            temperature=settings.qwen.temperature,
            max_tokens=settings.qwen.max_tokens,
            enable_thinking=enable_reasoning,
        )

    memory_dir = session_manager.get_memory_dir(session_id)
    memory = DualMemory(
        session_id=session_id,
        max_short_term=20,
        context_window=32768,
        persist_dir=memory_dir,
        llm=llm_service,
    )

    agent = create_flood_agent(
        llm_service=llm_service,
        memory=memory,
        session_id=session_id,
        enable_search=enable_search,
    )

    # 记录创建该 agent 时绑定的会话配置，供后续复用前比对
    agent._session_model_key = model_key or get_default_model_key()
    agent._session_enable_search = enable_search
    agent._session_enable_rag = enable_rag
    agent._session_enable_reasoning = enable_reasoning

    # 注入 Workspace
    ws = build_workspace(session_id, session_root=session_manager.sessions_dir)
    set_workspace(ws)
    set_session_context(
        session_id=session_id,
        output_dir=str(ws.user_dir),
    )

    return agent


def get_or_create_agent(session_id: str, session_manager: "SessionManager"):
    """获取或创建会话 Agent。配置变更时自动重建。"""
    from floodmind.memory.session_manager import validate_session_id
    session_id = validate_session_id(session_id or "default")
    session_manager.touch_session(session_id)

    ws = build_workspace(session_id, session_root=session_manager.sessions_dir)
    set_workspace(ws)
    set_session_context(
        session_id=session_id,
        output_dir=str(ws.user_dir),
    )

    state = ensure_session_state(session_id)
    enable_search = state.get('enable_search', True)
    enable_rag = state.get('enable_rag', False)
    enable_reasoning = state.get('enable_reasoning', True)
    model_key = state.get('model_key') or get_default_model_key()

    agent = session_manager.get_agent(session_id)
    if agent:
        current_model_key = getattr(agent, '_session_model_key', get_default_model_key())
        current_enable_search = getattr(agent, '_session_enable_search', False)
        current_enable_rag = getattr(agent, '_session_enable_rag', False)
        current_enable_reasoning = getattr(agent, '_session_enable_reasoning', True)

        if (
            current_model_key == model_key
            and current_enable_search == enable_search
            and current_enable_rag == enable_rag
            and current_enable_reasoning == enable_reasoning
        ):
            return agent

        logger.info(
            "会话 %s 配置已变更，重建 Agent: model %s -> %s, search %s -> %s, rag %s -> %s, reasoning %s -> %s",
            session_id, current_model_key, model_key,
            current_enable_search, enable_search,
            current_enable_rag, enable_rag,
            current_enable_reasoning, enable_reasoning,
        )
        if hasattr(session_manager, '_agents') and session_id in session_manager._agents:
            del session_manager._agents[session_id]

    _, agent = session_manager.get_or_create_session(
        session_id,
        agent_factory=lambda sid: create_agent_for_session(
            sid, session_manager,
            enable_search=enable_search,
            enable_rag=enable_rag,
            enable_reasoning=enable_reasoning,
            model_key=model_key,
        )
    )

    return agent
