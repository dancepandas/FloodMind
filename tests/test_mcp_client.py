"""Tests for MCP pool lifecycle, build_mcp_tool_specs, and registry unregister_prefix.

Covers the MCP unification primitives (MCP-B): the single ToolSpec construction
point, the runtime hot-plug lifecycle (list / get / disconnect), and the scoped
registry unregister used to clean up a disconnected server's tools.
"""

import re
from unittest.mock import MagicMock

from floodmind.agent.mcp_client import (
    McpClientPool,
    _mcp_tool_spec_name,
    build_mcp_tool_specs,
    mcp_tool_prefix,
)
from floodmind.agent.native.native_flood_agent import NativeFloodAgent, _InstanceToolRegistry
from floodmind.agent.runtime.contracts.tools import ToolSpec


class FakeConn:
    """Stand-in for McpClientConnection — no network, records disconnect."""

    def __init__(self, name, tools, transport="sse", call_result="ok"):
        self.name = name
        self.transport = transport
        self._tools = tools
        self._connected = True
        self.disconnected = False
        self.call_result = call_result

    def list_tools(self):
        return list(self._tools)

    @property
    def is_connected(self):
        return self._connected

    def call_tool(self, tool_name, arguments):
        return self.call_result

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
        # model-visible name 经 sanitize（OpenAI 兼容端点要求 ^[a-zA-Z0-9_-]+$）
        assert s.name == "mcp_srv_t1"
        assert re.fullmatch(r"[a-zA-Z0-9_-]+", s.name)
        assert s.description == "[MCP:srv] d1"
        assert s.parameters["required"] == ["a"]
        assert s.permission_policy.policy_type == "network"
        assert s.is_destructive is True
        # closure 仍以原始冒号分隔 full name 调用 call_tool_fn
        assert s.func(a="x") == "ok"
        assert calls == [("mcp:srv:t1", {"a": "x"})]

    def test_each_closure_captures_own_tool_name(self):
        conn = FakeConn("srv", [{"name": "t1"}, {"name": "t2"}])
        specs = build_mcp_tool_specs(conn, "srv", lambda fn, kw: fn)
        assert sorted(s.func() for s in specs) == ["mcp:srv:t1", "mcp:srv:t2"]

    def test_sanitize_supports_colon_in_tool_name(self):
        # MCP 工具名本身可含冒号：`mcp:hydro-rag:search:docs`
        conn = FakeConn("hydro-rag", [{"name": "search:docs"}])
        specs = build_mcp_tool_specs(conn, "hydro-rag", lambda fn, kw: fn)
        assert specs[0].name == "mcp_hydro-rag_search_docs"
        assert re.fullmatch(r"[a-zA-Z0-9_-]+", specs[0].name)
        # bound function 仍调用原始 full name
        assert specs[0].func() == "mcp:hydro-rag:search:docs"

    def test_mcp_tool_prefix_matches_sanitized_names(self):
        # 断开清理前缀与 _mcp_tool_spec_name 生成的 model-visible 名对齐
        assert _mcp_tool_spec_name("hydro-rag", "search:docs").startswith(mcp_tool_prefix("hydro-rag"))
        assert mcp_tool_prefix("hydro-rag") == "mcp_hydro-rag_"
        assert mcp_tool_prefix("srv") == "mcp_srv_"

    def test_disconnect_cleans_sanitized_tools(self):
        # 断开时 unregister_prefix 用 sanitized 前缀，能清掉 sanitized ToolSpec
        conn = FakeConn("hydro-rag", [{"name": "search:docs"}, {"name": "t2"}])
        reg = _InstanceToolRegistry()
        for spec in build_mcp_tool_specs(conn, "hydro-rag", lambda fn, kw: "ok"):
            reg.register(spec)
        assert len(reg.all()) == 2
        removed = reg.unregister_prefix(mcp_tool_prefix("hydro-rag"))
        assert removed == 2
        assert len(reg.all()) == 0


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


class TestMcpConnectionLiveness:
    def _conn(self, transport="stdio"):
        from floodmind.agent.mcp_client import McpClientConnection
        return McpClientConnection(name="a", transport=transport)

    def test_uninitialized_returns_false(self):
        assert self._conn().is_connected is False

    def test_stdio_alive_process_returns_true(self):
        conn = self._conn()
        conn._initialized = True
        conn._process = MagicMock()
        conn._process.poll.return_value = None
        assert conn.is_connected is True

    def test_stdio_dead_process_returns_false(self):
        conn = self._conn()
        conn._initialized = True
        conn._process = MagicMock()
        conn._process.poll.return_value = 1
        assert conn.is_connected is False

    def test_sse_initialized_returns_true(self):
        conn = self._conn(transport="sse")
        conn._initialized = True
        assert conn.is_connected is True


