"""Checkpoint continuation assembly shared by SDK and CLI hosts."""
import contextvars
from pathlib import Path


def resume_agent(agent, checkpoint_id: str, user_message: str = ""):
    """Return (AgentResult, read authority); active bindings end with execution."""
    if not checkpoint_id:
        raise ValueError("resume requires checkpoint_id")
    if not agent._stream_lock.acquire(blocking=False):
        raise RuntimeError("同一 Agent 实例不支持并发运行或恢复")
    try:
        return contextvars.copy_context().run(
            _resume_agent, agent, checkpoint_id, user_message,
        )
    finally:
        agent._stream_lock.release()


def _resume_agent(agent, checkpoint_id, user_message):
    from floodmind.agent.native.executor import project_run_state_to_loop_state
    from floodmind.agent.native.types import AgentLoopState
    from floodmind.agent.runtime.services.checkpoint_service import CheckpointService
    from floodmind.agent.runtime.services.resume_service import ResumeService

    workspace = agent._effective_workspace()
    if workspace is None or not workspace.session_root:
        raise ValueError("resume requires a workspace with session_root")
    session_id = agent.session_id
    service = CheckpointService(base_dir=str(workspace.session_root))
    manifest = service.load_manifest(session_id, checkpoint_id)
    identity = manifest.metadata or {}
    required = ("conversation_id", "task_id", "run_id", "thread_id", "turn_id", "runtime_dir")
    missing = [key for key in required if not identity.get(key)]
    if missing:
        raise ValueError(f"checkpoint missing journal identity: {', '.join(missing)}")
    state = service.load(session_id, checkpoint_id, state_class=AgentLoopState)
    outcome = ResumeService().resume(
        runtime_dir=Path(identity["runtime_dir"]),
        conversation_id=identity["conversation_id"], task_id=identity["task_id"],
        run_id=identity["run_id"], thread_id=identity["thread_id"],
        turn_id=identity["turn_id"], checkpoint_id=checkpoint_id,
        user_message=user_message, session_id=session_id, checkpoint_service=service,
    )
    authority = outcome.authority
    executor = agent._orchestrator_executor
    previous_context = agent._current_run_context
    bus = executor.event_bus
    try:
        try:
            if authority is None:
                raise RuntimeError("ResumeService returned no JournalAuthority")
            context = agent._assemble_run_context(
                authority=authority, runtime_dir=Path(identity["runtime_dir"]),
                identity=identity, workspace=workspace, session_id=session_id,
                user_text=user_message,
            )
            state = project_run_state_to_loop_state(state, outcome.run_state)
            if state.status in {"completed", "failed"}:
                state.status = "awaiting_llm"
                state.final_output = ""
            if user_message:
                state.user_message = user_message
                state.original_input = state.original_input or user_message
            agent._last_loop_state = state
            agent._current_run_context = context
            bus.emit({"type": "resume", "checkpoint_id": checkpoint_id, "status": "started"})
            result = executor.run_from_state(context, state, run_state=outcome.run_state)
            status = "completed" if authority.replay().status.value == "completed" else "failed"
        finally:
            try:
                agent._deactivate_run_authority(authority)
            finally:
                agent._current_run_context = previous_context
                outcome.lease.release()
    except Exception:
        bus.emit({"type": "resume", "checkpoint_id": checkpoint_id, "status": "failed"})
        raise
    bus.emit({"type": "resume", "checkpoint_id": checkpoint_id, "status": status})
    return result, authority
