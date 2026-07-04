---
name: mcp-builder
description: 创建符合 FloodMind MCP 热插拔架构的 MCP Server。当用户想把外部服务/API 封装成 MCP 工具、需要 FloodMind 能动态接入和断开某 MCP、或想知道 MCP Server 怎么写/怎么接入时使用。
---

# MCP Server 开发与接入（FloodMind 版）

引导创建和接入一个 **FloodMind 能运行时热插拔**的 MCP Server。先讲 FloodMind 的 MCP 架构（确保做对），再讲开发规范。

---

## 一、FloodMind MCP 集成架构

理解下面的架构才能写出能被 FloodMind 正确接入和管理的 MCP Server。

### 核心组件

```
McpClientPool (全局单例, floodmind/agent/mcp_client.py)
  ├── _connections: Dict[str, McpClientConnection]
  ├── _lock: threading.Lock
  │
  ├── connect_server(config) → McpClientConnection
  │   ├── _connect_sse(url) / _connect_stdio(command, args, env)
  │   ├── _initialize() → JSON-RPC handshake
  │   └── _discover_tools() → tools/list
  │
  ├── disconnect_server(name) → bool
  ├── list_servers() → [{name, transport, tools, connected}]
  │
  └── call_tool(full_name, kwargs) → result

build_mcp_tool_specs(conn, name, call_tool_fn) → List[ToolSpec]
  ↑ MCP ToolSpec 唯一构造入口（连接与注册解耦）
```

### 连接与注册解耦（核心设计原则）

**McpClientPool 只管连接，不管注册。** 连接和工具注册是两个独立步骤：

```
connect_server → 建立连接 + 发现工具
       │
       ▼
build_mcp_tool_specs → 把 MCP 工具转为 ToolSpec
       │
       ▼
调用方自行注册 → orchestrator_registry.register + specialist_registry.register
```

断开的对称操作：
```
disconnect_server → 关闭连接
       │
       ▼
调用方自行清理 → unregister_prefix("mcp:{name}:")
```

这个设计让 Agent 可以**随时接入、随时断开** MCP Server，不需要重启。

### 工具命名规范

MCP 工具在 FloodMind 中的全名为 `mcp:<server_name>:<tool_name>`。例如 RAG 服务 `rag-server` 提供 `search` 工具 → 全名 `mcp:rag-server:search`。

`unregister_prefix("mcp:rag-server:")` 可以一次性清理该 server 的所有工具。

### 双 registry 注册

MCP 工具同时注册到两个 registry：
- `orchestrator_registry`（主代理，完整权限）
- `specialist_registry`（子代理白名单，也有 MCP 工具）

### 权限策略

所有 MCP 工具统一 `policy_type="network"`。plan 模式下不可用。

### mcp.json 配置

`~/.floodmind/mcp.json`（独立于 settings.json）：

```json
{
  "servers": [
    {
      "name": "my-api",
      "transport": "sse",
      "url": "http://localhost:9000/sse",
      "enabled": true
    },
    {
      "name": "local-tool",
      "transport": "stdio",
      "command": "python",
      "args": ["./mcp/my_server.py"],
      "enabled": true
    }
  ]
}
```

Agent 启动时自动连接所有 `enabled: true` 的 server。

---

## 二、Agent MCP 管理工具

FloodMind 可在运行时**自己接入/断开 MCP Server**（仅 orchestrator 可用）：

| 工具 | 功能 | 参数 |
|---|---|---|
| `LoadMcpServer` | 运行时动态接入 MCP → 发现工具 → 双 registry 注册 | `name`, `transport`("sse"\|"stdio"), `url`(SSE 必填), `command`/`args`/`env`(stdio) |
| `ListMcpServers` | 列举所有已接入 server（含工具数、连接状态） | 无 |
| `DisconnectMcpServer` | 断开连接 + `unregister_prefix` 清理双 registry | `name` |

**典型流程**：

