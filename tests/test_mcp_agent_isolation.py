"""Focused MCP pool and model-visible name isolation tests."""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.mcp_client import McpClientPool
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.native_flood_agent import NativeFloodAgent


class FakeConn:
    def __init__(self, name, tools):
        self.name = name
        self.transport = "sse"
        self._tools = list(tools)
        self.disconnected = False

    def list_tools(self):
        return list(self._tools)

    @property
    def is_connected(self):
        return not self.disconnected

    def disconnect(self):
        self.disconnected = True

    def call_tool(self, tool_name, arguments):
        return f"{self.name}:{tool_name}"


def _agent(monkeypatch, pool=None):
    from floodmind.config.settings import settings

    monkeypatch.setattr(settings.mcp, "servers", [])
    return NativeFloodAgent(
        llm_service=ModelClient(api_key="k", base_url="http://mock/v1", model_name="m"),
        memory=None,
        session_id="mcp-isolation",
        bare=True,
        tools=[],
        mcp_pool=pool,
    )


def test_two_agents_can_use_same_server_name_without_cross_lookup(monkeypatch):
    first = _agent(monkeypatch)
    second = _agent(monkeypatch)
    assert first._mcp_pool is not second._mcp_pool

    c1 = FakeConn("same", [{"name": "lookup"}])
    c2 = FakeConn("same", [{"name": "lookup"}])
    first._mcp_pool._connections["same"] = c1
    second._mcp_pool._connections["same"] = c2
    first._register_mcp_connection("same", c1)
    second._register_mcp_connection("same", c2)

    assert first._orchestrator_registry.get("mcp_same_lookup").func() == "same:lookup"
    assert second._orchestrator_registry.get("mcp_same_lookup").func() == "same:lookup"
    first.cleanup()
    assert c1.disconnected is True
    assert c2.disconnected is False


def test_dynamic_load_returns_real_spec_names_and_refreshes(monkeypatch):
    agent = _agent(monkeypatch)
    conn = FakeConn("weather", [{"name": "current:conditions"}])
    agent._mcp_pool.connect_server = MagicMock(return_value=conn)
    agent._orchestrator_executor = MagicMock()
    agent._orchestrator_executor.system_prompts = ["one", "two"]
    agent._specialist_executor = MagicMock()
    agent._specialist_executor.system_prompts = ["one"]

    result = agent._handle_load_mcp_server(name="weather", transport="sse", url="http://x")

    assert "mcp_weather_current_conditions" in result
    agent._orchestrator_executor.set_tools_schema.assert_called_once()
    agent._specialist_executor.set_tools_schema.assert_called_once()


def test_sanitized_collision_rejected_without_overwrite_or_unrelated_removal(monkeypatch):
    agent = _agent(monkeypatch)
    first = FakeConn("srv", [{"name": "a:b"}])
    second = FakeConn("srv_", [{"name": "a_b"}])
    agent._mcp_pool._connections["srv"] = first
    agent._register_mcp_connection("srv", first)

    # srv/a:b and srv_/a_b are deliberately chosen to collide after sanitization.
    # Ensure the collision is explicit and the original binding remains intact.
    colliding = FakeConn("srv", [{"name": "a_b"}])
    with pytest.raises(ValueError, match="名称冲突"):
        agent._register_mcp_connection("srv", colliding)
    original = agent._orchestrator_registry.get("mcp_srv_a_b")
    assert original is not None
    assert original.func() == "srv:a:b"


def test_borrowed_pool_cleanup_closes_only_agent_owned_connections(monkeypatch):
    pool = McpClientPool()
    borrowed = FakeConn("borrowed", [])
    owned = FakeConn("owned", [])
    pool._connections.update({"borrowed": borrowed, "owned": owned})
    agent = _agent(monkeypatch, pool=pool)
    agent._mcp_owned_connections.add("owned")

    agent.cleanup()

    assert owned.disconnected is True
    assert borrowed.disconnected is False
    assert pool.get_server_info("borrowed") is not None
