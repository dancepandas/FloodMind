"""Tests for Workspace abstraction and build_workspace factory."""

import tempfile
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.services.workspace_service import (
    build_folder_workspace,
    build_workspace,
    set_workspace,
    get_workspace,
    reset_workspace,
)


class TestBuildWorkspace:
    def test_web_fallback_user_dir_equals_session_outputs(self):
        """网页版：不传 session_root/user_dir → user_dir = data/sessions/<id>/outputs"""
        with tempfile.TemporaryDirectory() as tmp:
            sr = Path(tmp) / "data" / "sessions"
            ws = build_workspace("test-sess", session_root=sr)
            assert ws.user_dir == sr / "test-sess" / "outputs"
            assert ws.session_root == sr.resolve()
            assert ws.sandbox_base == ws.session_root  # strategy=session_root

    def test_explicit_user_dir(self):
        """桌面版：传 user_dir → user_dir = 用户指定"""
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "my-project"
            ws = build_workspace("sess1", session_root=Path(tmp) / "appdata" / "sessions", user_dir=user)
            assert ws.user_dir == user.resolve()
            assert ws.session_root == (Path(tmp) / "appdata" / "sessions").resolve()

    def test_sandbox_strategy_session_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_workspace("s1", sandbox_strategy="session_root",
                                session_root=Path(tmp) / "data" / "sessions")
            assert ws.sandbox_base == ws.session_root

    def test_sandbox_strategy_user_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "project"
            ws = build_workspace("s1", session_root=Path(tmp) / "data" / "sessions",
                                user_dir=user, sandbox_strategy="user_dir")
            assert ws.sandbox_base == user / ".floodmind" / "sandboxes"

    def test_overwrite_protection_default_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_workspace("s1", session_root=Path(tmp) / "data" / "sessions")
            assert ws.overwrite_protection is False

    def test_ensure_creates_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "project"
            session = Path(tmp) / "appdata" / "sessions"
            sandbox = user / ".floodmind" / "sandboxes"
            ws = Workspace(user_dir=user, session_root=session, sandbox_base=sandbox,
                           overwrite_protection=False)
            assert not user.exists()
            ws.ensure()
            assert user.exists()
            assert session.exists()
            assert sandbox.exists()

    def test_default_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "project"
            ws = Workspace(user_dir=user, session_root=Path(tmp), sandbox_base=Path(tmp))
            assert ws.default_cwd == user

    def test_folder_workspace_uses_primary_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "project"
            ws = build_folder_workspace("sess1", primary_dir=primary)
            assert ws.mode == "folder_first"
            assert ws.is_folder_first is True
            assert ws.user_dir == primary.resolve()
            assert ws.primary_dir == primary.resolve()
            assert ws.cwd == primary.resolve()
            assert ws.default_cwd == primary.resolve()
            assert ws.session_root == primary.resolve() / ".floodmind" / "sessions"
            assert ws.sandbox_base == primary.resolve() / ".floodmind" / "sandboxes"
            assert ws.artifact_dir == primary.resolve() / ".floodmind" / "artifacts" / "sess1"
            assert ws.tmp_dir == primary.resolve() / ".floodmind" / "tmp" / "sess1"
            assert ws.scripts_dir == primary.resolve() / ".floodmind" / "scripts" / "sess1"

    def test_folder_workspace_ensure_creates_managed_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_folder_workspace("sess1", primary_dir=Path(tmp) / "project")
            ws.ensure()
            assert ws.user_dir.exists()
            assert ws.session_root.exists()
            assert ws.sandbox_base.exists()
            assert ws.artifact_dir.exists()
            assert ws.tmp_dir.exists()
            assert ws.scripts_dir.exists()

    def test_workspace_from_folder_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.from_folder(Path(tmp) / "project", session_id="sdk")
            assert ws.mode == "folder_first"
            assert ws.default_cwd == (Path(tmp) / "project").resolve()
            assert ws.session_root == (Path(tmp) / "project" / ".floodmind" / "sessions").resolve()
            assert ws.artifact_dir == (Path(tmp) / "project" / ".floodmind" / "artifacts" / "sdk").resolve()

    def test_top_level_workspace_exports(self):
        from floodmind import Workspace as PublicWorkspace, build_folder_workspace as public_build_folder_workspace
        assert PublicWorkspace is Workspace
        ws = public_build_folder_workspace("sess1", primary_dir=Path(tempfile.gettempdir()) / "fm-sdk-ws")
        assert ws.mode == "folder_first"


class TestWorkspaceContextvar:
    def test_set_and_get(self):
        import contextvars
        with tempfile.TemporaryDirectory() as tmp:
            # 显式清理可能残留的上下文
            set_workspace(None)
            ws = Workspace(user_dir=Path(tmp)/"u", session_root=Path(tmp)/"s", sandbox_base=Path(tmp)/"b")
            token = set_workspace(ws)
            assert get_workspace() is ws
            reset_workspace(token)
            assert get_workspace() is None

    def test_set_none_clears(self):
        set_workspace(None)
        assert get_workspace() is None
