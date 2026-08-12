"""Focused tests for instance-bound GetSkill tools."""

from floodmind.skills.base import Skill
from floodmind.tools.base_tools import make_get_skill_tool


class FakeRegistry:
    def __init__(self, skills):
        self.skills = {skill.name: skill for skill in skills}
        self.callbacks = []

    def get_skill(self, name):
        return self.skills.get(name)

    def all_skills(self):
        return list(self.skills.values())

    def add_refresh_callback(self, callback):
        self.callbacks.append(callback)
        active = True

        def unsubscribe():
            nonlocal active
            if active:
                active = False
                self.callbacks.remove(callback)

        return unsubscribe

    def refresh(self):
        for callback in list(self.callbacks):
            callback()


class FakeCurator:
    def __init__(self):
        self.calls = []

    def record_usage(self, skill_name, success=True, session_id=""):
        self.calls.append((skill_name, success))


def _skill(prompt):
    return Skill(name="shared", description="description", prompt=prompt)


def test_get_skill_factory_isolates_same_named_registry_content_and_usage():
    curator_a = FakeCurator()
    curator_b = FakeCurator()
    tool_a = make_get_skill_tool(FakeRegistry([_skill("prompt A")]), curator_a)
    tool_b = make_get_skill_tool(FakeRegistry([_skill("prompt B")]), curator_b)

    assert "prompt A" in tool_a.func("shared")
    assert "prompt B" in tool_b.func("shared")
    assert curator_a.calls == [("shared", True)]
    assert curator_b.calls == [("shared", True)]

    tool_a.cleanup()
    tool_b.cleanup()


def test_registry_refresh_invalidates_only_its_tool_cache():
    registry_a = FakeRegistry([_skill("first A")])
    registry_b = FakeRegistry([_skill("first B")])
    tool_a = make_get_skill_tool(registry_a, FakeCurator())
    tool_b = make_get_skill_tool(registry_b, FakeCurator())

    assert "first A" in tool_a.func("shared")
    assert "first B" in tool_b.func("shared")
    registry_a.skills["shared"] = _skill("second A")
    registry_b.skills["shared"] = _skill("second B")
    registry_a.refresh()

    assert "second A" in tool_a.func("shared")
    assert "first B" in tool_b.func("shared")

    tool_a.cleanup()
    tool_b.cleanup()


def test_cleanup_unsubscribes_refresh_callback_idempotently():
    registry = FakeRegistry([_skill("prompt")])
    tool = make_get_skill_tool(registry, FakeCurator())

    assert len(registry.callbacks) == 1
    tool.cleanup()
    tool.cleanup()
    assert registry.callbacks == []
