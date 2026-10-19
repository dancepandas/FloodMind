"""Skill CRUD 写后回读校验（G-10）：UpdateSkill 与 CreateSkill 对齐，失败回滚。"""

from unittest.mock import patch

from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.skills.registry import SkillRegistry


def _handler(tmp_path):
    """只装配 Skill CRUD 处理器所需的最小状态，避免完整 Agent 初始化开销。"""
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    registry = SkillRegistry(roots=[], writable_root=tmp_path / "skills")
    agent._skill_registry = registry
    agent.refresh_skills = registry.refresh
    return agent, registry


def test_update_skill_writes_and_reloads(tmp_path):
    agent, registry = _handler(tmp_path)
    assert "已创建" in agent._handle_create_skill(name="demo", description="d", body="line1")
    assert "已更新" in agent._handle_update_skill(
        name="demo", action="append", content="line2"
    )
    text = (tmp_path / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8")
    assert "line1" in text
    assert "line2" in text
    assert registry.get_skill("demo") is not None


def test_update_skill_rolls_back_when_readback_mismatch(tmp_path):
    agent, _ = _handler(tmp_path)
    assert "已创建" in agent._handle_create_skill(name="demo", description="d", body="line1")
    path = tmp_path / "skills" / "demo" / "SKILL.md"
    before = path.read_text(encoding="utf-8")

    with patch("floodmind.agent.native.native_flood_agent.yaml.safe_load", return_value={}):
        result = agent._handle_update_skill(name="demo", action="append", content="line2")

    assert "已回滚" in result
    assert path.read_text(encoding="utf-8") == before


def test_create_skill_removes_file_when_readback_mismatch(tmp_path):
    agent, _ = _handler(tmp_path)

    with patch("floodmind.agent.native.native_flood_agent.yaml.safe_load", return_value={}):
        result = agent._handle_create_skill(name="demo", description="d", body="line1")

    assert "已回滚" in result
    assert not (tmp_path / "skills" / "demo" / "SKILL.md").exists()
