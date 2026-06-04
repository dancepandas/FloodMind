"""FloodMind TUI — Web Server 生命周期管理

功能：
- 端口可用性检测
- 后台启动 web server（如未运行）
- 健康检查轮询
- 自动清理（atexit / signal）
"""

import atexit
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOGGER = logging.getLogger(__name__)

# 全局进程句柄，用于后台启动的 web server
_SERVER_PROC: Optional[subprocess.Popen] = None
_OWNED_SERVER = False
_SERVER_LOG_FH = None


def set_default_port(port: int = 13014) -> None:
    """设置默认端口（由外部 CLI 参数覆盖）"""
    global DEFAULT_PORT
    DEFAULT_PORT = port


DEFAULT_PORT = 13014


def get_project_root() -> Path:
    return PROJECT_ROOT


def _log_dir() -> Path:
    p = get_project_root() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_port_available(host: str, port: int) -> bool:
    """检测 TCP 端口是否可用（未被占用）"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(0.5)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def is_web_server_running(host: str, port: int, timeout: float = 2.0) -> bool:
    """检测目标 host:port 上是否运行着 FloodMind Web Server（通过 /api/health 判断）"""
    try:
        url = f"http://{host}:{port}/api/health"
        resp = httpx.get(url, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("status") == "healthy"
    except Exception:
        pass
    return False


def start_web_server(host: str, port: int) -> subprocess.Popen:
    """后台启动 web_server.py，返回进程句柄。日志重定向到文件。"""
    global _SERVER_PROC, _OWNED_SERVER

    log_file = _log_dir() / f"web_server_{port}.log"
    global _SERVER_LOG_FH
    _SERVER_LOG_FH = open(log_file, "a", encoding="utf-8", buffering=1)
    _SERVER_LOG_FH.write(f"\n--- Web Server 启动于 {time.strftime('%Y-%m-%d %H:%M:%S')} (port={port}) ---\n")

    env = os.environ.copy()
    env.setdefault("DASHSCOPE_API_KEY", env.get("FLOODMIND_API_KEY", ""))

    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    web_server_script = get_project_root() / "web_server.py"
    if web_server_script.exists():
        cmd = [
            sys.executable, str(web_server_script),
            "--host", host, "--port", str(port),
        ]
    else:
        cmd = [
            sys.executable, "-m", "floodmind.cli", "serve",
            "--host", host, "--port", str(port), "--no-scheduler",
        ]
        env["_FLOODMIND_WEB_PIP"] = "1"

    proc = subprocess.Popen(
        cmd,
        stdout=_SERVER_LOG_FH,
        stderr=subprocess.STDOUT,
        cwd=str(get_project_root()),
        env=env,
        **kwargs,
    )

    _SERVER_PROC = proc
    _OWNED_SERVER = True
    return proc


def wait_for_web_server(host: str, port: int, timeout: float = 90.0, interval: float = 1.0) -> bool:
    """轮询 /api/health 直到 web server 就绪或超时"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_web_server_running(host, port, timeout=2.0):
            return True
        time.sleep(interval)
    return False


def stop_web_server(force: bool = False) -> None:
    """终止本进程启动的后台 web server（不影响用户自己起的服务）"""
    global _SERVER_PROC, _OWNED_SERVER, _SERVER_LOG_FH
    if _SERVER_PROC is None or not _OWNED_SERVER:
        return
    try:
        if _SERVER_PROC.poll() is None:
            try:
                _SERVER_PROC.terminate()
                _SERVER_PROC.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _SERVER_PROC.kill()
                _SERVER_PROC.wait(timeout=2)
    except Exception as e:
        LOGGER.warning(f"停止 web server 异常: {e}")
    finally:
        _SERVER_PROC = None
        _OWNED_SERVER = False
        if _SERVER_LOG_FH is not None:
            try:
                _SERVER_LOG_FH.close()
            except Exception:
                pass
            _SERVER_LOG_FH = None


def ensure_web_server(host: str, port: int) -> tuple[bool, bool]:
    """确保 web server 在 host:port 上运行。

    返回 (ok, started_here):
      - ok=True, started_here=False → 复用已有 server
      - ok=True, started_here=True  → 本进程启动的新 server
      - ok=False, started_here=False → 失败（端口被占，或启动超时）
    """
    # 1. 检查是否已有 web server 运行
    if is_web_server_running(host, port):
        LOGGER.info(f"复用已存在的 web server: {host}:{port}")
        return True, False

    # 2. 端口被占用但不是 floodmind 服务 → 不启动
    if not is_port_available(host, port):
        LOGGER.error(f"端口 {port} 已被占用且不是 FloodMind web server")
        return False, False

    # 3. 后台启动新的 web server
    try:
        start_web_server(host, port)
    except Exception as e:
        LOGGER.error(f"启动 web server 失败: {e}")
        return False, False

    # 4. 等待就绪
    LOGGER.info(f"web server 已在 {host}:{port} 后台启动，等待就绪...")
    if not wait_for_web_server(host, port, timeout=90.0):
        LOGGER.error("web server 启动超时（90s）")
        stop_web_server()
        return False, True

    return True, True


# 注册退出钩子，确保 TUI 退出时清理后台进程
def _at_exit():
    stop_web_server()


atexit.register(_at_exit)


def _install_signal_handlers():
    """Windows 上 Ctrl-C 不传播到子进程（子进程是 NEW_PROCESS_GROUP）；
    POSIX 上捕获 SIGTERM 优雅退出。"""
    if sys.platform != "win32":
        try:
            signal.signal(signal.SIGTERM, lambda *_: stop_web_server())
        except Exception:
            pass


_install_signal_handlers()
