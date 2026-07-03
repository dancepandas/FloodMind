"""Tests for MCP pool lifecycle, build_mcp_tool_specs, and registry unregister_prefix.

Covers the MCP unification primitives (MCP-B): the single ToolSpec construction
point, the runtime hot-plug lifecycle (list / get / disconnect), and the scoped
registry unregister used to clean up a disconnected server's tools.
"""

from floodmind.agent.mcp_client import McpClientPool, build_mcp_tool_specs
from floodmind.agent.native.native_flood_agent import _InstanceToolRegistry
from floodmind.agent.runtime.contracts.tools import ToolSpec


class FakeConn:
    """Stand-in for McpClientConnection — no network, records disconnect."""

    def __init__(self, name, tools, transport="sse"):
        self.name = name
        self.transport = transport
        self._tools = tools
        self._connected = True
        self.disconnected = False

    def list_tools(self):
        return list(self._tools)

    @property
    def is_connected(self):
        return self._connected

    def disconnect(self):
        self._connected = False
        self.disconnected = True


class TestBuildMcpToolSpecs:
    def test_builds_specs_with_correct_fields(self):
        conn = FakeConn("srv", [{"name": "t1", "description": "d1", "inputSchema": {"properties": {"a": {"type": "string"}}, "required": ["a"]}}])
        calls = []
        specs = build_mcp_tool_specs(conn, "srv", lambda fn, kw: calls.append((fn, kw)) or "ok")
        assert len(specs) == 1
        s = specs[0]
        assert s.name == "mcp:srv:t1"
        assert s.description == "[MCP:srv] d1"
        assert s.parameters["required"] == ["a"]
        assert s.permission_policy.policy_type == "network"
        assert s.is_destructive is True
        # closure dispatches to call_tool_fn with full name + kwargs
        assert s.func(a="x") == "ok"
        assert calls == [("mcp:srv:t1", {"a": "x"})]

    def test_each_closure_captures_own_tool_name(self):
        conn = FakeConn("srv", [{"name": "t1"}, {"name": "t2"}])
        specs = build_mcp_tool_specs(conn, "srv", lambda fn, kw: fn)
        assert sorted(s.func() for s in specs) == ["mcp:srv:t1", "mcp:srv:t2"]


class TestPoolLifecycle:
    def _pool_with(self, conns):
        pool = McpClientPool()
        for c in conns:
            pool._connections[c.name] = c
        return pool

    def test_list_servers(self):
        pool = self._pool_with([
            FakeConn("a", [{"name": "t1"}]),
            FakeConn("b", [{"name": "t1"}, {"name": "t2"}], transport="stdio"),
        ])
        info = {s["name"]: s for s in pool.list_servers()}
        assert info["a"]["tools"] == 1 and info["a"]["transport"] == "sse" and info["a"]["connected"] is True
        assert info["b"]["tools"] == 2 and info["b"]["transport"] == "stdio"

    def test_get_server_info_not_found(self):
        pool = self._pool_with([FakeConn("a", [])])
        assert pool.get_server_info("nope") is None
        assert pool.get_server_info("a")["tools"] == []

    def test_disconnect_server_removes_conn_and_disconnects(self):
        c = FakeConn("a", [{"name": "t1"}])
        pool = self._pool_with([c])
        assert pool.disconnect_server("a") is True
        assert c.disconnected is True
        assert pool.get_server_info("a") is None  # removed from pool

    def test_disconnect_server_missing_returns_false(self):
        pool = McpClientPool()
        assert pool.disconnect_server("nope") is False


class TestRegistryUnregisterPrefix:
    def test_removes_matching_keeps_others(self):
        reg = _InstanceToolRegistry()
        for n in ["mcp:srv:t1", "mcp:srv:t2", "mcp:srv2:other", "Read", "Write"]:
            reg.register(ToolSpec(name=n, description="d", parameters={"type": "object"}, func=lambda **k: "ok"))
        removed = reg.unregister_prefix("mcp:srv:")
        assert removed == 2
        # 'mcp:srv:' must NOT match 'mcp:srv2:' (prefix isolation), and non-mcp tools untouched
        assert {t.name for t in reg.all()} == {"mcp:srv2:other", "Read", "Write"}

    def test_no_match_returns_zero(self):
        reg = _InstanceToolRegistry()
        reg.register(ToolSpec(name="Read", description="d", parameters={"type": "object"}, func=lambda **k: "ok"))
        assert reg.unregister_prefix("mcp:x:") == 0
        assert len(reg.all()) == 1
