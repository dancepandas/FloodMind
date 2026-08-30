"""Tests for SkillRegistry + skill CRUD handlers (skill unification)."""

import logging
import os
import threading
from pathlib import Path

import pytest

from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.skills import base as skill_base
import floodmind.skills.registry as skill_reg_mod
from floodmind.skills.registry import (
    SkillRegistry,
    SkillRoot,
    create_skill_registry,
    default_roots,
    get_skill_registry,
)


def _make_skill_md(root: Path, name: str, desc: str = "d", body: str = "body") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n", encoding="utf-8"
    )
    return d


def _create_junction(src: Path, dst: Path) -> Path:
    """Windows junction（兼容 3.10：Path 无 mkdir(junctions=...)，用 _winapi）。"""
    import _winapi

    _winapi.CreateJunction(str(src), str(dst))
    return dst


_junction_only = pytest.mark.skipif(os.name != "nt", reason="junction 仅 Windows")


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

    def test_shadowed_ephemeral_survives_refresh(self, tmp_path):
        reg = SkillRegistry(roots=[tmp_path], writable_root=tmp_path)
        reg.register_skill(skill_base.Skill(name="same", description="e", prompt="ephemeral"))
        disk_dir = _make_skill_md(tmp_path, "same", body="disk")
        reg.refresh()
        assert reg.get_skill("same").prompt == "disk"
        (disk_dir / "SKILL.md").unlink()
        reg.refresh()
        assert reg.get_skill("same").prompt == "ephemeral"

    def test_register_disk_name_keeps_disk_precedence_and_ephemeral_fallback(self, tmp_path):
        disk_dir = _make_skill_md(tmp_path, "same", body="disk")
        reg = SkillRegistry(roots=[tmp_path], writable_root=tmp_path)

        reg.register_skill(
            skill_base.Skill(name="same", description="e", prompt="ephemeral")
        )
        assert reg.get_skill("same").prompt == "disk"
        assert reg.get_skill_root("same").path == tmp_path.resolve()

        (disk_dir / "SKILL.md").unlink()
        reg.refresh()
        assert reg.get_skill("same").prompt == "ephemeral"
        assert reg.get_skill_root("same") is None

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
        assert items["s1"]["origin"] == "host"
        assert items["s1"]["read_only"] is False
        assert reg.get_skill_source("s1") == tmp_path.resolve() / "s1"
        assert "name: s1" in reg.get_skill_content("s1")
        assert reg.load_skill("s1") == reg.get_skill_content("s1")

    def test_writable_root_default_is_project_skills(self):
        reg = SkillRegistry()
        assert reg.writable_root.name == "skills"

    def test_factory_defaults_and_metadata(self):
        reg = create_skill_registry()
        assert reg.roots == [path.expanduser().resolve() for path in default_roots()]
        assert [spec.origin for spec in reg.root_specs] == [
            "builtin", "project", "claude_compat"
        ]
        assert [spec.priority for spec in reg.root_specs] == [500, 300, 200]
        assert reg.root_specs[0].read_only is True
        assert reg.root_specs[1].read_only is False
        copy = reg.root_specs
        copy.clear()
        assert len(reg.root_specs) == 3

    def test_factory_normalizes_dedupes_and_includes_custom_writable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SKILL_ROOT_HOME", str(tmp_path))
        host = tmp_path / "host"
        writable = tmp_path / "writable"
        reg = create_skill_registry(
            additional_roots=[host / ".." / "host", host],
            writable_root=writable,
        )
        host_specs = [spec for spec in reg.root_specs if spec.origin == "host"]
        assert [spec.path for spec in host_specs] == [host.resolve(), writable.resolve()]
        assert host_specs[0].read_only is True
        assert host_specs[1].read_only is False
        project_spec = next(spec for spec in reg.root_specs if spec.origin == "project")
        assert project_spec.read_only is True
        assert len({os.path.normcase(str(path)) for path in reg.roots}) == len(reg.roots)

    def test_factory_same_additional_and_writable_root_is_writable(self, tmp_path):
        host = tmp_path / "shared-host"
        reg = create_skill_registry(additional_roots=[host], writable_root=host)
        matching = [spec for spec in reg.root_specs if spec.path == host.resolve()]
        assert len(matching) == 1
        assert matching[0].origin == "host"
        assert matching[0].priority == 400
        assert matching[0].order == 0
        assert matching[0].read_only is False
        assert reg.writable_root == host.resolve()

    def test_factory_rejects_read_only_writable_root(self):
        with pytest.raises(ValueError, match="只读根"):
            create_skill_registry(writable_root=default_roots()[0])

    def test_factory_rejects_existing_writable_symlink(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink unavailable")
        with pytest.raises(ValueError, match="符号链接"):
            create_skill_registry(writable_root=link)

    def test_writable_skill_path_rejects_symlinked_skill_md_leaf(self, tmp_path):
        root = tmp_path / "skills"
        skill_dir = root / "victim"
        skill_dir.mkdir(parents=True)
        outside = tmp_path / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        leaf = skill_dir / "SKILL.md"
        try:
            leaf.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlink unavailable")

        reg = SkillRegistry(roots=[root], writable_root=root)
        with pytest.raises(ValueError, match="符号链接"):
            reg.validate_writable_skill_path("victim")

    def test_writable_skill_path_rejects_symlinked_existing_ancestor(self, tmp_path):
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        linked_parent = tmp_path / "linked-parent"
        try:
            linked_parent.symlink_to(real_parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink unavailable")

        with pytest.raises(ValueError, match="符号链接"):
            SkillRegistry(roots=[linked_parent], writable_root=linked_parent)

    @_junction_only
    def test_writable_root_rejects_junction(self, tmp_path):
        """Windows junction 不是 Path.is_symlink()，但必须同样被拒绝。"""
        target = tmp_path / "real-skills"
        target.mkdir()
        junction = _create_junction(target, tmp_path / "junction-skills")
        assert not junction.is_symlink()  # 前置：junction 不算 symlink
        with pytest.raises(ValueError, match="junction"):
            SkillRegistry(roots=[junction], writable_root=junction)

    @_junction_only
    def test_writable_skill_dir_rejects_junction_skill_dir(self, tmp_path):
        """writable_root 下已是 junction 的 skill 目录同样拒绝。"""
        root = tmp_path / "skills"
        root.mkdir()
        real = tmp_path / "elsewhere"
        real.mkdir()
        _create_junction(real, root / "victim")
        reg = SkillRegistry(roots=[root], writable_root=root)
        with pytest.raises(ValueError, match="junction"):
            reg.writable_skill_dir("victim")

    @_junction_only
    def test_discover_skills_skips_junction_dirs(self, tmp_path):
        """发现阶段对根下的 junction 目录拒绝加载（防止越权来源混入）。"""
        from floodmind.skills.base import discover_skills

        _make_skill_md(tmp_path, "real")
        outside = tmp_path / "outside"
        _make_skill_md(outside, "smuggled")
        _create_junction(outside, tmp_path / "jdir")

        names = {s.name for s in discover_skills(tmp_path)}
        assert "real" in names
        assert "smuggled" not in names

    @pytest.mark.parametrize(
        "name",
        [
            "CON", "nul.txt", "COM1", "LPT9.md",
            "bad:name", "bad*name", "bad?name", "bad|name",
            "trailing.", "trailing ", " leading", "control\x1fchar",
        ],
    )
    def test_validate_skill_name_rejects_nonportable_windows_names(self, tmp_path, name):
        reg = SkillRegistry(roots=[tmp_path], writable_root=tmp_path)
        with pytest.raises(ValueError, match="非法 skill name"):
            reg.writable_skill_path(name)

    def test_priority_and_stable_host_order_with_duplicate_warning(self, tmp_path, caplog):
        host_one = tmp_path / "host-one"
        host_two = tmp_path / "host-two"
        project = tmp_path / "project"
        for root, body in ((host_one, "first"), (host_two, "second"), (project, "project")):
            _make_skill_md(root, "duplicate", body=body)
        reg = SkillRegistry(
            root_specs=[
                SkillRoot(project, "project", 300, False, 0),
                SkillRoot(host_two, "host", 400, True, 1),
                SkillRoot(host_one, "host", 400, True, 0),
            ],
            writable_root=project,
        )
        with caplog.at_level(logging.WARNING):
            reg.refresh()
        assert reg.get_skill("duplicate").prompt == "first"
        assert reg.get_skill_root("duplicate").path == host_one.resolve()
        assert "重复 skill 'duplicate'" in caplog.text

    def test_refresh_scan_does_not_hold_lock_and_retries_concurrent_mutation(self, tmp_path, monkeypatch):
        reg = SkillRegistry(roots=[tmp_path], writable_root=tmp_path)
        started = threading.Event()
        release = threading.Event()
        original = skill_reg_mod.discover_skills

        def slow_discover(path):
            started.set()
            assert release.wait(2)
            return original(path)

        monkeypatch.setattr(skill_reg_mod, "discover_skills", slow_discover)
        worker = threading.Thread(target=reg.refresh)
        worker.start()
        assert started.wait(2)

        # This mutation must not block behind filesystem discovery, and the
        # refresh must retry rather than overwrite the new programmatic skill.
        reg.register_skill(skill_base.Skill(name="during-scan", description="d"))
        release.set()
        worker.join(2)

        assert not worker.is_alive()
        assert reg.get_skill("during-scan") is not None

    def test_callbacks_unsubscribe_remove_and_lambda_lifetime(self, tmp_path):
        reg = SkillRegistry(roots=[tmp_path], writable_root=tmp_path)
        calls = []
        unsubscribe = reg.add_refresh_callback(lambda: calls.append("lambda"))
        reg.refresh()
        assert calls == ["lambda"]
        unsubscribe()
        reg.refresh()
        assert calls == ["lambda"]

        def callback():
            calls.append("function")

        reg.add_refresh_callback(callback)
        reg.remove_refresh_callback(callback)
        reg.refresh()
        assert calls == ["lambda"]

    def test_legacy_constructor_root_is_writable(self, tmp_path):
        reg = SkillRegistry(roots=[tmp_path / "."], writable_root=tmp_path)
        assert reg.roots == [tmp_path.resolve()]
        assert reg.root_specs[0].origin == "host"
        assert reg.root_specs[0].read_only is False
        assert reg.writable_skill_path("new-skill") == tmp_path.resolve() / "new-skill" / "SKILL.md"

    def test_global_singleton_compatibility(self, tmp_path, monkeypatch):
        reg = SkillRegistry(roots=[tmp_path], writable_root=tmp_path)
        monkeypatch.setattr(skill_reg_mod, "_registry", reg)
        assert get_skill_registry() is reg
        skill_base.register_skill(skill_base.Skill(name="global-eph", description="d"))
        assert get_skill_registry().get_skill("global-eph") is not None


# ---------------------------------------------------------------------------
# CRUD handlers (harness + isolated singleton)
# ---------------------------------------------------------------------------

class _CrudHarness:
    """Binds the real CRUD handlers to an explicit isolated registry/curator."""

    def __init__(self, registry, curator):
        self._skill_catalog = ""
        self._skill_registry = registry
        self._skill_curator = curator

    def refresh_skills(self):
        self._skill_registry.refresh()
        self._skill_catalog = self._skill_registry.catalog()

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
    curator = SkillCurator(registry=reg, state_file=str(tmp_path / "curator_state.json"))
    monkeypatch.setattr(curator_mod, "_curator", curator)
    return _CrudHarness(reg, curator), tmp_path


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

    def test_create_skill_frontmatter_survives_yaml_specials(self, crud_setup):
        """description 含冒号/换行/引号时 frontmatter 不被破坏（yaml.safe_dump 构造）。"""
        import yaml as _yaml

        h, tmp = crud_setup
        desc = "第一行: 带冒号\n第二行 '含引号' 与 # 注释符"
        assert "已创建" in h._handle_create_skill(name="yaml-safe", description=desc, body="b")
        text = (tmp / "yaml-safe" / "SKILL.md").read_text(encoding="utf-8")
        fm, _ = h._split_skill_md(text)
        parsed = _yaml.safe_load(fm)
        assert parsed["name"] == "yaml-safe"
        assert parsed["description"] == desc
        # 技能仍能正常加载
        assert get_skill_registry().get_skill("yaml-safe") is not None

    def test_remove_ephemeral_rejected(self, crud_setup):
        h, _ = crud_setup
        h._skill_registry.register_skill(skill_base.Skill(name="eph2", description="e", prompt="p"))
        assert "ephemeral" in h._handle_remove_skill(name="eph2")
        assert h._skill_registry.get_skill("eph2") is not None

    def test_update_missing_skill_errors(self, crud_setup):
        h, _ = crud_setup
        assert "未找到" in h._handle_update_skill(name="nope", action="append", content="x")

    def test_create_rejects_path_traversal(self, crud_setup):
        h, tmp = crud_setup
        for bad in ("../evil", "a/b", "x\\y", "..dot"):
            assert "非法" in h._handle_create_skill(name=bad, description="d", body="b"), bad
        # 确认没有逃逸出 writable_root
        assert not (tmp.parent / "evil").exists()
