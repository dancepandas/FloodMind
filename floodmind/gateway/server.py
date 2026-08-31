"""FloodMind Gateway 入口：uvicorn 服务启动 + 控制台信息。"""

from __future__ import annotations

import logging
import os
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from floodmind.gateway.app import create_app
from floodmind.gateway.state import resolve_auth_token

logger = logging.getLogger(__name__)


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1", "")


def run_gateway(
    host: str = "127.0.0.1",
    port: int = 8317,
    workspace_root: Optional[str] = None,
    auth_token: Optional[str] = None,
    open_browser: bool = True,
    no_auth: bool = False,
    force_auth: bool = False,
) -> None:
    """启动 Gateway 服务（阻塞调用）。

    鉴权默认策略遵循本地工具惯例（Ollama / ComfyUI / Open WebUI 单用户模式）：
    - 回环地址（127.0.0.1 等）：默认免鉴权，启动即拉浏览器进入 Web 界面；
    - 非回环地址（如 0.0.0.0，局域网/远程访问）：默认强制 token（未提供则自动生成）；
    - 显式传 auth_token / 环境变量 FLOODMIND_GATEWAY_TOKEN / force_auth 时开启鉴权；
    - --no-auth 为强制覆盖（非回环地址下会打警告）。

    鉴权开启时沿用 Jupyter 惯例：浏览器自动打开的 URL 自动携带 ?token=...，
    前端收下后存入 localStorage 并从地址栏抹掉——用户全程无需手动处理 token。
    """
    import urllib.parse
    import uvicorn

    root = Path(workspace_root or Path.cwd()).resolve()
    explicit_token = (auth_token or os.getenv("FLOODMIND_GATEWAY_TOKEN") or "").strip()
    loopback = _is_loopback(host)

    if no_auth:
        token, auth_enabled = "", False
    elif force_auth or explicit_token or not loopback:
        token = explicit_token or resolve_auth_token(None)
        auth_enabled = True
    else:
        token, auth_enabled = "", False

    app = create_app(workspace_root=root, auth_token=token, auth_enabled=auth_enabled)

    base_url = f"http://{host}:{port}/"
    # 一键进入 URL：token 拼进链接（Jupyter 模式），控制台同步打印可点击的完整链接
    open_url = base_url
    if auth_enabled:
        open_url = f"{base_url}?token={urllib.parse.quote(token)}"

    print("=" * 64)
    print("FloodMind Gateway 已启动")
    print(f"  地址:       {open_url}")
    print(f"  工作区:     {root}")
    if auth_enabled:
        print("  鉴权:       已开启（token 已自动拼入上方链接，点击即用）")
        print("  (非回环地址默认开启鉴权；token 持久化于 ~/.floodmind/settings.json)")
    else:
        print("  鉴权:       关闭（本机回环模式，启动即用）")
        if not loopback:
            print("  警告: 监听非回环地址且鉴权已关闭，局域网内任何人都可操控本机 Agent！")
    print("=" * 64)

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(open_url)).start()

    uvicorn.run(app, host=host, port=port, log_level="info")
