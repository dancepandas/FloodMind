"""P7 Task 6 — child sandbox backend binding + OS-boundary posture."""
import json
import sys
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.sandbox import (
    ResourceLimits,
    SandboxPolicy,
    ToolInvocation,
)
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.sandbox_backend import LocalRestrictedSandbox


def _policy(tmp_path):
    return SandboxPolicy(
        file_root=str(tmp_path),
        env_allowlist=["PATH"],
        secret_inject={"CHILD_SECRET": "s3cr3t"},
        resources=ResourceLimits(max_seconds=10, max_output_bytes=1024),
    )


def test_prepare_launch_sanitizes_env_and_validates_cwd(tmp_path):
    backend = LocalRestrictedSandbox()
    policy = _policy(tmp_path)
    prepared = backend.prepare_launch(
        ToolInvocation(
            command=[sys.executable, "-c", "pass"],
            cwd=str(tmp_path),
            env={"HOME": "x", "PATH": "/usr/bin", "TEMP": "/bad"},
        ),
        policy,
    )
    assert prepared["cwd"] == str(Path(tmp_path).resolve())
    assert prepared["env"]["TEMP"] == str(Path(tmp_path) / "tmp")
    assert "HOME" not in prepared["env"]
    assert prepared["env"]["CHILD_SECRET"] == "s3cr3t"
    assert prepared["env"]["PATH"] == "/usr/bin"


def test_prepare_launch_rejects_cwd_escape(tmp_path):
    backend = LocalRestrictedSandbox()
    policy = _policy(tmp_path)
    with pytest.raises(Exception):
        backend.prepare_launch(
            ToolInvocation(
                command=[sys.executable, "-c", "pass"],
                cwd=str(tmp_path.parent / "elsewhere"),
                env={},
            ),
            policy,
        )


def test_session_sandbox_applied_to_bg_task(tmp_path):
    svc = BackgroundTaskService(base_dir=str(tmp_path / "sessions"))
    policy = _policy(tmp_path)
    svc.set_session_sandbox("sub-1", policy, LocalRestrictedSandbox())
    try:
        task = svc.start(
            "sub-1", "true", [sys.executable, "-c", "pass"], cwd=str(tmp_path)
        )
    finally:
        svc.set_session_sandbox("sub-1", None, None)
    assert task.status in ("running", "completed")
    meta = Path(task.meta_path)
    if meta.exists():
        assert "sandbox_capabilities" in json.loads(meta.read_text(encoding="utf-8"))
    svc.kill_session("sub-1")
    assert svc.has_active("sub-1") is False
