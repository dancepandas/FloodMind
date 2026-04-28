"""
FloodMind 统一启动入口

同时启动 web_server 和 scheduler，一次命令搞定。
用法:
    python start.py                  # 默认 0.0.0.0:13014
    python start.py --port 8080      # 指定端口
    python start.py --no-scheduler   # 不启动定时任务调度器
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _stream_output(proc: subprocess.Popen, prefix: str, stop_event: threading.Event) -> None:
    try:
        for raw_line in proc.stdout:
            if stop_event.is_set():
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            if line:
                print(f"[{prefix}] {line}", flush=True)
    except Exception:
        pass


def start_web_server(host: str, port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "web_server.py"), "--host", host, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
    )
    return proc


def start_scheduler() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "scheduler.py")],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
    )
    return proc


def main() -> int:
    parser = argparse.ArgumentParser(description="FloodMind 统一启动入口")
    parser.add_argument("--host", default="0.0.0.0", help="Web 服务器监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=13014, help="Web 服务器端口 (默认 13014)")
    parser.add_argument("--no-scheduler", action="store_true", help="不启动定时任务调度器")
    args = parser.parse_args()

    stop_event = threading.Event()
    procs: list[subprocess.Popen] = []
    threads: list[threading.Thread] = []

    def _shutdown(signum=None, frame=None):
        print("\n正在停止所有服务...", flush=True)
        stop_event.set()
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    web_proc = start_web_server(args.host, args.port)
    procs.append(web_proc)
    t = threading.Thread(target=_stream_output, args=(web_proc, "web"), daemon=True)
    t.start()
    threads.append(t)
    print(f"[start] Web Server 已启动 -> http://{args.host}:{args.port}", flush=True)

    if not args.no_scheduler:
        sched_proc = start_scheduler()
        procs.append(sched_proc)
        t = threading.Thread(target=_stream_output, args=(sched_proc, "scheduler"), daemon=True)
        t.start()
        threads.append(t)
        print("[start] Scheduler 已启动", flush=True)

    print("[start] 所有服务已启动，按 Ctrl+C 停止\n", flush=True)

    try:
        while not stop_event.is_set():
            for proc in list(procs):
                ret = proc.poll()
                if ret is not None:
                    prefix = "web" if proc is web_proc else "scheduler"
                    print(f"[start] {prefix} 进程已退出 (code={ret})", flush=True)
                    procs.remove(proc)
                    if not procs:
                        stop_event.set()
            stop_event.wait(1.0)
    except KeyboardInterrupt:
        _shutdown()

    for t in threads:
        t.join(timeout=5)

    for proc in procs:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("[start] 所有服务已停止", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
