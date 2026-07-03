"""Tests for SandboxService, ProcessSandbox, and sandbox-aware permission/path rules."""

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior,
    PermissionDecision,
    PermissionRequest,
    PermissionRule,
    ToolPermissionPolicy,
)
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.process_sandbox import ProcessSandbox
from floodmind.agent.runtime.services.sandbox_service import SandboxService


class TestSandboxService:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.svc = SandboxService(base_dir=self.tmp)

    def test_create_workspace(self):
        ctx = self.svc.create("sub-parent-step-abc12345")
        assert ctx.workspace_dir.exists()
        assert ctx.outputs_dir.exists()
        assert ctx.uploads_dir.exists()
        assert ctx.workspace_dir == Path(self.tmp) / "sub-parent-step-abc12345" / "workspace"

    def test_destroy_cleans_workspace(self):
        ctx = self.svc.create("sub-parent-step-abc12345")
        marker = ctx.workspace_dir / "marker.txt"
        marker.write_text("x")
        self.svc.destroy(ctx)
        assert not ctx.workspace_dir.exists()

    def test_keep_workspace_option(self):
        svc = SandboxService(base_dir=self.tmp, keep_workspace=True)
        ctx = svc.create("sub-parent-step-abc12345")
        marker = ctx.workspace_dir / "marker.txt"
        marker.write_text("x")
        svc.destroy(ctx)
        assert ctx.workspace_dir.exists()

    def test_copy_artifacts_to_parent(self):
        parent = Path(self.tmp) / "parent_outputs"
        parent.mkdir()
        ctx = self.svc.create("sub-parent-step-abc12345", parent_output_dir=parent)
        artifact = ctx.outputs_dir / "report.md"
        artifact.write_text("# result")

        copied = self.svc.copy_artifacts_to_parent(ctx, [str(artifact)])

        assert len(copied) == 1
        assert Path(copied[0]).exists()
        assert Path(copied[0]).parent == parent

    def test_copy_artifacts_to_parent_blocks_traversal(self):
        """产物路径即使通过 .. 越级，也必须被 containment 检查拦截，不能复制到 parent_dir 外。"""
        parent = Path(self.tmp) / "parent_outputs"
        parent.mkdir()
        ctx = self.svc.create("sub-parent-step-abc12345", parent_output_dir=parent)

        # 在 workspace 的父目录创建一个文件，并用带 .. 的路径尝试逃逸
        outside = ctx.workspace_dir.parent / "outside_secret.txt"
        outside.write_text("secret")
        malicious_src = str(ctx.workspace_dir / ".." / "outside_secret.txt")

        copied = self.svc.copy_artifacts_to_parent(ctx, [malicious_src])

        # 由于目标解析后落在 parent_dir 外，应被跳过且不写入任何文件
        assert len(copied) == 1
        assert copied[0] == malicious_src
        assert not (parent / "outside_secret.txt").exists()
        assert not (parent / ".." / "outside_secret.txt").exists()
        rule = PermissionRule(
            name="subagent_no_nested",
            tool_name="SubAgent",
            session_id_pattern=r"^sub-",
            behavior=PermissionBehavior.DENY,
            reason="子代理内禁止再启动子代理",
        )
        assert rule.matches("SubAgent", {}, "sub-parent-step-abc12345")
        assert not rule.matches("SubAgent", {}, "parent-session")
        assert not rule.matches("Bash", {}, "sub-parent-step-abc12345")

    def test_permission_service_applies_session_id_rules(self):
        perm_svc = PermissionService()
        perm_svc.add_deny_rule(PermissionRule(
            name="subagent_no_network",
            tool_name="WebSearch",
            session_id_pattern=r"^sub-",
            behavior=PermissionBehavior.DENY,
            reason="子代理禁止网络",
        ))

        # 子代理调用应被 DENY
        sub_req = PermissionRequest(
            session_id="sub-parent-step-abc12345",
            tool_name="WebSearch",
            tool_input={"query": "x"},
            permission_policy=ToolPermissionPolicy(policy_type="network"),
        )
        decision = perm_svc.check(sub_req)
        assert decision.behavior == PermissionBehavior.DENY

        # 父代理调用不应命中
        parent_req = PermissionRequest(
            session_id="parent-session",
            tool_name="WebSearch",
            tool_input={"query": "x"},
            permission_policy=ToolPermissionPolicy(policy_type="network"),
        )
        decision = perm_svc.check(parent_req)
        assert decision.behavior == PermissionBehavior.ALLOW


