"""Per-Agent Skill roots and runtime isolation."""

from pathlib import Path

import pytest

from floodmind import Agent, Skill, SkillRegistry, create_flood_agent, create_skill_registry
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.runtime.contracts.workspace import Workspace


def _llm():
    return ModelClient(api_key="mock", base_url="https://mock.invalid/v1", model_name="mock")


def _write_skill(root: Path, name: str, marker: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {marker}\n---\n\n{marker}\n",
        encoding="utf-8",
    )


def _get_skill_text(agent: Agent, name: str) -> str:
    tool = agent.raw._orchestrator_registry.get("GetSkill")
    return tool.func(skill_name=name)


def test_two_agents_isolate_same_named_skill_and_catalog(tmp_path):
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    _write_skill(root_a, "same", "agent-a-only")
    _write_skill(root_b, "same", "agent-b-only")

    a = Agent(llm=_llm(), skill_roots=[root_a], skill_writable_root=root_a)
    b = Agent(llm=_llm(), skill_roots=[root_b], skill_writable_root=root_b)

    assert "agent-a-only" in _get_skill_text(a, "same")
    assert "agent-b-only" not in _get_skill_text(a, "same")
    assert "agent-b-only" in _get_skill_text(b, "same")
    assert "agent-a-only" in a.raw._skill_catalog
    assert "agent-b-only" not in a.raw._skill_catalog


def test_public_property_and_injected_registry(tmp_path):
    registry = create_skill_registry(additional_roots=[tmp_path], writable_root=tmp_path)
    agent = Agent(llm=_llm(), skill_writable_root=tmp_path)
    assert agent.skill_registry is agent.raw.skill_registry
    assert agent.skill_registry.writable_root == tmp_path.resolve()

    native = create_flood_agent(llm_service=_llm(), bare=True, skill_registry=registry)
    assert native.skill_registry is registry
    with pytest.raises(ValueError):
        create_flood_agent(
            llm_service=_llm(), bare=True, skill_registry=registry, skill_roots=[tmp_path]
        )


