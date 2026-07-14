"""Tests for SkillRegistry + skill CRUD handlers (skill unification)."""

from pathlib import Path

import pytest

from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.skills import base as skill_base
import floodmind.skills.registry as skill_reg_mod
from floodmind.skills.registry import SkillRegistry, get_skill_registry


def _make_skill_md(root: Path, name: str, desc: str = "d", body: str = "body") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n", encoding="utf-8"
    )
    return d


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------

class TestSkillRegistry:
    def test_refresh_preserves_ephemeral(self, tmp_path):
        reg = SkillRegistry(roots=[tmp_path], writable_root=tmp_path)
        reg.register_skill(skill_base.Skill(name="eph", description="e", prompt="p"))
        assert reg.get_skill("eph") is not None
        reg.refresh()
        assert reg.get_skill("eph") is not None  # 编程式 skill 重扫不丢

    def test_set_disabled_hides_and_restores(self, tmp_path):
        _make_skill_md(tmp_path, "s1", "desc1")
        reg = SkillRegistry(roots=[tmp_path], writable_root=tmp_path)
        assert reg.get_skill("s1") is not None
        reg.set_disabled("s1", True)
        assert reg.get_skill("s1") is None
        assert "s1" not in reg.catalog()
        reg.set_disabled("s1", False)
        assert reg.get_skill("s1") is not None

    def test_list_skills_includes_source(self, tmp_path):
        _make_skill_md(tmp_path, "s1")
        reg = SkillRegistry(roots=[tmp_path], writable_root=tmp_path)
        items = {s["name"]: s for s in reg.list_skills()}
        assert "s1" in items and items["s1"]["source"]

    def test_writable_root_default_is_project_skills(self):
        reg = SkillRegistry()
        assert reg.writable_root.name == "skills"


# ---------------------------------------------------------------------------
# CRUD handlers (harness + isolated singleton)
# ---------------------------------------------------------------------------

class _CrudHarness:
    """Binds the real CRUD handlers; refresh_skills stubs out prompt rebuild (data-only)."""

    def __init__(self):
        self._skill_catalog = ""

    def refresh_skills(self):
        get_skill_registry().refresh()
        self._skill_catalog = get_skill_registry().catalog()

    _resolve_skill_md_path = NativeFloodAgent._resolve_skill_md_path
    _validate_skill_name = staticmethod(NativeFloodAgent._validate_skill_name)
    _split_skill_md = staticmethod(NativeFloodAgent._split_skill_md)
    _apply_skill_body_action = staticmethod(NativeFloodAgent._apply_skill_body_action)
    _handle_list_skills = NativeFloodAgent._handle_list_skills
    _handle_create_skill = NativeFloodAgent._handle_create_skill
    _handle_update_skill = NativeFloodAgent._handle_update_skill
    _handle_remove_skill = NativeFloodAgent._handle_remove_skill
    _handle_refresh_skills = NativeFloodAgent._handle_refresh_skills


@pytest.fixture
def crud_setup(tmp_path, monkeypatch):
    reg = SkillRegistry(roots=[tmp_path], writable_root=tmp_path)
    monkeypatch.setattr(skill_reg_mod, "_registry", reg)  # 安装为单例
    # curator 单例同步：_handle_remove_skill 委托 curator.archive_skill，需要一致的 skills_dirs
    from floodmind.skills.skill_curator import SkillCurator
    import floodmind.skills.skill_curator as curator_mod
    curator = SkillCurator(skills_dirs=[str(tmp_path)], state_file=str(tmp_path / "curator_state.json"))
    monkeypatch.setattr(curator_mod, "_curator", curator)
    return _CrudHarness(), tmp_path


class TestSkillCrudHandlers:
    def test_create_list_update_remove_cycle(self, crud_setup):
        h, tmp = crud_setup
        assert "已创建" in h._handle_create_skill(name="my-skill", description="测试", body="## 用法\n做某事")
        assert (tmp / "my-skill" / "SKILL.md").exists()
        assert "my-skill" in h._handle_list_skills()
        assert get_skill_registry().get_skill("my-skill") is not None

        # append
        assert "已更新" in h._handle_update_skill(name="my-skill", action="append", content="## 备注\nnew")
        text = (tmp / "my-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert "备注" in text and "用法" in text

        # replace_section
        h._handle_update_skill(name="my-skill", action="replace_section", section_title="用法", content="updated")
        assert "updated" in (tmp / "my-skill" / "SKILL.md").read_text(encoding="utf-8")

        # remove → archive
        assert "归档" in h._handle_remove_skill(name="my-skill")
        assert (tmp / ".archived" / "my-skill" / "SKILL.md").exists()
        assert "my-skill" not in h._handle_list_skills()

    def test_create_duplicate_errors(self, crud_setup):
        h, _ = crud_setup
        h._handle_create_skill(name="dup", description="d", body="b")
        assert "已存在" in h._handle_create_skill(name="dup", description="d", body="b")

    def test_remove_ephemeral_disables(self, crud_setup):
        h, _ = crud_setup
        get_skill_registry().register_skill(skill_base.Skill(name="eph2", description="e", prompt="p"))
        assert "禁用" in h._handle_remove_skill(name="eph2")
        assert get_skill_registry().get_skill("eph2") is None

    def test_update_missing_skill_errors(self, crud_setup):
        h, _ = crud_setup
        assert "未找到" in h._handle_update_skill(name="nope", action="append", content="x")

    def test_create_rejects_path_traversal(self, crud_setup):
        h, tmp = crud_setup
        for bad in ("../evil", "a/b", "x\\y", "..dot"):
            assert "非法" in h._handle_create_skill(name=bad, description="d", body="b"), bad
        # 确认没有逃逸出 writable_root
        assert not (tmp.parent / "evil").exists()
