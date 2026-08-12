"""Tests for skill curator lifecycle management."""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from floodmind.skills.registry import SkillRegistry
from floodmind.skills.skill_curator import (
    SkillCurator,
    SkillStat,
    SkillUsageRecord,
    get_skill_curator,
    record_skill_usage,
    run_maintenance_if_needed,
)


class TestSkillCuratorBasics:
    """Test SkillCurator core tracking."""

    def test_record_usage_creates_stat(self):
        """First usage creates a new SkillStat."""
        with tempfile.TemporaryDirectory() as tmp:
            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            curator.record_usage("test-skill", success=True)
            stat = curator.get_skill_stat("test-skill")
            assert stat is not None
            assert stat.total_uses == 1
            assert stat.success_count == 1

    def test_record_usage_increments(self):
        """Multiple usages increment counters."""
        with tempfile.TemporaryDirectory() as tmp:
            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            curator.record_usage("test-skill", success=True)
            curator.record_usage("test-skill", success=False)
            stats = curator.get_stats()
            assert stats[0]["total_uses"] == 2
            assert stats[0]["success_rate"] == 0.5

    def test_record_failure(self):
        """Failure increments failure_count."""
        with tempfile.TemporaryDirectory() as tmp:
            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            curator.record_usage("test-skill", success=False)
            stat = curator.get_skill_stat("test-skill")
            assert stat.failure_count == 1

    def test_reactivate_stale_skill(self):
        """Usage reactivates a stale skill."""
        with tempfile.TemporaryDirectory() as tmp:
            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            curator.record_usage("test-skill", success=True)
            curator._stats["test-skill"].status = "stale"
            curator.record_usage("test-skill", success=True)
            assert curator.get_skill_stat("test-skill").status == "active"

    def test_persistence(self):
        """State survives curator recreation."""
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            c1 = SkillCurator(skills_dirs=[tmp], state_file=state_file)
            c1.record_usage("test-skill", success=True)

            c2 = SkillCurator(skills_dirs=[tmp], state_file=state_file)
            stat = c2.get_skill_stat("test-skill")
            assert stat is not None
            assert stat.total_uses == 1

    def test_same_state_path_stale_instances_merge_usage(self, tmp_path):
        state_file = tmp_path / "state.json"
        root = tmp_path / "skills"
        root.mkdir()
        first = SkillCurator(skills_dirs=[str(root)], state_file=str(state_file))
        second = SkillCurator(skills_dirs=[str(root)], state_file=str(state_file))

        first.record_usage("shared", success=True)
        second.record_usage("shared", success=False)
        first.record_usage("shared", success=True)

        reloaded = SkillCurator(skills_dirs=[str(root)], state_file=str(state_file))
        stat = reloaded.get_skill_stat("shared")
        assert stat.total_uses == 3
        assert stat.success_count == 2
        assert stat.failure_count == 1
        assert not list(tmp_path.glob("state.json.*.tmp"))
    def test_record_usage_batch_persists_once_and_merges_stale_instance(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        root = tmp_path / "skills"
        root.mkdir()
        first = SkillCurator(skills_dirs=[str(root)], state_file=str(state_file))
        second = SkillCurator(skills_dirs=[str(root)], state_file=str(state_file))
        first.record_usage("shared", success=True)
        saves = []
        original_save = second._save

        def counted_save():
            saves.append("save")
            original_save()

        monkeypatch.setattr(second, "_save", counted_save)
        second.record_usage_batch([
            SkillUsageRecord("shared", "", False, "a"),
            SkillUsageRecord("shared", "", True, "b"),
        ])

        assert saves == ["save"]
        reloaded = SkillCurator(skills_dirs=[str(root)], state_file=str(state_file))
        stat = reloaded.get_skill_stat("shared")
        assert stat.total_uses == 3
        assert stat.success_count == 2
        assert stat.failure_count == 1


class TestSkillCuratorStaleDetection:
    """Test stale skill detection."""

    def test_no_stale_when_recent(self):
        """Recently used skill is not stale."""
        with tempfile.TemporaryDirectory() as tmp:
            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"), stale_days=30)
            curator.record_usage("test-skill", success=True)
            stale = curator.find_stale_skills()
            assert len(stale) == 0

    def test_stale_when_old(self):
        """Old unused skill is detected as stale."""
        with tempfile.TemporaryDirectory() as tmp:
            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"), stale_days=1)
            curator.record_usage("test-skill", success=True)
            # Manually backdate
            curator._stats["test-skill"].last_used_at = (datetime.now() - timedelta(days=2)).isoformat()
            stale = curator.find_stale_skills()
            assert len(stale) == 1
            assert stale[0].skill_name == "test-skill"


class TestSkillCuratorArchive:
    """Test skill archive and restore."""

    def test_archive_moves_directory(self):
        """Archive moves skill dir to .archived/."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "test-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: test-skill\n---\n")

            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            curator.record_usage("test-skill", success=True)
            success = curator.archive_skill("test-skill")
            assert success
            assert not skill_dir.exists()
            assert (Path(tmp) / ".archived" / "test-skill" / "SKILL.md").exists()
            assert curator.get_skill_stat("test-skill").status == "archived"

    def test_archive_nonexistent_returns_false(self):
        """Archiving non-existent skill returns False."""
        with tempfile.TemporaryDirectory() as tmp:
            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            assert not curator.archive_skill("nonexistent")

    def test_restore_moves_back(self):
        """Restore moves skill back from .archived/."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "test-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("test")

            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            curator.record_usage("test-skill", success=True)  # ensure stat exists
            curator.archive_skill("test-skill")
            success = curator.restore_skill("test-skill")
            assert success
            assert skill_dir.exists()
            assert curator.get_skill_stat("test-skill").status == "active"

    def test_list_archived(self):
        """list_archived returns archived skill names."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "test-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("test")

            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            curator.archive_skill("test-skill")
            archived = curator.list_archived()
            assert "test-skill" in archived


class TestSkillCuratorDuplicates:
    """Test duplicate skill detection."""

    def test_no_duplicates_when_different(self):
        """Very different skills have low similarity."""
        with tempfile.TemporaryDirectory() as tmp:
            s1 = Path(tmp) / "skill-a"
            s1.mkdir()
            (s1 / "SKILL.md").write_text("---\nname: skill-a\ndescription: data analysis\n---\nAnalyze data.")

            s2 = Path(tmp) / "skill-b"
            s2.mkdir()
            (s2 / "SKILL.md").write_text("---\nname: skill-b\ndescription: image generation\n---\nGenerate images.")

            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            dups = curator.find_duplicates(threshold=0.9)
            assert len(dups) == 0

    def test_finds_similar_skills(self):
        """Near-identical skills are detected as duplicates."""
        with tempfile.TemporaryDirectory() as tmp:
            s1 = Path(tmp) / "skill-a"
            s1.mkdir()
            (s1 / "SKILL.md").write_text("---\nname: skill-a\ndescription: run hydro model\n---\nRun the hydro model.")

            s2 = Path(tmp) / "skill-b"
            s2.mkdir()
            (s2 / "SKILL.md").write_text("---\nname: skill-b\ndescription: run hydro model\n---\nRun the hydro model.")

            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            dups = curator.find_duplicates(threshold=0.7)
            assert len(dups) >= 1
            assert dups[0][2] > 0.7  # similarity score


class TestSkillCuratorMaintenance:
    """Test maintenance run."""

    def test_maintenance_marks_stale(self):
        """Maintenance marks old skills as stale."""
        with tempfile.TemporaryDirectory() as tmp:
            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"), stale_days=1)
            curator.record_usage("test-skill", success=True)
            curator._stats["test-skill"].last_used_at = (datetime.now() - timedelta(days=2)).isoformat()

            report = curator.run_maintenance()
            assert report["stale_marked"] == 1
            assert curator.get_skill_stat("test-skill").status == "stale"

    def test_maintenance_archives_very_old(self):
        """Maintenance archives skills stale for too long."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "test-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("test")

            curator = SkillCurator(
                skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"),
                stale_days=1, archive_days=1,
            )
            curator.record_usage("test-skill", success=True)
            curator._stats["test-skill"].last_used_at = (datetime.now() - timedelta(days=3)).isoformat()
            curator._stats["test-skill"].status = "stale"

            report = curator.run_maintenance()
            assert report["archived"] == 1


class TestSkillCuratorGlobalInstance:
    """Test global curator instance."""

    def test_get_skill_curator_returns_singleton(self):
        """get_skill_curator returns the same instance."""
        c1 = get_skill_curator()
        c2 = get_skill_curator()
        assert c1 is c2

    def test_record_skill_usage_convenience(self):
        """record_skill_usage convenience function works."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch("floodmind.skills.skill_curator.get_skill_curator") as mock_get:
                mock_curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
                mock_get.return_value = mock_curator
                record_skill_usage("test-skill", success=True)
                assert mock_curator.get_skill_stat("test-skill").total_uses == 1

    def test_global_singleton_keeps_legacy_state_path(self):
        import floodmind.skills.skill_curator as module

        old = module._curator
        module._curator = None
        try:
            with patch.object(module, "get_skill_registry") as get_registry:
                get_registry.return_value = SkillRegistry(roots=[], writable_root=Path.cwd() / "skills")
                first = module.get_skill_curator()
                second = module.get_skill_curator()
            assert first is second
            assert first.state_file == Path(".floodmind/skill_curator.json")
        finally:
            module._curator = old


class TestSkillCuratorRegistryBinding:
    @staticmethod
    def _write_skill(root: Path, name: str, description: str = "skill") -> Path:
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n{description}",
            encoding="utf-8",
        )
        return skill_dir

    def test_registry_binding_defaults_state_and_archive_under_writable_root(self, tmp_path):
        writable = tmp_path / "writable"
        readonly = tmp_path / "readonly"
        self._write_skill(writable, "owned")
        self._write_skill(readonly, "external")
        registry = SkillRegistry(roots=[readonly, writable], writable_root=writable)

        curator = SkillCurator(registry=registry)

        assert curator.registry is registry
        assert curator.state_file == writable / ".floodmind" / "skill-curator.json"
        assert curator.archive_root == writable / ".archived"

    def test_separate_state_and_archive_roots(self, tmp_path):
        writable = tmp_path / "skills"
        state = tmp_path / "state" / "curator.json"
        archive = tmp_path / "archive"
        self._write_skill(writable, "owned")
        registry = SkillRegistry(roots=[writable], writable_root=writable)
        curator = SkillCurator(registry=registry, state_file=str(state), archive_root=archive)

        curator.record_usage("owned")
        assert curator.archive_skill("owned")
        assert state.is_file()
        assert (archive / "owned" / "SKILL.md").is_file()
        assert curator.restore_skill("owned")
        assert (writable / "owned" / "SKILL.md").is_file()

    def test_readonly_skill_cannot_be_archived(self, tmp_path):
        writable = tmp_path / "writable"
        readonly = tmp_path / "readonly"
        writable.mkdir()
        readonly_skill = self._write_skill(readonly, "external")
        registry = SkillRegistry(roots=[readonly], writable_root=writable)
        curator = SkillCurator(registry=registry)

        assert not curator.archive_skill("external")
        assert readonly_skill.is_dir()
        assert not (writable / ".archived" / "external").exists()

    def test_registry_duplicates_use_loaded_winners_only(self, tmp_path):
        writable = tmp_path / "writable"
        readonly = tmp_path / "readonly"
        self._write_skill(readonly, "duplicate", "same hydro model")
        self._write_skill(writable, "duplicate", "same hydro model")
        self._write_skill(writable, "other", "same hydro model")
        registry = SkillRegistry(roots=[readonly, writable], writable_root=writable)
        curator = SkillCurator(registry=registry)

        duplicates = curator.find_duplicates(threshold=0.5)
        assert [(a, b) for a, b, _ in duplicates] == [("duplicate", "other")]

    def test_explicit_maintenance_curator_uses_instance_marker(self, tmp_path):
        writable = tmp_path / "skills"
        writable.mkdir()
        registry = SkillRegistry(roots=[writable], writable_root=writable)
        curator = SkillCurator(registry=registry)

        with patch.object(curator, "run_maintenance", return_value={"ok": True}) as run:
            result = run_maintenance_if_needed(curator=curator, force=True)

        assert result == {"ok": True}
        run.assert_called_once_with()
        assert (writable / ".floodmind" / ".last-skill-maintenance").is_file()
