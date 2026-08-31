"""基础工具（Bash/WebFetch）回归测试。

覆盖对抗性审查确认的缺陷：
- 同步 exec_bash 强制 utf-8 解码导致中文 Windows 下 GBK 输出乱码；
- stdout/stderr 无界累积（现封顶 64KB 并附截断提示）；
- timeout 非法值/超上限未钳制；
- 超时分支只 process.kill() 导致 Windows 孙进程泄漏；
- WebFetch 保留 requests 预置的 ISO-8859-1 导致中文页面乱码。
"""

import json
import os
import subprocess
import sys
import time

import pytest

from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.workspace_service import build_folder_workspace
from floodmind.tools.base_tools import (
    _clamp_exec_timeout,
    _impl_exec_bash,
    _kill_process_tree,
)
from floodmind.tools.session_context import set_runtime_context, set_session_context


def _bind_workspace(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
    set_runtime_context(RuntimeContext("s1", "s1", "run", "thread", "turn", path_service=PathService(project_root=tmp_path, workspace=ws)))
    return ws


def _reset(tmp_path):
    set_session_context("", output_dir="")
    set_runtime_context(RuntimeContext("", "", "", "", "", path_service=PathService(project_root=tmp_path)))


# ── timeout 钳制 ──────────────────────────────────────────────────────────


def test_clamp_exec_timeout_bounds_and_fallbacks():
    """timeout 钳制到 [1, 240]；非数字/负数回退默认 120。"""
    assert _clamp_exec_timeout(30) == 30
    assert _clamp_exec_timeout("45") == 45
    assert _clamp_exec_timeout(99999) == 240
    assert _clamp_exec_timeout(0.5) == 1
    assert _clamp_exec_timeout(-5) == 120
    assert _clamp_exec_timeout(0) == 120
    assert _clamp_exec_timeout("abc") == 120
    assert _clamp_exec_timeout(None) == 120


def test_impl_exec_bash_timeout_path_returns_within_bound(tmp_path):
    """同步超时路径：超时应返回超时信息而非挂起（内部走杀进程树逻辑）。"""
    ws = _bind_workspace(tmp_path)
    try:
        start = time.monotonic()
        result = _impl_exec_bash(command="Start-Sleep 30", timeout=2)
        elapsed = time.monotonic() - start
    finally:
        _reset(tmp_path)
    assert elapsed < 20, f"超时未及时返回（{elapsed:.1f}s）"
    assert "超时" in result


# ── 输出编码 ──────────────────────────────────────────────────────────────


def test_impl_exec_bash_decodes_gbk_output(tmp_path):
    """子进程输出 GBK 字节时应回退 GBK 解码，而非 utf-8 replace 乱码（回归测试）。"""
    ws = _bind_workspace(tmp_path)
    script = ws.workspace_dir / "gbk_out.py"
    script.write_text(
        "import sys\nsys.stdout.buffer.write('中文GBK输出'.encode('gbk'))\n",
        encoding="utf-8",
    )
    try:
        result = _impl_exec_bash(command=f"python {script}", timeout=30)
    finally:
        _reset(tmp_path)

    assert "中文GBK输出" in result


def test_impl_exec_bash_decodes_utf8_output(tmp_path):
    """UTF-8 输出走严格解码主路径（不因 GBK 回退被二次转码）。"""
    ws = _bind_workspace(tmp_path)
    script = ws.workspace_dir / "utf8_out.py"
    script.write_text(
        "import sys\nsys.stdout.buffer.write('中文UTF8输出'.encode('utf-8'))\n",
        encoding="utf-8",
    )
    try:
        result = _impl_exec_bash(command=f"python {script}", timeout=30)
    finally:
        _reset(tmp_path)

    assert "中文UTF8输出" in result


# ── 输出截断 ──────────────────────────────────────────────────────────────


def test_impl_exec_bash_truncates_oversized_output(tmp_path):
    """stdout 超过 64KB 封顶：结果被截断并附原始输出总字节数提示。"""
    ws = _bind_workspace(tmp_path)
    script = ws.workspace_dir / "flood_out.py"
    script.write_text("print('x' * 200_000)\n", encoding="utf-8")
    try:
        result = _impl_exec_bash(command=f"python {script}", timeout=30)
    finally:
        _reset(tmp_path)

    assert "[输出已截断" in result
    assert "原始输出共" in result
    # 结果本体应有界（封顶 64KB + 提示语），不再无界累积
    assert len(result) < 200_000


# ── 进程树终止 ────────────────────────────────────────────────────────────


@pytest.mark.skipif(os.name != "nt", reason="taskkill 进程树语义为 Windows 专属")
def test_kill_process_tree_terminates_grandchild(tmp_path):
    """P0 回归：终止必须覆盖整棵进程树（旧实现 process.kill() 在 Windows 泄漏孙进程）。"""
    pid_file = tmp_path / "grandchild_pid.txt"
    grandchild_code = (
        f"import os, time; open(r'{pid_file}', 'w').write(str(os.getpid())); time.sleep(60)"
    )
    parent_code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}])\n"
        "time.sleep(60)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pid_file.exists(), "孙进程未按预期启动"
        grandchild_pid = int(pid_file.read_text().strip())

        _kill_process_tree(parent)
        assert parent.poll() is not None

        # 孙进程也必须被终止（tasklist 查不到该 PID）
        time.sleep(1.0)
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {grandchild_pid}"],
            capture_output=True, text=True,
        ).stdout
        assert "python" not in listing.lower(), f"孙进程 {grandchild_pid} 泄漏"
    finally:
        if parent.poll() is None:
            parent.kill()


# ── WebFetch 编码 ─────────────────────────────────────────────────────────


def test_webfetch_prefers_apparent_encoding_over_iso_8859_1(monkeypatch):
    """requests 对无 charset 的 text/* 预置 ISO-8859-1，需回退 apparent_encoding（回归测试）。"""
    import requests

    from floodmind.tools.base_tools import _impl_fetch_webpage

    resp = requests.Response()
    resp.status_code = 200
    resp._content = "<html><body><main><p>中文内容测试</p></main></body></html>".encode("utf-8")
    resp.headers["Content-Type"] = "text/html"
    # 模拟 requests 对 text/* 无 charset 的预置行为
    resp.encoding = "ISO-8859-1"
    # apparent_encoding 探测固定返回 utf-8，保证断言确定性
    monkeypatch.setattr(type(resp), "apparent_encoding", property(lambda self: "utf-8"))
    monkeypatch.setattr(requests, "get", lambda url, headers=None, timeout=30: resp)

    result = _impl_fetch_webpage(url="https://example.com")
    payload = json.loads(result)
    assert "中文内容测试" in payload["content"]
