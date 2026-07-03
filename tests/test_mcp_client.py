"""Tests for MCP pool lifecycle, build_mcp_tool_specs, and registry unregister_prefix.

Covers the MCP unification primitives (MCP-B): the single ToolSpec construction
point, the runtime hot-plug lifecycle (list / get / disconnect), and the scoped
registry unregister used to clean up a disconnected server's tools.
"""

from floodmind.agent.mcp_client import McpClientPool, build_mcp_tool_specs
from floodmind.agent.native.native_flood_agent import NativeFloodAgent, _InstanceToolRegistry
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


class _McpMgmtHarness:
    """Lightweight stand-in binding the real MCP management handlers to a pool + two
    registries, so the handlers can be exercised without constructing a full agent."""

    def __init__(self, pool):
        self._mcp_pool = pool
        self._orchestrator_registry = _InstanceToolRegistry()
        self._specialist_registry = _InstanceToolRegistry()

    _handle_list_mcp_servers = NativeFloodAgent._handle_list_mcp_servers
    _handle_disconnect_mcp_server = NativeFloodAgent._handle_disconnect_mcp_server


class TestMcpManagementHandlers:
    def test_list_empty_pool(self):
        h = _McpMgmtHarness(McpClientPool())
        assert "未接入" in h._handle_list_mcp_servers()

    def test_list_lists_connected_servers(self):
        pool = McpClientPool()
        pool._connections["a"] = FakeConn("a", [{"name": "t1"}])
        h = _McpMgmtHarness(pool)
        out = h._handle_list_mcp_servers()
        assert "a" in out and "1 个工具" in out and "已连接" in out

    def test_disconnect_missing_name_errors(self):
        h = _McpMgmtHarness(McpClientPool())
        assert "错误" in h._handle_disconnect_mcp_server(name="")

    def test_disconnect_unknown_server_errors(self):
        h = _McpMgmtHarness(McpClientPool())
        assert "未找到" in h._handle_disconnect_mcp_server(name="nope")

    def test_disconnect_removes_conn_and_tools_from_both_registries(self):
        pool = McpClientPool()
        conn = FakeConn("srv", [{"name": "t1"}, {"name": "t2"}])
        pool._connections["srv"] = conn
        h = _McpMgmtHarness(pool)
        # simulate the connect-time registration: build specs → register to both registries
        for spec in build_mcp_tool_specs(conn, "srv", pool.call_tool):
            h._orchestrator_registry.register(spec)
            h._specialist_registry.register(spec)
        assert len(h._orchestrator_registry.all()) == 2
        assert len(h._specialist_registry.all()) == 2

        out = h._handle_disconnect_mcp_server(name="srv")
        assert "已断开" in out
        assert conn.disconnected is True
        # tools cleaned from BOTH registries; pool no longer holds the server
        assert h._orchestrator_registry.all() == []
        assert h._specialist_registry.all() == []
        assert pool.get_server_info("srv") is None
