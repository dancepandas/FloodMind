"""Focused tests for specialist tool-state and artifact handoff."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.native_flood_agent import NativeFloodAgent, _InstanceToolRegistry
from floodmind.agent.native.tool_loading import ToolLoader, ToolLoadingConfig, make_get_tool_tool
from floodmind.agent.native.types import AgentResult, RunContext
from floodmind.agent.runtime.contracts.tools import ToolSpec
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.sandbox_service import SandboxService
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService


def _tool(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} tool",
        parameters={"type": "object", "properties": {}, "required": []},
        func=lambda: "ok",
    )


def _specialist_agent(tmp_path: Path) -> NativeFloodAgent:
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent._specialist_tool_loader = ToolLoader(
        ToolLoadingConfig(mode="progressive", core_tools=["GetTool"], max_loaded_tools=1)
    )
    agent._specialist_registry = _InstanceToolRegistry()
    agent._specialist_registry.register(_tool("Read"))
    agent._specialist_registry.register(_tool("Write"))
    agent._specialist_registry.register(
        make_get_tool_tool(agent._specialist_tool_loader, agent._specialist_registry)
    )
    agent._specialist_executor = SimpleNamespace(
        system_prompts=["specialist"],
        _build_initial_messages=lambda **kwargs: [],
    )
    agent._sandbox_service = SandboxService(base_dir=tmp_path / "sandboxes")
    agent._event_bus = EventBus()
    agent._model_client = None
    agent._tool_executor = None
    agent._max_iterations = 4
    agent._checkpoint_service = None
    agent._tracing_service = None
    agent._path_service = PathService(project_root=tmp_path)
    agent._permission_service = PermissionService(path_service=agent._path_service)
    agent._background_task_service = BackgroundTaskService(base_dir=tmp_path / "sessions")
    agent._journal_authority = open_journal_authority(
        tmp_path, conversation_id="conv", task_id="task", run_id="run_parent",
        thread_id="thread_parent", turn_id="turn_parent",
    )
    return agent


def _parent_context(tmp_path: Path, *, output_dir: Path) -> RunContext:
    return RunContext(
        session_id="parent", user_text="task", output_dir=str(output_dir),
        state_dir="",
        runtime_context=RuntimeContext(
            conversation_id="conv", task_id="task", run_id="run_parent",
            thread_id="thread_parent", turn_id="turn_parent",
        ),
    )


def test_parallel_specialist_tool_loaders_are_clean_and_isolated(tmp_path):
    agent = _specialist_agent(tmp_path)
    agent._specialist_tool_loader.get_tool_detail(agent._specialist_registry, "Read")

    def create_and_load(tool_name):
        registry, loader = agent._make_specialist_tool_runtime()
        registry.get("GetTool").func(tool_name=tool_name)
        return registry, loader

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(create_and_load, ["Read", "Write"]))

    first_registry, first_loader = first
    second_registry, second_loader = second
    assert first_loader is not second_loader
    assert first_loader.config is not second_loader.config
    assert first_loader.loaded_tools == {"GetTool", "Read"}
    assert second_loader.loaded_tools == {"GetTool", "Write"}
    assert agent._specialist_tool_loader.loaded_tools == {"GetTool", "Read"}
    assert first_registry.get("GetTool") is not second_registry.get("GetTool")


def test_specialist_run_injects_background_task_service(tmp_path, monkeypatch):
    agent = _specialist_agent(tmp_path)
    captured = {}

    class CapturingExecutor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run_from_state(self, context, state, run_state=None):
            return AgentResult(final_output="done", reasoning="")

    monkeypatch.setattr(
        "floodmind.agent.native.native_flood_agent.NativeAgentExecutor",
        CapturingExecutor,
    )
    agent._run_specialist_task(
        task_text="run background work",
        skill_name="",
        parent_context=_parent_context(tmp_path, output_dir=tmp_path / "parent"),
        step_key="step-bg",
    )

    assert captured["background_task_service"] is agent._background_task_service


def test_specialist_copies_only_artifacts_created_during_run(tmp_path, monkeypatch):
    agent = _specialist_agent(tmp_path)
    parent_output = tmp_path / "parent"
    original_create = agent._sandbox_service.create

    def create_with_preexisting(*args, **kwargs):
        ctx = original_create(*args, **kwargs)
        (ctx.outputs_dir / "preexisting.md").write_text("old", encoding="utf-8")
        return ctx

    monkeypatch.setattr(agent._sandbox_service, "create", create_with_preexisting)

    class WritingExecutor:
        def __init__(self, **kwargs):
            pass

        def run_from_state(self, context, state, run_state=None):
            (Path(context.output_dir) / "generated.md").write_text("new", encoding="utf-8")
            return AgentResult(final_output="done", reasoning="")

    monkeypatch.setattr(
        "floodmind.agent.native.native_flood_agent.NativeAgentExecutor", WritingExecutor
    )
    report = agent._run_specialist_task(
        task_text="write report",
        skill_name="",
        parent_context=_parent_context(tmp_path, output_dir=parent_output),
        step_key="step-1",
    )

    assert report.completed is True
    assert report.artifacts == [str(parent_output / "generated.md")]
    assert (parent_output / "generated.md").read_text(encoding="utf-8") == "new"
    assert not (parent_output / "preexisting.md").exists()


def test_specialist_copies_new_artifact_when_execution_fails(tmp_path, monkeypatch):
    agent = _specialist_agent(tmp_path)
    parent_output = tmp_path / "parent"

    class FailingExecutor:
        def __init__(self, **kwargs):
            pass

        def run_from_state(self, context, state, run_state=None):
            (Path(context.output_dir) / "partial.txt").write_text("partial", encoding="utf-8")
            raise RuntimeError("specialist failed")

    monkeypatch.setattr(
        "floodmind.agent.native.native_flood_agent.NativeAgentExecutor", FailingExecutor
    )
    report = agent._run_specialist_task(
        task_text="write report",
        skill_name="",
        parent_context=_parent_context(tmp_path, output_dir=parent_output),
        step_key="step-1",
    )

    assert report.completed is False
    assert report.summary == "specialist failed"
    assert report.outputs == {"error": "specialist failed"}
    assert report.artifacts == [str(parent_output / "partial.txt")]
    assert (parent_output / "partial.txt").read_text(encoding="utf-8") == "partial"