class TestSandboxPathEnforcement:
    def test_sub_session_write_outside_workspace_denied(self):
        tmp = tempfile.mkdtemp()
        svc = PathService(project_root=Path(tmp))
        sub_id = "sub-parent-step-abc12345"
        # 预先创建 workspace
        workspace = Path(tmp) / "data" / "sessions" / sub_id / "workspace"
        workspace.mkdir(parents=True)

        # 写父 output_dir 绝对路径应被拒绝
        parent_output = Path(tmp) / "data" / "sessions" / "parent" / "outputs"
        parent_output.mkdir(parents=True)
        result = svc.resolve_simple(str(parent_output / "file.txt"), access="write", session_id=sub_id)
        assert not result.allowed
        assert "工作区" in result.reason

    def test_sub_session_write_inside_workspace_allowed(self):
        tmp = tempfile.mkdtemp()
        svc = PathService(project_root=Path(tmp))
        sub_id = "sub-parent-step-abc12345"
        workspace = Path(tmp) / "data" / "sessions" / sub_id / "workspace"
        workspace.mkdir(parents=True)

        target = workspace / "outputs" / "file.txt"
        result = svc.resolve_simple(str(target), access="write", session_id=sub_id)
        assert result.allowed

    def test_resolve_tool_path_enforces_sub_workspace_via_session_context(self):
        """端到端：通过 resolve_tool_path + SESSION_CONTEXT 验证 sub-session 写强制生效。

        使用 Workspace 注入替代旧 _PROJECT_ROOT mutation。
        """
        import os
        from floodmind.agent.runtime.contracts.workspace import Workspace
        from floodmind.agent.runtime.services.workspace_service import set_workspace, reset_workspace
        from floodmind.tools.session_context import set_session_context
        from floodmind.tools.agent_tool import resolve_tool_path

        tmp = tempfile.mkdtemp()
        tmp_p = Path(tmp)

        sub_id = "sub-parent-step-abc12345"
        workspace = tmp_p / "data" / "sessions" / sub_id / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        parent_output = tmp_p / "data" / "sessions" / "parent" / "outputs"
        parent_output.mkdir(parents=True, exist_ok=True)
        user_dir = tmp_p / "extra_user"
        user_dir.mkdir(parents=True, exist_ok=True)

        ws = Workspace(
            user_dir=user_dir,
            session_root=tmp_p / "data" / "sessions",
            sandbox_base=tmp_p / "data" / "sessions",
        )
        token = set_workspace(ws)

        # 全局 PathService 需使用 tmp 作为 project_root（is_write_allowed 检查）
        from floodmind.agent.runtime.services.path_service import PathService, set_path_service, get_path_service
        original_svc = get_path_service()
        try:
            path_svc = PathService(project_root=tmp_p)
            set_path_service(path_svc)

            # 设置 sub session 上下文
            set_session_context(sub_id, output_dir=str(workspace / "outputs"))

            # 子代理写 sandbox workspace → 放行
            (workspace / "outputs").mkdir(parents=True, exist_ok=True)
            result = resolve_tool_path(str(workspace / "outputs" / "ok.py"), access="write")
            assert result.allowed, f"子代理写自己 workspace 应放行，got reason={result.reason}"

            # 子代理写 user_dir → 放行（阶段C 放权）
            result = resolve_tool_path(str(user_dir / "sub_output.txt"), access="write")
            assert result.allowed, f"子代理写 user_dir 应放行，got reason={result.reason}"

            # 子代理写 tmp 根（不在 user_dir/sandbox/delegate_cwd 内）→ 拒绝
            evil = tmp_p / "evil.py"
            evil.write_text("bad")
            result = resolve_tool_path(str(evil), access="write")
            assert not result.allowed, "子代理写 tmp 根应被拒"
        finally:
            set_session_context("", output_dir="")
            set_path_service(original_svc)
            reset_workspace(token)


class TestProcessSandbox:
    def test_wrap_popen_kwargs_windows(self):
        sandbox = ProcessSandbox()
        kwargs = sandbox.wrap_popen_kwargs({})
        if os.name == "nt":
            assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            assert kwargs.get("preexec_fn") is os.setsid

    def test_terminate_all_kills_child(self):
        sandbox = ProcessSandbox()
        cmd = ["python", "-c", "import time; time.sleep(30)"]
        kwargs = sandbox.wrap_popen_kwargs({
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        })
        proc = subprocess.Popen(cmd, **kwargs)
        sandbox.register_process(proc)
        assert proc.poll() is None

        sandbox.terminate_all()
        time.sleep(0.5)
        assert proc.poll() is not None

    def test_restrict_env_limits_temp(self):
        sandbox = ProcessSandbox(workspace_dir=Path("/tmp/workspace"))
        env = sandbox.restrict_env({"PATH": "/usr/bin", "HOME": "/home/user"}, Path("/tmp/workspace"))
        assert env["TEMP"] == str(Path("/tmp/workspace"))
        assert env["TMP"] == str(Path("/tmp/workspace"))
        assert "HOME" not in env
