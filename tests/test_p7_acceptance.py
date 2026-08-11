"""P7 acceptance proof for §25.7 child/background isolation."""
from unittest.mock import MagicMock

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent, RunContext
from floodmind.agent.runtime.contracts.artifacts import ArtifactDeclaration
from floodmind.agent.runtime.contracts.child_thread import ChildThread, SubagentEventType
from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior,
    PermissionRequest,
    ToolPermissionPolicy,
)
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.artifact_service import ArtifactService
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.child_permission_context import (
    build_child_permission_context,
)
from floodmind.agent.runtime.services.child_thread_runtime import ChildThreadRuntime
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.sandbox_service import SandboxService


def _registry():
    registry = MagicMock()
    registry.tools_schema.return_value = []
    return registry


def _loader():
    return MagicMock()


def _runtime(tmp_path, model_client, *, tool_runtime_factory=None):
    parent_authority = open_journal_authority(
        tmp_path / "runtime",
        conversation_id="c",
        task_id="t",
        run_id="run_1",
        thread_id="th_main",
        turn_id="tu_main",
    )
    runtime = ChildThreadRuntime(
        model_client=model_client,
        tool_executor=MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=50,
        system_prompts=["test prompt"],
        checkpoint_service=None,
        tracing_service=None,
        background_task_service=BackgroundTaskService(
            base_dir=str(tmp_path / "sessions")
        ),
        journal_authority=parent_authority,
        sandbox_service=SandboxService(base_dir=str(tmp_path / "sbx")),
        permission_service=PermissionService(),
        path_service=PathService(project_root=tmp_path),
        artifact_store_root=tmp_path / "artifacts",
        runtime_dir=tmp_path / "runtime",
        tool_runtime_factory=tool_runtime_factory or (lambda: (_registry(), _loader())),
    )
    return runtime


def _context(session_id="sess_main"):
    return RunContext(
        session_id=session_id,
        user_text="child task",
        agent_tier="main",
        runtime_context=RuntimeContext(
            conversation_id="c",
            task_id="t",
            run_id="run_1",
            thread_id="th_main",
            turn_id="tu_main",
            actor_type="agent",
            actor_id="main",
            agent_tier="main",
            runtime_mode="execution",
        ),
    )


def _child(thread_id="th_child", call_id="step_1"):
    return ChildThread(
        thread_id=thread_id,
        parent_thread_id="th_main",
        parent_call_id=call_id,
    )


def _successful_model_client():
    model_client = MagicMock(spec=ModelClient)
    model_client.stream_chat.return_value = [
        ModelEvent(type="token", content="ok"),
        ModelEvent(type="done"),
    ]
    return model_client


def test_acceptance_child_does_not_share_parent_tool_loader(tmp_path):
    """Each child receives a fresh loader, distinct from parent mutable state."""
    parent_loader = _loader()
    child_loaders = []

    def make_child_tool_runtime():
        child_loader = _loader()
        child_loaders.append(child_loader)
        return _registry(), child_loader

    runtime = _runtime(
        tmp_path,
        _successful_model_client(),
        tool_runtime_factory=make_child_tool_runtime,
    )
    first = runtime.run(_child("th_child_1", "step_1"), _context("sess_1"))
    second = runtime.run(_child("th_child_2", "step_2"), _context("sess_2"))

    assert first.event_type == SubagentEventType.result
    assert second.event_type == SubagentEventType.result
    assert len(child_loaders) == 2
    assert child_loaders[0] is not child_loaders[1]
    assert all(loader is not parent_loader for loader in child_loaders)


def test_acceptance_child_cannot_escalate_permission(tmp_path):
    """A child cannot use its parent's authority to acquire network access."""
    parent_path = PathService(project_root=tmp_path)
    parent_permission = PermissionService(ask_service=None, path_service=parent_path)
    child_workspace = tmp_path / "child-workspace"
    child_workspace.mkdir()
    child_permission, _ = build_child_permission_context(
        parent_path_service=parent_path,
        parent_permission_service=parent_permission,
        child_workspace=child_workspace,
        child_session_id="sub-1",
    )
    authority = open_journal_authority(
        tmp_path / "journal",
        conversation_id="c",
        task_id="t",
        run_id="run_1",
        thread_id="th_child",
        turn_id="tu_child",
    )
    request = PermissionRequest(
        tool_name="mcp:srv:web",
        session_id="sub-1",
        agent_tier="sub",
        permission_policy=ToolPermissionPolicy(policy_type="network"),
        tool_input={},
        call_id="call_1",
    )

    decision = child_permission.check(request, journal_authority=authority)

    assert child_permission is not parent_permission
    assert child_permission._ask_service is None
    assert decision.behavior == PermissionBehavior.DENY


def test_acceptance_artifact_survives_sandbox_destroy(tmp_path):
    """Publishing copies child output into the durable content-addressed store."""
    sandbox = SandboxService(base_dir=str(tmp_path / "sessions"))
    sandbox_context = sandbox.create("sub_1")
    source = sandbox_context.workspace_dir / "result.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    artifact_service = ArtifactService(
        tmp_path / "artifacts",
        allowed_roots=[str(sandbox_context.workspace_dir)],
    )
    manifest = artifact_service.publish(
        ArtifactDeclaration(
            logical_name=source.name,
            source_path=str(source),
            producer_thread_id="th_child",
            producer_call_id="call_1",
        )
    )

    sandbox.destroy(sandbox_context)

    assert not source.exists()
    assert artifact_service.read_path(manifest.artifact_id).read_text(
        encoding="utf-8"
    ) == "a,b\n1,2\n"


def test_acceptance_cleanup_no_residual_subscriptions_and_tasks(tmp_path):
    """A completed child leaves neither active tasks nor event subscriptions."""
    runtime = _runtime(tmp_path, _successful_model_client())

    result = runtime.run(_child(), _context())

    background_service = runtime._background_task_service
    assert result.session_id.startswith("sub-")
    assert background_service.list(result.session_id) == []
    assert background_service.has_active(result.session_id) is False
    assert background_service._subscribers == []
