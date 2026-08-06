"""Tests for PathService with Workspace dynamic roots, sub-agent write range, and overwrite protection."""

import tempfile
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.workspace_service import build_folder_workspace, set_workspace, reset_workspace


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

    def test_write_allowed_in_project_data_for_legacy_workspace(self, tmp_workspace):
        root, ws = tmp_workspace
        assert not ws.is_folder_first
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


class TestSkillRegistryReadWhitelist:
    """SDK 收敛项 ②：folder-first 下 agent 可直接读已装 skill 注册表源文件。"""

    def test_folder_first_can_read_skill_registry(self, tmp_path):
        ws = build_folder_workspace("s1", primary_dir=tmp_path / "project")
        ws.ensure()
        svc = PathService(project_root=tmp_path, workspace=ws)

        from floodmind.skills.registry import get_skill_registry
        prefixes = svc._skill_read_prefixes()
        assert prefixes  # 非空
        skill_root = get_skill_registry().roots[0]
        ref = skill_root / "some-skill" / "references" / "guide.md"
        assert svc.is_read_allowed(ref)
        # 只放开读、不影响写
        assert not svc.is_write_allowed(ref)

    def test_site_packages_skills_prefix_derived(self):
        import floodmind.skills as skills_mod
        from pathlib import Path as _P
        svc = PathService(project_root=_P.cwd())
        expected = _P(skills_mod.__file__).resolve().parent.parent.parent / "skills"
        prefixes = [p.resolve() for p in svc._skill_read_prefixes()]
        assert expected in prefixes


class TestReadDenyGuidance:
    """SDK 收敛项 ③：读取拒绝原因包含可操作引导。"""

    def test_read_deny_reason_has_attachment_guidance(self, tmp_path):
        ws = build_folder_workspace("s1", primary_dir=tmp_path / "project")
        ws.ensure()
        svc = PathService(project_root=tmp_path, workspace=ws)
        outside = (tmp_path / "elsewhere" / "secret.txt").resolve()
        outside.parent.mkdir(parents=True)
        outside.write_text("x")
        allowed, reason = svc._check_path_allowed(outside, "read", "s1")
        assert not allowed
        assert "在工作区附件中引用该文件" in reason


class TestFolderFirstPathResolution:
    def test_relative_write_resolves_to_cwd(self, tmp_path):
        from floodmind.agent.runtime.services.path_service import PathResolveRequest
        from floodmind.tools.session_context import set_session_context

        ws = build_folder_workspace("s1", primary_dir=tmp_path / "project")
        ws.ensure()
        svc = PathService(project_root=tmp_path, workspace=ws)
        set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
        try:
            result = svc.resolve(PathResolveRequest(raw_path="report.md", access="write", session_id="s1"))
            assert result.allowed
            assert Path(result.resolved_path) == ws.default_cwd / "report.md"
        finally:
            set_session_context("", output_dir="")

    def test_relative_read_resolves_to_cwd(self, tmp_path):
        from floodmind.agent.runtime.services.path_service import PathResolveRequest
        from floodmind.tools.session_context import set_session_context

        ws = build_folder_workspace("s1", primary_dir=tmp_path / "project")
        ws.ensure()
        target = ws.default_cwd / "report.md"
        target.write_text("hello", encoding="utf-8")
        svc = PathService(project_root=tmp_path, workspace=ws)
        set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
        try:
            result = svc.resolve(PathResolveRequest(raw_path="report.md", access="read", session_id="s1"))
            assert result.allowed
            assert result.source == "workspace"
            assert Path(result.resolved_path) == target
        finally:
            set_session_context("", output_dir="")

    def test_folder_first_disables_project_root_static_write_allowlist(self, tmp_path):
        ws = build_folder_workspace("s1", primary_dir=tmp_path / "project")
        ws.ensure()
        svc = PathService(project_root=tmp_path, workspace=ws)
        project_data = tmp_path / "data" / "outputs" / "file.txt"
        project_data.parent.mkdir(parents=True)
        project_data.write_text("x")
        assert not svc.is_write_allowed(project_data)

    def test_no_workspace_relative_read_rejected(self, tmp_path):
        from floodmind.agent.runtime.services.path_service import PathResolveRequest

        target = tmp_path / "file.txt"
        target.write_text("x")
        svc = PathService(project_root=tmp_path, workspace=None)
        result = svc.resolve(PathResolveRequest(raw_path="file.txt", access="read", session_id=""))
        assert not result.allowed
        assert result.source == "no_workspace_rejected"

    def test_readable_root_does_not_allow_write(self, tmp_path):
        outside_root = tmp_path / "outside"
        ws = build_folder_workspace("s1", primary_dir=tmp_path / "project", readable_roots=(outside_root,))
        ws.ensure()
        outside_root.mkdir()
        outside = outside_root / "data.csv"
        outside.write_text("x")
        svc = PathService(project_root=tmp_path, workspace=ws)
        assert svc.is_read_allowed(outside)
        assert not svc.is_write_allowed(outside)

    def test_writable_root_allows_write_and_read(self, tmp_path):
        outside_root = tmp_path / "outside"
        ws = build_folder_workspace("s1", primary_dir=tmp_path / "project", writable_roots=(outside_root,))
        ws.ensure()
        outside_root.mkdir()
        outside = outside_root / "data.csv"
        outside.write_text("x")
        svc = PathService(project_root=tmp_path, workspace=ws)
        assert svc.is_write_allowed(outside)
        assert svc.is_read_allowed(outside)
    def test_external_absolute_path_denied_by_default(self, tmp_path):
        ws = build_folder_workspace("s1", primary_dir=tmp_path / "project")
        ws.ensure()
        outside = tmp_path / "outside" / "data.csv"
        outside.parent.mkdir()
        outside.write_text("x")
        svc = PathService(project_root=tmp_path, workspace=ws)
        assert not svc.is_read_allowed(outside)

    def test_external_read_root_allows_absolute_path(self, tmp_path):
        outside_root = tmp_path / "outside"
        ws = build_folder_workspace("s1", primary_dir=tmp_path / "project", readable_roots=(outside_root,))
        ws.ensure()
        outside_root.mkdir()
        outside = outside_root / "data.csv"
        outside.write_text("x")
        svc = PathService(project_root=tmp_path, workspace=ws)
        assert svc.is_read_allowed(outside)

    def test_project_agents_md_uses_workspace_root_in_folder_first(self, tmp_path):
        from floodmind.tools.agent_tool import get_agents_md_path

        ws = build_folder_workspace("s1", primary_dir=tmp_path / "project")
        ws.ensure()
        token = set_workspace(ws)
        try:
            assert get_agents_md_path("project") == ws.workspace_dir / "AGENTS.md"
        finally:
            reset_workspace(token)


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
