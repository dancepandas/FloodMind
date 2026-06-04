"""
MCP (Model Context Protocol) Client

将 FloodMind 作为 MCP 客户端，连接外部 MCP Server，
发现外部工具并注册到 Agent 的工具列表。

支持两种传输:
- sse:   HTTP SSE (远程服务)
- stdio: 本地子进程 stdin/stdout

协议: JSON-RPC 2.0
"""

import json
import logging
import subprocess
import threading
import time
import uuid
from queue import Queue, Empty
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_SSE_CONNECT_TIMEOUT = 15.0


class McpConnectionError(Exception):
    pass


class McpClientConnection:
    """单个 MCP Server 的连接管理"""

    def __init__(self, name: str, transport: str = "sse", **kwargs):
        self.name = name
        self.transport = transport
        self._config = kwargs
        self._request_id = 0
        self._initialized = False
        self._tools: List[dict] = []
        self._client: Optional[httpx.Client] = None
        self._process: Optional[subprocess.Popen] = None
        self._message_url: str = ""
        self._lock = threading.Lock()

    # ── 连接生命周期 ──────────────────────────────────────

    def connect(self) -> None:
        if self.transport == "sse":
            self._connect_sse()
        elif self.transport == "stdio":
            self._connect_stdio()
        else:
            raise McpConnectionError(f"不支持的传输类型: {self.transport}")

        self._initialize()
        self._discover_tools()
        logger.info(
            "MCP 连接成功: server=%s transport=%s tools=%d",
            self.name, self.transport, len(self._tools),
        )

    def disconnect(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            except Exception:
                try:
                    self._process.kill()
                    self._process.wait(timeout=3)
                except Exception:
                    pass
            self._process = None
        self._initialized = False
        self._tools.clear()

    def _connect_sse(self) -> None:
        url = self._config.get("url", "")
        if not url:
            raise McpConnectionError("SSE transport 需要 url 参数")

        self._client = httpx.Client(timeout=httpx.Timeout(60.0, connect=_SSE_CONNECT_TIMEOUT))
        headers = self._config.get("headers", {})
        resp = self._client.get(url, headers={**headers, "Accept": "text/event-stream"})
        resp.raise_for_status()

        # 解析 SSE endpoint 事件（MCP 规范：server 发送 event: endpoint 携带消息 URL）
        endpoint = ""
        current_event = ""
        for line in resp.iter_lines():
            line = line.strip()
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
                if current_event == "endpoint" and data:
                    if data.startswith("http://") or data.startswith("https://"):
                        endpoint = data
                    elif data.startswith("/"):
                        base = url.rstrip("/")
                        endpoint = base + data
                    if endpoint:
                        break
                current_event = ""

        if not endpoint:
            # fallback: 使用 url + /messages
            endpoint = url.rstrip("/") + "/messages"

        self._message_url = endpoint
        logger.debug("MCP SSE endpoint: %s", endpoint)

    def _connect_stdio(self) -> None:
        command = self._config.get("command", "")
        if not command:
            raise McpConnectionError("stdio transport 需要 command 参数")
        args = self._config.get("args", [])
        env = self._config.get("env", {})

        import os
        merged_env = {**os.environ, **env}

        self._process = subprocess.Popen(
            [command] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=merged_env,
            text=True,
        )
        # 守护线程持续排空 stderr，防止管道缓冲区满导致子进程死锁
        def _drain_stderr():
            try:
                while self._process and self._process.poll() is None:
                    self._process.stderr.readline()
            except Exception:
                pass
        threading.Thread(target=_drain_stderr, daemon=True, name=f"mcp-stderr-{self.name}").start()

    def _send_jsonrpc(self, method: str, params: Optional[dict] = None) -> dict:
        with self._lock:
            self._request_id += 1
            req_id = self._request_id

        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        })

        if self.transport == "sse":
            resp = self._client.post(
                self._message_url,
                content=msg,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

        elif self.transport == "stdio":
            self._process.stdin.write(msg + "\n")
            self._process.stdin.flush()
            line = self._process.stdout.readline()
            if not line:
                raise McpConnectionError(f"MCP {self.name}: stdio 无响应")
            return json.loads(line)

    def _initialize(self) -> None:
        result = self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "FloodMind", "version": "1.0.0"},
        })
        if "error" in result:
            raise McpConnectionError(f"MCP {self.name} initialize 失败: {result['error']}")

        svr = result.get("result", {}).get("serverInfo", {})
        logger.info("MCP %s: 已连接 → %s v%s", self.name, svr.get("name", "?"), svr.get("version", "?"))

        # 发送 initialized 通知
        self._send_notification("notifications/initialized", {})
        self._initialized = True

    def _send_notification(self, method: str, params: dict) -> None:
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        })
        if self.transport == "sse":
            try:
                self._client.post(self._message_url, content=msg, headers={"Content-Type": "application/json"})
            except Exception:
                pass
        elif self.transport == "stdio":
            try:
                self._process.stdin.write(msg + "\n")
                self._process.stdin.flush()
            except Exception:
                pass

    # ── 工具操作 ──────────────────────────────────────────

    def _discover_tools(self) -> None:
        result = self._send_jsonrpc("tools/list")
        if "error" in result:
            raise McpConnectionError(f"MCP {self.name} tools/list 失败: {result['error']}")
        self._tools = result.get("result", {}).get("tools", [])
        logger.info("MCP %s: 发现 %d 个工具", self.name, len(self._tools))
        for t in self._tools:
            logger.debug("  - mcp:%s:%s", self.name, t.get("name", "?"))

    def list_tools(self) -> List[dict]:
        return list(self._tools)

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        result = self._send_jsonrpc("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })

        if "error" in result:
            return f"MCP 工具 {tool_name} 调用失败: {result['error'].get('message', str(result['error']))}"

        content = result.get("result", {}).get("content", [])
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(texts) if texts else json.dumps(result.get("result", {}), ensure_ascii=False)

    @property
    def is_connected(self) -> bool:
        return self._initialized


# ── MCP 连接池 ─────────────────────────────────────────────

class McpClientPool:
    """管理多个 MCP Server 连接"""

    def __init__(self):
        self._connections: Dict[str, McpClientConnection] = {}
        self._lock = threading.Lock()

    def connect_all(self, servers: List[dict]) -> int:
        """连接所有配置的 MCP Server，返回成功数"""
        success = 0
        for cfg in servers:
            name = cfg.get("name", f"mcp-{len(self._connections)}")
            transport = cfg.get("transport", "sse")
            try:
                conn = McpClientConnection(name=name, transport=transport, **{k: v for k, v in cfg.items() if k not in ("name", "transport")})
                conn.connect()
                with self._lock:
                    self._connections[name] = conn
                success += 1
            except Exception as e:
                logger.warning("MCP %s 连接失败: %s", name, e)
        return success

    def get_all_tools(self) -> List[dict]:
        tools: List[dict] = []
        with self._lock:
            for conn in self._connections.values():
                tools.extend(conn.list_tools())
        return tools

    def call_tool(self, full_name: str, arguments: dict) -> str:
        """full_name 格式: mcp:server_name:tool_name"""
        parts = full_name.split(":", 2)
        if len(parts) < 3 or parts[0] != "mcp":
            return f"无效的 MCP 工具名: {full_name}，格式应为 mcp:server:tool"

        _, server_name, tool_name = parts
        with self._lock:
            conn = self._connections.get(server_name)
        if not conn:
            return f"MCP Server '{server_name}' 未连接"
        return conn.call_tool(tool_name, arguments)

    def connect_and_register(self, server_config: dict, registry) -> int:
        """运行时连接单个 MCP Server 并立即注册工具到 registry

        Args:
            server_config: {"name": "...", "transport": "sse|stdio", "url": "...", ...}
            registry: _InstanceToolRegistry 实例，调用 registry.register() 注册

        Returns: 注册的工具数量
        """
        from floodmind.agent.runtime.contracts.tools import ToolSpec
        from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy

        name = server_config.get("name", f"mcp-dynamic-{len(self._connections)}")
        transport = server_config.get("transport", "sse")
        cfg = {k: v for k, v in server_config.items() if k not in ("name", "transport")}

        conn = McpClientConnection(name=name, transport=transport, **cfg)
        conn.connect()

        with self._lock:
            self._connections[name] = conn

        count = 0
        for mt in conn.list_tools():
            mcp_tool_name = f"mcp:{name}:{mt.get('name', '')}"
            input_schema = mt.get("inputSchema", {})
            registry.register(ToolSpec(
                name=mcp_tool_name,
                description=f"[MCP:{name}] {mt.get('description', '')}",
                parameters={
                    "type": "object",
                    "properties": input_schema.get("properties", {}),
                    "required": input_schema.get("required", []),
                },
                func=lambda args, sn=name, tn=mt.get('name', ''): self.call_tool(f"mcp:{sn}:{tn}", args),
                is_readonly=False,
                is_destructive=True,
                is_concurrency_safe=True,
                permission_policy=ToolPermissionPolicy(policy_type="network"),
            ))
            count += 1

        logger.info("MCP 动态接入: server=%s, tools=%d", name, count)
        return count

    def disconnect_all(self) -> None:
        with self._lock:
            for conn in self._connections.values():
                try:
                    conn.disconnect()
                except Exception:
                    pass
            self._connections.clear()


# ── 全局单例 ──────────────────────────────────────────────

_mcp_pool: Optional[McpClientPool] = None


def get_mcp_client_pool() -> McpClientPool:
    global _mcp_pool
    if _mcp_pool is None:
        _mcp_pool = McpClientPool()
    return _mcp_pool
