"""Tests for skill curator lifecycle management."""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from floodmind.skills.skill_curator import (
    SkillCurator,
    SkillStat,
    SkillUsageRecord,
    get_skill_curator,
    record_skill_usage,
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
        """Archive moves skill dir to .archive/."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "test-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: test-skill\n---\n")

            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            curator.record_usage("test-skill", success=True)
            success = curator.archive_skill("test-skill")
            assert success
            assert not skill_dir.exists()
            assert (Path(tmp) / ".archive" / "test-skill" / "SKILL.md").exists()
            assert curator.get_skill_stat("test-skill").status == "archived"

    def test_archive_nonexistent_returns_false(self):
        """Archiving non-existent skill returns False."""
        with tempfile.TemporaryDirectory() as tmp:
            curator = SkillCurator(skills_dirs=[tmp], state_file=os.path.join(tmp, "state.json"))
            assert not curator.archive_skill("nonexistent")

    def test_restore_moves_back(self):
        """Restore moves skill back from .archive/."""
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