```
用户："帮我把这个 API 封装成 MCP"
  → Agent 开发 MCP Server（按 §三）
  → 本地测试通过
  → 运行时 LoadMcpServer(name="my-api", transport="sse", url="http://localhost:9000/sse")
  → ListMcpServers 验证工具数和连接状态
  → 后续对话中直接使用 mcp:my-api:xxx 工具
```

---

## 三、开发 MCP Server

### 推荐技术栈

| 语言 | 框架 | 适用场景 |
|---|---|---|
| **Python** | FastMCP (`mcp` 包) | 首选——与 FloodMind 同语言，本地 stdio 接入零依赖 |
| **TypeScript** | `@modelcontextprotocol/sdk` | 需要 JS 生态的 API 封装 |

### 传输层选择

| 传输 | 适用场景 | FloodMind 支持 |
|---|---|---|
| **stdio** | 本地进程，FloodMind 启动子进程通信 | ✅ `transport: "stdio"` |
| **SSE** | 远程服务，HTTP long-lived stream | ✅ `transport: "sse"` |

### 工具设计原则

1. **inputSchema 完整**：每个参数配 `type` + `description`，复杂类型用 `properties` 嵌套
2. **description 说清"做什么 + 返回什么"**：这是 LLM 决定是否调用的唯一线索
3. **annotations 准确**：`readOnlyHint`/`destructiveHint`/`idempotentHint` 帮助 FloodMind 做权限决策
4. **错误消息可执行**：不要只返回 "Error"，要告诉 agent 下一步该做什么
5. **返回结构化数据**：JSON 优于 Markdown 文本，方便下游工具解析

### Python 示例（FastMCP）

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-api")

@mcp.tool()
def query_data(query: str, limit: int = 10) -> dict:
    """搜索外部 API 并返回结构化结果。

    Args:
        query: 搜索关键词
        limit: 返回条数上限，默认 10
    """
    # 实现 API 调用
    return {"results": [...], "total": 42}

if __name__ == "__main__":
    mcp.run(transport="stdio")  # 或 mcp.run(transport="sse", port=9000)
```

---

## 四、测试与接入

### 本地测试

```bash
# MCP Inspector 交互式测试
npx @modelcontextprotocol/inspector python my_server.py

# 或直接跑验证
python my_server.py  # stdio 模式
```

### 接入 FloodMind

**方式 A：配置文件持久接入**（适合稳定的 server）

1. 写 `~/.floodmind/mcp.json`，添加 server 配置
2. 重启 FloodMind（或重新创建 agent）→ 自动连接

**方式 B：运行时热插拔**（适合临时/动态 server）

```
Agent 调 LoadMcpServer(name="my-api", transport="sse", url="http://localhost:9000/sse")
```

### 验证接入成功

```
ListMcpServers → 确认 my-api 在列表，工具数 > 0
GetSkill("mcp-builder") → 了解 MCP 开发规范
```

---

## 参考资源

- **MCP 协议规范**：`https://modelcontextprotocol.io/specification/draft.md`
- **MCP 最佳实践**：[reference/mcp_best_practices.md](./reference/mcp_best_practices.md)
- **Python 开发指南**：[reference/python_mcp_server.md](./reference/python_mcp_server.md)
- **TypeScript 开发指南**：[reference/node_mcp_server.md](./reference/node_mcp_server.md)
- **评估指南**：[reference/evaluation.md](./reference/evaluation.md)

---

## 检查清单

- [ ] MCP Server 可以独立启动（Python 或 Node，stdio 或 SSE）
- [ ] 每个 tool 有完整的 inputSchema（参数类型 + description）
- [ ] annotations 标记了 readOnlyHint / destructiveHint
- [ ] 错误返回 actionable（引导 agent 下一步做什么）
- [ ] 本地 MCP Inspector 测试通过
- [ ] `mcp.json` 配置正确（或准备 LoadMcpServer 参数）
- [ ] `ListMcpServers` 确认 server 已连接、工具数正确
- [ ] 试调一个工具验证端到端可用