def test_skill_roots_are_agent_local_readonly_authorizations(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    workspace = Workspace.from_folder(tmp_path / "workspace", session_id="roots").ensure()
    before = workspace.readable_roots
    agent = Agent(
        llm=_llm(), workspace=workspace, skill_roots=[root], skill_writable_root=root
    )
    skill_file = root / "custom" / "SKILL.md"

    assert workspace.readable_roots == before
    assert agent.raw._path_service.is_read_allowed(skill_file)
    assert not agent.raw._path_service.is_write_allowed(skill_file)

    rebound = Workspace.from_folder(tmp_path / "rebound", session_id="roots").ensure()
    rebound_before = rebound.readable_roots
    agent.bind_workspace(rebound)
    assert rebound.readable_roots == rebound_before
    assert agent.raw._path_service._effective_workspace() is rebound
    assert agent.raw._path_service.is_read_allowed(skill_file)
    assert not agent.raw._path_service.is_write_allowed(skill_file)


def test_two_full_agents_sharing_workspace_keep_skill_roots_isolated(tmp_path):
    shared = Workspace.from_folder(tmp_path / "workspace", session_id="shared").ensure()
    root_a, root_b = tmp_path / "skills-a", tmp_path / "skills-b"
    root_a.mkdir()
    root_b.mkdir()
    file_a, file_b = root_a / "a" / "SKILL.md", root_b / "b" / "SKILL.md"

    a = Agent(llm=_llm(), bare=False, workspace=shared, skill_roots=[root_a])
    b = Agent(llm=_llm(), bare=False, workspace=shared, skill_roots=[root_b])

    assert shared.readable_roots == ()
    assert a.raw._path_service.is_read_allowed(file_a)
    assert not a.raw._path_service.is_read_allowed(file_b)
    assert b.raw._path_service.is_read_allowed(file_b)
    assert not b.raw._path_service.is_read_allowed(file_a)
    assert not a.raw._path_service.is_write_allowed(file_a)
    assert not b.raw._path_service.is_write_allowed(file_b)


def test_run_injects_agent_path_service_after_later_constructor(tmp_path, monkeypatch):
    from floodmind.agent.native.types import AgentResult

    shared = Workspace.from_folder(tmp_path / "workspace", session_id="run").ensure()
    root_a, root_b = tmp_path / "skills-a", tmp_path / "skills-b"
    root_a.mkdir()
    root_b.mkdir()
    file_a, file_b = root_a / "a" / "SKILL.md", root_b / "b" / "SKILL.md"
    a = Agent(llm=_llm(), workspace=shared, skill_roots=[root_a])
    Agent(llm=_llm(), workspace=shared, skill_roots=[root_b])
    observed = {}

    def capture_context(*, context, state):
        service = context.runtime_context.path_service
        observed["service"] = service
        observed["a"] = service.is_read_allowed(file_a)
        observed["b"] = service.is_read_allowed(file_b)
        return AgentResult(final_output="ok", reasoning="")

    monkeypatch.setattr(a.raw._orchestrator_executor, "run_from_state", capture_context)
    monkeypatch.setattr(a.raw, "_validate_artifacts", lambda result: None)
    list(a.stream("check context"))

    assert observed == {"service": a.raw._path_service, "a": True, "b": False}


def test_bare_and_full_specialist_getskill_without_crud(tmp_path):
    bare = Agent(llm=_llm(), skill_writable_root=tmp_path / "bare")
    assert bare.raw._orchestrator_registry.get("GetSkill") is not None
    assert bare.raw._orchestrator_registry.get("CreateSkill") is None

    full = Agent(llm=_llm(), bare=False, skill_writable_root=tmp_path / "full")
    assert full.raw._specialist_registry.get("GetSkill") is not None
    assert full.raw._specialist_registry.get("CreateSkill") is None
    assert full.raw._orchestrator_registry.get("CreateSkill") is not None


def test_programmatic_update_and_cleanup_are_owner_scoped(tmp_path):
    a = Agent(llm=_llm(), skill_writable_root=tmp_path / "a")
    b = Agent(llm=_llm(), skill_writable_root=tmp_path / "b")
    before_b = b.raw._skill_catalog

    a.skill_registry.register_skill(Skill(name="dynamic-owner", description="only a", prompt="A"))
    assert "dynamic-owner" in a.raw._skill_catalog
    assert b.raw._skill_catalog == before_b
    assert "dynamic-owner" not in b.raw._skill_catalog

    bg_service = a.raw._background_task_service
    assert (a.raw.session_id, a.raw._bg_task_callback) in bg_service._subscribers
    assert len(a.skill_registry._refresh_callbacks) == 2  # GetSkill cache + agent prompt

    a.cleanup()
    a.cleanup()

    assert (a.raw.session_id, a.raw._bg_task_callback) not in bg_service._subscribers
    assert len(a.skill_registry._refresh_callbacks) == 0


def test_injected_registry_refresh_is_safe_after_construction(tmp_path):
    registry = create_skill_registry(writable_root=tmp_path)
    native = create_flood_agent(llm_service=_llm(), bare=True, skill_registry=registry)

    registry.register_skill(Skill(name="live-refresh", description="refresh marker", prompt="body"))

    assert "live-refresh" in native._skill_catalog
    assert "live-refresh" in native._orchestrator_executor.system_prompts[0]
    native.cleanup()


def test_factory_propagates_skill_roots(tmp_path):
    root = tmp_path / "factory"
    root.mkdir()
    native = create_flood_agent(
        llm_service=_llm(), bare=True, skill_roots=[root], skill_writable_root=root
    )
    assert native.skill_registry.writable_root == root.resolve()
    assert root.resolve() in native.skill_registry.roots
