"""Tests for PathService with Workspace dynamic roots, sub-agent write range, and overwrite protection."""

import tempfile
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.workspace_service import set_workspace, reset_workspace


@pytest.fixture
def tmp_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        user = root / "user_dir"
        session = root / "data" / "sessions"
        sandbox = root / "data" / "sessions"  # web-style
        user.mkdir(parents=True, exist_ok=True)
        session.mkdir(parents=True, exist_ok=True)
        ws = Workspace(user_dir=user, session_root=session, sandbox_base=sandbox)
        token = set_workspace(ws)
        yield root, ws
        reset_workspace(token)


class TestPathServiceDynamicRoots:
    def test_write_allowed_in_user_dir(self, tmp_workspace):
        root, ws = tmp_workspace
        svc = PathService(project_root=root, workspace=ws)
        f = ws.user_dir / "output.txt"
        f.write_text("hi")
        assert svc.is_write_allowed(f)

    def test_write_allowed_in_project_data(self, tmp_workspace):
        root, ws = tmp_workspace
        svc = PathService(project_root=root, workspace=ws)
        d = root / "data" / "outputs"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "file.txt"
        f.write_text("x")
        assert svc.is_write_allowed(f)

    def test_read_allowed_outside_project_via_workspace(self, tmp_workspace):
        """通过 workspace 动态根，user_dir 可读"""
        root, ws = tmp_workspace
        svc = PathService(project_root=root, workspace=ws)
        f = ws.user_dir / "readable.txt"
        f.write_text("x")
        assert svc.is_read_allowed(f)

    def test_overwrite_protection_denies_existing(self, tmp_workspace):
        root, ws = tmp_workspace
        ws_protected = Workspace(
            user_dir=ws.user_dir, session_root=ws.session_root,
            sandbox_base=ws.sandbox_base, overwrite_protection=True,
        )
        svc = PathService(project_root=root, workspace=ws_protected)
        f = ws.user_dir / "existing.txt"
        f.write_text("dont touch")
        allowed, reason = svc._check_path_allowed(f, "write", "main-sess")
        assert not allowed
        assert "覆盖保护" in reason

    def test_no_overwrite_protection_by_default(self, tmp_workspace):
        root, ws = tmp_workspace
        svc = PathService(project_root=root, workspace=ws)
        f = ws.user_dir / "existing.txt"
        f.write_text("ok")
        allowed, _ = svc._check_path_allowed(f, "write", "main-sess")
        assert allowed


class TestSubAgentWriteRange:
    def test_sub_can_write_sandbox(self, tmp_workspace):
        root, ws = tmp_workspace
        svc = PathService(project_root=root, workspace=ws)
        sub_id = "sub-parent-step-abc12345"
        sandbox_workspace = ws.sandbox_base / sub_id / "workspace"
        sandbox_workspace.mkdir(parents=True)
        f = sandbox_workspace / "result.txt"
        f.write_text("x")
        allowed, _ = svc._check_path_allowed(f, "write", sub_id)
        assert allowed

    def test_sub_can_write_user_dir(self, tmp_workspace):
        root, ws = tmp_workspace
        svc = PathService(project_root=root, workspace=ws)
        sub_id = "sub-parent-step-abc12345"
        f = ws.user_dir / "sub_output.txt"
        f.write_text("x")
        allowed, _ = svc._check_path_allowed(f, "write", sub_id)
        assert allowed

    def test_sub_denied_outside_range(self, tmp_workspace):
        root, ws = tmp_workspace
        svc = PathService(project_root=root, workspace=ws)
        sub_id = "sub-parent-step-abc12345"
        outside = root / "outside"
        outside.mkdir(parents=True)
        f = outside / "bad.txt"
        f.write_text("x")
        allowed, reason = svc._check_path_allowed(f, "write", sub_id)
        assert not allowed
        # 可能被 is_write_allowed 或子代理范围检查拒绝
        assert ("不在允许目录" in reason) or ("子代理" in reason)


class TestSubAgentRelativePathIsolation:
    """回归：子代理相对路径写入必须落到自己的 sandbox outputs，不能落到主代理 user_dir。

    根因（已修）：_get_user_dir 曾优先返回 workspace.user_dir，而子代理继承主代理的
    workspace contextvar，导致相对写入错误解析到主代理 user_dir，破坏 sandbox 隔离。
    """

    def test_sub_relative_write_resolves_to_sandbox_not_user_dir(self, tmp_workspace):
        from floodmind.tools.session_context import set_session_context
        from floodmind.agent.runtime.services.path_service import PathResolveRequest

        root, ws = tmp_workspace
        svc = PathService(project_root=root, workspace=ws)

        sub_id = "sub-parent-step-abc12345"
        sandbox_outputs = ws.sandbox_base / sub_id / "workspace" / "outputs"
        sandbox_outputs.mkdir(parents=True, exist_ok=True)

        # 子代理经 ToolExecutionService 注入 SESSION_CONTEXT["output_dir"] = sandbox outputs
        set_session_context(sub_id, output_dir=str(sandbox_outputs))
        try:
            result = svc.resolve(PathResolveRequest(
                raw_path="result.py", access="write", session_id=sub_id,
            ))
            # 关键断言：相对路径 result.py 必须解析到子代理 sandbox，而非主代理 user_dir
            assert str(sandbox_outputs) in str(result.resolved_path), (
                f"子代理相对写入应落到 sandbox outputs，实际: {result.resolved_path}"
            )
            assert str(ws.user_dir) not in str(result.resolved_path) or str(ws.user_dir) == str(sandbox_outputs), (
                f"子代理相对写入不应落到主代理 user_dir: {result.resolved_path}"
            )
            assert result.allowed
        finally:
            set_session_context("", output_dir="")
