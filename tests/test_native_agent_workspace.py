"""Tests for NativeFloodAgent workspace 实例属性。

回归：桌面端 sidecar 跨线程丢失 floodmind_workspace contextvar，导致 _get_output_dir
落到 C 盘 AppData。修复方案——宿主通过 bind_workspace / 构造函数显式注入 workspace
（实例属性，线程无关），_run_loop 在 SDK 子线程内据此重新 set_workspace()。

这里用 NativeFloodAgent.__new__ 轻量构造（绕过重型 __init__/LLM 依赖），只验证
workspace 解析逻辑：contextvar 为空（模拟 SDK 子线程）时，_get_output_dir /
_get_upload_dir 仍解析到实例 workspace。
"""

import threading
from pathlib import Path

import pytest

from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.services.workspace_service import (
    get_workspace,
    reset_workspace,
    set_workspace,
)


def _make_agent(workspace=None) -> NativeFloodAgent:
    """轻量构造 NativeFloodAgent（绕过重型 __init__），只设置被测方法依赖的属性。"""
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent.session_id = ""
    agent._workspace = workspace
    return agent


def _make_ws(tmp: Path, name: str = "proj") -> Workspace:
    return Workspace(
        user_dir=tmp / name / "outputs",
        session_root=tmp / name / "appdata" / "sessions",
        sandbox_base=tmp / name / "appdata" / "sandboxes",
    )


@pytest.fixture
def clean_workspace_ctx():
    """每个用例起点 contextvar 为 None（默认值），结束清理。"""
    set_workspace(None)
    yield
    set_workspace(None)


class TestInstanceWorkspaceNoContextvar:
    """复现桌面端 bug 条件：contextvar 为空，仅靠实例属性解析工作区。"""

    def test_output_dir_uses_instance_workspace(self, clean_workspace_ctx, tmp_path):
        ws = _make_ws(tmp_path)
        agent = _make_agent(workspace=ws)
        assert agent._get_output_dir() == str(ws.user_dir)

    def test_output_dir_falls_back_to_contextvar_when_no_instance(self, clean_workspace_ctx, tmp_path):
        """网页版路径：实例属性为 None 时，回退 contextvar。"""
        ws = _make_ws(tmp_path, "ctx")
        agent = _make_agent(workspace=None)
        token = set_workspace(ws)
        try:
            assert agent._get_output_dir() == str(ws.user_dir)
        finally:
            reset_workspace(token)

    def test_upload_dir_uses_instance_workspace(self, clean_workspace_ctx, tmp_path):
        ws = _make_ws(tmp_path)
        agent = _make_agent(workspace=ws)
        agent.session_id = "sess-1"
        assert agent._get_upload_dir("sess-1") == str(ws.session_root / "sess-1" / "uploads")

    def test_effective_workspace_instance_priority(self, clean_workspace_ctx, tmp_path):
        """实例属性优先于 contextvar（即便 contextvar 里有一个不同的 ws）。"""
        ws_inst = _make_ws(tmp_path, "inst")
        agent = _make_agent(workspace=ws_inst)
        token = set_workspace(_make_ws(tmp_path, "ctx"))
        try:
            assert agent._effective_workspace() is ws_inst
        finally:
            reset_workspace(token)

    def test_bind_workspace_switches(self, clean_workspace_ctx, tmp_path):
        agent = _make_agent(workspace=None)
        ws1 = _make_ws(tmp_path, "first")
        agent.bind_workspace(ws1)
        assert agent._effective_workspace() is ws1
        assert agent._get_output_dir() == str(ws1.user_dir)

        ws2 = _make_ws(tmp_path, "second")
        agent.bind_workspace(ws2)
        assert agent._effective_workspace() is ws2
        assert agent._get_output_dir() == str(ws2.user_dir)


class TestCrossThreadResolution:
    """跨线程：新线程 contextvar 为默认 None（复现 SDK 子线程），_get_output_dir 仍应
    返回实例 workspace——这正是修复点。"""

    def test_output_dir_resolves_in_clean_thread(self, clean_workspace_ctx, tmp_path):
        ws = _make_ws(tmp_path)
        agent = _make_agent(workspace=ws)

        result = {}

        def _worker():
            # 新线程不继承主线程 contextvar（与 SDK 子线程同构）
            result["ctx_ws"] = get_workspace()
            result["output_dir"] = agent._get_output_dir()

        t = threading.Thread(target=_worker)
        t.start()
        t.join()

        # 先确认子线程里 contextvar 确实丢失（bug 根因）
        assert result["ctx_ws"] is None
        # 再确认实例属性让 _get_output_dir 仍解析到工作区（修复效果）
        assert result["output_dir"] == str(ws.user_dir)

    def test_run_loop_rebinds_contextvar_for_downstream(self, clean_workspace_ctx, tmp_path):
        """_run_loop 开头会用实例属性重绑 contextvar，使 PathService 等下游在子线程内一致。

        这里直接验证该机制：在 contextvar 为空的子线程里，按 _run_loop 的重绑逻辑
        set_workspace(self._effective_workspace()) 后，get_workspace() 即返回实例 ws。
        """
        ws = _make_ws(tmp_path)
        agent = _make_agent(workspace=ws)

        result = {}

        def _worker():
            effective = agent._effective_workspace()
            if effective is not None:
                set_workspace(effective)
            result["rebound"] = get_workspace()

        t = threading.Thread(target=_worker)
        t.start()
        t.join()

        assert result["rebound"] is ws
