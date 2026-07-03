"""Tests for AgentTool authoring, to_tool_spec conversion, and schema hygiene (C-deep)."""

from typing import Optional

from pydantic import BaseModel, Field

from floodmind.agent.native.tool_runtime import native_from_agent_tool
from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
from floodmind.agent.runtime.contracts.tools import ToolSpec
from floodmind.tools.agent_tool import AgentTool, _sanitize_parameters, build_agent_tool


# ---------------------------------------------------------------------------
# AgentTool.to_tool_spec
# ---------------------------------------------------------------------------

class TestToToolSpec:
    def test_args_schema_parameters_strip_title(self):
        class In(BaseModel):
            x: int = Field(description="an int")

        spec = build_agent_tool(func=lambda **k: k, name="T", description="d", args_schema=In).to_tool_spec()
        assert isinstance(spec, ToolSpec)
        assert "title" not in spec.parameters  # no class-name leak
        assert "title" not in spec.parameters["properties"]["x"]
        assert spec.parameters["required"] == ["x"]

    def test_optional_anyof_collapsed(self):
        class In(BaseModel):
            q: Optional[str] = Field(default=None, description="opt")

        prop = build_agent_tool(func=lambda **k: k, name="T", description="d", args_schema=In).to_tool_spec().parameters["properties"]["q"]
        # Optional anyOf:[{string},{null}] -> {string}
        assert prop.get("type") == "string"
        assert "anyOf" not in prop

    def test_raw_parameters_mode_passes_through(self):
        raw = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
        spec = build_agent_tool(func=lambda **k: k, name="R", description="d", parameters=raw).to_tool_spec()
        assert spec.parameters == raw  # raw mode: no sanitization, unchanged

    def test_no_schema_yields_empty_object(self):
        spec = build_agent_tool(func=lambda **k: k, name="E", description="d").to_tool_spec()
        assert spec.parameters == {"type": "object", "properties": {}}

    def test_field_passthrough(self):
        def vf(d):
            return None

        def cpf(d):
            return None

        spec = build_agent_tool(
            func=lambda **k: "ok",
            name="P",
            description="desc",
            is_readonly=False,
            is_destructive=True,
            is_concurrency_safe=False,
            check_permissions_fn=cpf,
            validate_input_fn=vf,
            permission_policy=ToolPermissionPolicy(policy_type="network"),
        ).to_tool_spec()
        assert spec.name == "P" and spec.description == "desc"
        assert spec.is_readonly is False and spec.is_destructive is True and spec.is_concurrency_safe is False
        assert spec.check_permissions_fn is cpf
        assert spec.validate_input_fn is vf  # not silently None (regression for pydantic-field bug)
        assert spec.permission_policy.policy_type == "network"
        assert spec.func() == "ok"


# ---------------------------------------------------------------------------
# Bridge: native_from_agent_tool (thin normalizer)
# ---------------------------------------------------------------------------

class TestBridgeNormalize:
    def test_toolspec_passthrough_identity(self):
        ts = ToolSpec(name="X", description="d", parameters={"type": "object"}, func=lambda **k: "ok")
        assert native_from_agent_tool(ts) is ts

    def test_agent_tool_delegates_to_to_tool_spec(self):
        tool = build_agent_tool(
            func=lambda **k: k, name="A", description="d",
            parameters={"type": "object", "properties": {"a": {"type": "string"}}},
        )
        spec = native_from_agent_tool(tool)
        assert isinstance(spec, ToolSpec)
        assert spec.name == "A"
        assert spec.parameters["properties"]["a"]["type"] == "string"

    def test_duck_typed_foreign_object_routes_through_agenttool(self):
        # Non-AgentTool, non-ToolSpec object — bridge wraps as AgentTool then projects,
        # so conversion stays single-point (no duplicated getattr/schema logic).
        vf = lambda d: None  # noqa: E731

        class Foreign:
            def __init__(self):
                self.name = "F"
                self.description = "foreign"
                self.func = lambda **k: "ran"
                self.parameters = {"type": "object", "properties": {"z": {"type": "integer"}}, "required": ["z"]}
                self.validate_input_fn = vf

        spec = native_from_agent_tool(Foreign())
        assert isinstance(spec, ToolSpec)
        assert spec.name == "F"
        assert spec.func() == "ran"
        assert spec.parameters["required"] == ["z"]
        assert spec.validate_input_fn is vf  # duck-typed branch forwards all fields


# ---------------------------------------------------------------------------
# _sanitize_parameters
# ---------------------------------------------------------------------------

class TestSanitizeParameters:
    def test_strips_title_recursively(self):
        schema = {
            "title": "Top", "type": "object",
            "properties": {"a": {"title": "A", "type": "string"}},
            "required": ["a"],
        }
        out = _sanitize_parameters(schema)
        assert "title" not in out
        assert "title" not in out["properties"]["a"]
        assert out["required"] == ["a"]  # required preserved

    def test_collapses_optional_anyof(self):
        schema = {"type": "object", "properties": {
            "q": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": ""}}}
        out = _sanitize_parameters(schema)
        assert out["properties"]["q"]["type"] == "string"
        assert "anyOf" not in out["properties"]["q"]
        assert out["properties"]["q"]["default"] == ""  # sibling retained

    def test_non_optional_union_anyof_preserved(self):
        schema = {"type": "object", "properties": {
            "u": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}
        out = _sanitize_parameters(schema)
        assert out["properties"]["u"]["anyOf"] == [{"type": "string"}, {"type": "integer"}]

    def test_non_dict_returns_empty_object(self):
        assert _sanitize_parameters(None) == {"type": "object", "properties": {}}
        assert _sanitize_parameters("not a dict") == {"type": "object", "properties": {}}