class TestMcpCallHealth:
    def _pool_with(self, conns):
        pool = McpClientPool()
        for c in conns:
            pool._connections[c.name] = c
        return pool

    def test_missing_connection_records_failure(self):
        pool = McpClientPool()
        out = pool.call_tool("mcp:missing:t", {})
        assert "未连接" in out
        h = pool.call_health()["missing"]
        assert h["ok"] is False
        assert "未连接" in h["error"]

    def test_success_records_ok(self):
        conn = FakeConn("a", [{"name": "t1"}], call_result="ok result")
        pool = self._pool_with([conn])
        assert pool.call_tool("mcp:a:t1", {}) == "ok result"
        assert pool.call_health()["a"] == {"ok": True, "error": None}

    def test_fail_marker_records_failure(self):
        conn = FakeConn("a", [{"name": "t1"}], call_result="工具调用失败: backend down")
        pool = self._pool_with([conn])
        pool.call_tool("mcp:a:t1", {})
        h = pool.call_health()["a"]
        assert h["ok"] is False
        assert "backend down" in h["error"]

    def test_exception_records_failure_and_reraises(self):
        import pytest

        class BoomConn:
            name = "a"

            def call_tool(self, tool_name, arguments):
                raise ConnectionError("ECONNREFUSED")

        pool = self._pool_with([BoomConn()])
        with pytest.raises(ConnectionError):
            pool.call_tool("mcp:a:t1", {})
        h = pool.call_health()["a"]
        assert h["ok"] is False
        assert "ECONNREFUSED" in h["error"]


class TestMcpServerConnectedListener:
    def _make_pool_with_fake_connect(self, monkeypatch, fake_conn):
        import floodmind.agent.mcp_client as mcp_mod
        monkeypatch.setattr(mcp_mod, "McpClientConnection", lambda name, transport, **kw: fake_conn)
        return McpClientPool()

    def test_listener_notified_with_config_and_conn(self, monkeypatch):
        fake = MagicMock()
        fake.list_tools.return_value = []
        pool = self._make_pool_with_fake_connect(monkeypatch, fake)
        seen = []
        pool.add_server_connected_listener(lambda cfg, conn: seen.append((cfg, conn)))
        cfg = {"name": "srv-a", "transport": "sse", "url": "http://x"}
        conn = pool.connect_server(cfg)
        assert conn is fake
        assert len(seen) == 1
        assert seen[0][0] == cfg
        assert seen[0][1] is fake

    def test_listener_registration_is_idempotent(self):
        pool = McpClientPool()
        l = lambda cfg, conn: None
        pool.add_server_connected_listener(l)
        pool.add_server_connected_listener(l)
        assert len(pool._server_connected_listeners) == 1
        pool.remove_server_connected_listener(l)
        assert pool._server_connected_listeners == []

    def test_listener_exception_does_not_block_connect(self, monkeypatch):
        fake = MagicMock()
        fake.list_tools.return_value = []
        pool = self._make_pool_with_fake_connect(monkeypatch, fake)
        seen = []

        def boom(cfg, conn):
            raise RuntimeError("boom")

        pool.add_server_connected_listener(boom)
        pool.add_server_connected_listener(lambda cfg, conn: seen.append(cfg))
        pool.connect_server({"name": "srv", "transport": "sse"})
        assert seen == [{"name": "srv", "transport": "sse"}]


class TestBareModeMcpLoading:
    """Agent(bare=True) 也自动接入 mcp.json 配置的 MCP server（desktop 反馈修复）。"""

    def test_bare_init_auto_loads_configured_mcp_servers(self, monkeypatch):
        from floodmind.config.settings import settings
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        from floodmind.agent.native.model_client import ModelClient

        # 注入假配置：settings.mcp.servers 含一个 server
        monkeypatch.setattr(settings.mcp, "servers", [
            {"name": "srv", "transport": "sse", "url": "http://x"},
        ])

        fake_conn = FakeConn("srv", [{"name": "t1"}])
        fake_pool = MagicMock()
        fake_pool.connect_all.return_value = 1
        fake_pool.connections.return_value = {"srv": fake_conn}
        fake_pool.call_tool.return_value = "ok"
        # _load_mcp_tools 内从 floodmind.agent.mcp_client import get_mcp_client_pool
        monkeypatch.setattr("floodmind.agent.mcp_client.get_mcp_client_pool", lambda: fake_pool)

        agent = NativeFloodAgent(
            llm_service=ModelClient(api_key="k", base_url="http://mock/v1", model_name="m"),
            memory=None,
            session_id="s1",
            bare=True,
            tools=[],
        )
        names = {t.name for t in agent._orchestrator_registry.all()}
        assert "mcp_srv_t1" in names  # MCP 工具已注册（sanitized model-visible 名）
        assert agent._mcp_pool is fake_pool  # _mcp_pool 已初始化
        assert "GetTool" in names  # catalog 工具仍在

    def test_full_init_also_loads_mcp_via_shared_method(self, monkeypatch):
        """完整 runtime 走同一 _load_mcp_tools，两模式一致。"""
        from floodmind.config.settings import settings
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        from floodmind.agent.native.model_client import ModelClient

        called = {"n": 0}
        orig = NativeFloodAgent._load_mcp_tools

        def spy(self):
            called["n"] += 1
            return orig(self)

        monkeypatch.setattr(NativeFloodAgent, "_load_mcp_tools", spy)
        monkeypatch.setattr(settings.mcp, "servers", [])
        NativeFloodAgent(
            llm_service=ModelClient(api_key="k", base_url="http://mock/v1", model_name="m"),
            memory=None,
            session_id="s1",
            bare=False,
        )
        assert called["n"] == 1
