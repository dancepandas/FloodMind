# MCP 集成子系统详解

> 父文档: [`OVERVIEW.md`](./OVERVIEW.md) | 更新: 2026-07-04 (MCP-A/B/C 完成后)

## 设计原则

> **MCP 遵循一个准则：系统运行状态下可以随时接入随时发现，不需要重启整个系统就可以接入；然后接入要明确，这是为后面让 FloodMind 自己维护或创建 MCP 服务打基础。**

## 核心组件

### McpClientPool (`floodmind/agent/mcp_client.py`)

全局单例，管理所有 MCP 连接。线程安全（`threading.Lock`）。

```
McpClientPool
├── _connections: Dict[str, McpClientConnection]
├── _lock: threading.Lock
│
├── connect_server(config) → McpClientConnection
│   ├── _connect_sse(url) / _connect_stdio(command, args, env)
│   ├── _initialize() → JSON-RPC handshake
│   └── _discover_tools() → tools/list
│
├── connect_all(servers: List[dict]) → Dict[str, McpClientConnection]
│
├── disconnect_server(name) → bool
│   └── conn.disconnect() → 关闭 HTTP / terminate 子进程
│
├── connections() → Dict[str, McpClientConnection]
├── list_servers() → List[{name, transport, tools, connected}]
├── get_server_info(name) → {name, transport, tools, ...}
│
└── call_tool(full_name, kwargs) → result
```

### build_mcp_tool_specs (`mcp_client.py:264-298`)

**MCP ToolSpec 的唯一构造入口**。连接与注册解耦。

```python
def build_mcp_tool_specs(conn, server_name, call_tool_fn) -> List[ToolSpec]:
    for mt in conn.list_tools():
        name = f"mcp:{server_name}:{mt['name']}"
        func = lambda **kw: call_tool_fn(name, kw)
        spec = ToolSpec(
            name=name,
            description=f"[MCP:{server_name}] {mt['description']}",
            parameters=input_schema(mt['inputSchema']),
            func=func,
            permission_policy=ToolPermissionPolicy("network"),
        )
```

## 调用流程

### 初始化（Agent 启动时）

```
NativeFloodAgent.__init__()
  → _init_tools()
    → get_mcp_client_pool()           # 全局单例
    → load_mcp_config()               # settings.json / mcp.json
    → pool.connect_all(servers)       # 逐个连接
      → connect_server(config)
        → _connect_sse/_connect_stdio
        → _initialize()              # JSON-RPC 握手
        → _discover_tools()          # tools/list
    → build_mcp_tool_specs(conn, name, pool.call_tool)
    → orchestrator_registry.register(spec)   # 双注册
    → specialist_registry.register(spec)
```

### 运行时接入（Agent 调用 LoadMcpServer）

```
Agent 调用 LoadMcpServer(name="rag-server", transport="sse", url="http://...")
  → _handle_load_mcp_server()
    → pool.connect_server(config)     # 仅连接
    → build_mcp_tool_specs(...)       # 构造 ToolSpec
    → orchestrator_registry.register  # 双注册
    → specialist_registry.register
```

### 运行时断开（Agent 调用 DisconnectMcpServer）

```
Agent 调用 DisconnectMcpServer(name="rag-server")
  → _handle_disconnect_mcp_server()
    → pool.get_server_info(name)     # 获取工具列表
    → pool.disconnect_server(name)   # 关闭连接
    → orchestrator_registry.unregister_prefix("mcp:rag-server:")
    → specialist_registry.unregister_prefix("mcp:rag-server:")
```

## Agent 管理工具

所有 MCP 管理工具**仅注册到 orchestrator**（管理是主代理职责）。

| 工具 | policy | 功能 |
|---|---|---|
| `LoadMcpServer` | state_write | 运行时动态接入 MCP server |
| `ListMcpServers` | readonly | 列举已接入的 MCP server（含工具数、连接状态） |
| `DisconnectMcpServer` | state_write | 断开 MCP server + 清理双 registry 工具 |

## 生命周期状态机

```
        connect_server()
  [disconnected] ──────────→ [connected]
                                  │
                    disconnect_server()
                                  │
  [disconnected] ←────────────────┘
                                  │
              unregister_prefix() （调用方负责）
```

## 关键文件

| 文件 | 职责 |
|---|---|
| `floodmind/agent/mcp_client.py` | McpClientPool 单例 + build_mcp_tool_specs + 生命周期 |
| `floodmind/agent/native/native_flood_agent.py` | MCP 管理工具 handler (L1125-1205) + 初始化集成 |
| `floodmind/agent/native/tool_runtime.py` | AgentTool → ToolSpec 桥接（MCP 绕过，直造 ToolSpec） |
| `tests/test_mcp_client.py` | MCP 客户端测试 (13 tests) |
