from pathlib import Path
from types import SimpleNamespace

import pytest

from floodmind.memory import skill_generator
from floodmind.memory.experience_tree import ExperienceLeaf
from floodmind.memory.task_experience import (
    SkillGenerationOwner,
    TaskExperienceCapture,
    TaskExperienceStore,
)
from floodmind.tools.base_tools import make_task_experience_tools


class _Tree:
    def __init__(self, leaves):
        self._leaves = leaves

    def find_node(self, path):
        return SimpleNamespace(node_id="branch")

    def get_leaves(self, node_id):
        return self._leaves

    def mark_skill_generated(self, path):
        return True


def _store(leaves):
    store = object.__new__(TaskExperienceStore)
    store._tree = _Tree(leaves)
    store._llm_service = None
    return store


def test_skill_generation_is_scoped_to_each_owner(tmp_path, monkeypatch):
    leaves = [SimpleNamespace()]
    store = _store(leaves)
    callbacks = []

    monkeypatch.setattr(
        skill_generator,
        "generate_skill_from_branch",
        lambda **kwargs: f"# {kwargs['skill_slug']}",
    )

    owner_a = SkillGenerationOwner(tmp_path / "project-a" / "skills", lambda: callbacks.append("a"))
    owner_b = SkillGenerationOwner(tmp_path / "project-b" / "skills", lambda: callbacks.append("b"))

    store._try_generate_skill(["owner-a-skill"], 1, "summary", owner=owner_a)
    store._try_generate_skill(["owner-b-skill"], 1, "summary", owner=owner_b)

    assert (owner_a.writable_root / "owner-a-skill" / "SKILL.md").read_text(encoding="utf-8") == "# owner-a-skill"
    assert (owner_b.writable_root / "owner-b-skill" / "SKILL.md").read_text(encoding="utf-8") == "# owner-b-skill"
    assert not (owner_a.writable_root / "owner-b-skill").exists()
    assert not (owner_b.writable_root / "owner-a-skill").exists()
    assert callbacks == ["a", "b"]


def test_skill_generation_rejects_traversal_and_absolute_components(tmp_path, monkeypatch):
    store = _store([SimpleNamespace()])
    generated = []
    monkeypatch.setattr(
        skill_generator,
        "generate_skill_from_branch",
        lambda **kwargs: generated.append(kwargs) or "# unsafe",
    )
    owner = SkillGenerationOwner(tmp_path / "skills")

    for path in (["..", "evil"], ["/absolute"], ["nested/name"]):
        with pytest.raises(ValueError, match="非法 skill name"):
            store._try_generate_skill(path, 1, "summary", owner=owner)

    assert generated == []
    assert not (tmp_path / "evil").exists()


def test_store_maintenance_persists_tree_once(tmp_path, monkeypatch):
    store = TaskExperienceStore(str(tmp_path), shared=False)
    tree = store.tree
    for index in range(2):
        tree.add_leaf(
            ExperienceLeaf(
                node_id=f"leaf-{index}",
                experience_id=f"exp-{index}",
                path=["domain"],
                label=f"case-{index}",
                task_description="same task",
                domain_keywords=["same"],
                importance=0.1,
            ),
            ["domain"],
        )
    saves = []
    monkeypatch.setattr(tree, "_save_now", lambda: saves.append("save"))
    monkeypatch.setattr(tree, "find_duplicate_groups", lambda threshold: [])
    monkeypatch.setattr(store, "seal_if_needed", lambda: [])
    monkeypatch.setattr(store, "_mark_maintenance_done", lambda: None)

    report = store.run_maintenance()

    assert report["removed"] == 2
    assert saves == ["save"]


def test_capture_binding_returns_independent_owner_facades(tmp_path):
    shared = TaskExperienceCapture(llm_service=object())
    callback_a = lambda: None
    callback_b = lambda: None

    capture_a = shared.bind_skill_generation(tmp_path / "a", callback_a)
    capture_b = shared.bind_skill_generation(tmp_path / "b", callback_b)

    assert capture_a is not capture_b
    assert capture_a.skill_owner.writable_root == (tmp_path / "a").resolve()
    assert capture_b.skill_owner.writable_root == (tmp_path / "b").resolve()
    assert capture_a.skill_owner.on_generated is callback_a
    assert capture_b.skill_owner.on_generated is callback_b
    assert capture_a.store is not capture_b.store
    assert capture_a.store.tree is not capture_b.store.tree
    assert Path(capture_a.store.persist_dir) == (tmp_path / "a" / ".floodmind" / "task_experience").resolve()
    assert Path(capture_b.store.persist_dir) == (tmp_path / "b" / ".floodmind" / "task_experience").resolve()
    assert shared.skill_owner is None


def test_bound_captures_use_explicit_per_agent_llms(tmp_path):
    llm_a = object()
    llm_b = object()
    shared = TaskExperienceCapture(llm_service=object())

    capture_a = shared.bind_skill_generation(tmp_path / "a", llm_service=llm_a)
    capture_b = shared.bind_skill_generation(tmp_path / "b", llm_service=llm_b)

    assert capture_a.llm_service is llm_a
    assert capture_b.llm_service is llm_b
    assert capture_a._extractor.llm_service is llm_a
    assert capture_b._extractor.llm_service is llm_b
    assert capture_a.store._llm_service is llm_a
    assert capture_b.store._llm_service is llm_b


def test_skill_generation_requires_explicit_owner():
    store = _store([])

    with pytest.raises(TypeError):
        store._try_generate_skill(["ambiguous"], 1, "summary")


def test_ownerless_seal_summarizes_generation_eligible_branch():
    store = _store([])
    store._tree.get_branches_needing_seal = lambda threshold: [(["eligible"], 5)]
    store._tree.get_all_summaries = lambda: []
    sealed = []
    store._generate_summary = lambda path: "summary"
    store._tree.seal_branch = lambda path, summary: sealed.append(path) or SimpleNamespace()

    assert len(store.seal_if_needed()) == 1
    assert sealed == [["eligible"]]


def test_owner_can_generate_from_previously_ownerless_sealed_branch(tmp_path, monkeypatch):
    store = _store([SimpleNamespace()])
    summary = SimpleNamespace(
        tree_path=["eligible"], child_ids=["a"] * 5,
        summary_text="summary", skill_generated=False,
    )
    store._tree.get_branches_needing_seal = lambda threshold: []
    store._tree.get_all_summaries = lambda: [summary]
    monkeypatch.setattr(
        skill_generator,
        "generate_skill_from_branch",
        lambda **kwargs: "# generated",
    )

    owner = SkillGenerationOwner(tmp_path / "skills")
    store.seal_if_needed(skill_owner=owner)

    assert (owner.writable_root / "eligible" / "SKILL.md").is_file()


def test_bound_tools_are_isolated_between_two_agents(tmp_path):
    capture_a = TaskExperienceCapture().bind_skill_generation(tmp_path / "a")
    capture_b = TaskExperienceCapture().bind_skill_generation(tmp_path / "b")
    tools_a = {tool.name: tool for tool in make_task_experience_tools(
        capture_a.store, skill_owner=capture_a.skill_owner,
    )}
    tools_b = {tool.name: tool for tool in make_task_experience_tools(
        capture_b.store, skill_owner=capture_b.skill_owner,
    )}

    tools_a["AddTaskExperience"].func(path="domain", description="only-a")

    assert capture_a.store.has_experiences()
    assert not capture_b.store.has_experiences()
    assert "only-a" in tools_a["SearchTaskExperience"].func(query="only-a")
    assert "当前没有积累" in tools_b["SearchTaskExperience"].func(query="only-a")


def test_completed_capture_is_visible_to_next_turn(tmp_path, monkeypatch):
    capture = TaskExperienceCapture(llm_service=object()).bind_skill_generation(tmp_path)
    leaf = ExperienceLeaf(
        node_id="", experience_id="", path=["domain", "captured"], label="captured",
        task_description="next turn knowledge", domain_keywords=["next"], importance=0.8,
    )
    monkeypatch.setattr(capture._extractor, "extract", lambda **kwargs: leaf)

    capture.on_task_complete(
        session_id="s", user_input="task", plan="plan", tool_results=[{}, {}],
        final_output="done",
    )
    capture.wait_for_pending()

    assert capture.store.has_experiences()
    assert "domain/captured" in capture.store.build_summary_context()
    assert "next turn knowledge" in capture.store.render_experience_markdown(
        capture.store.search_keywords("next")
    )
