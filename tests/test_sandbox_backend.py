"""SandboxBackend 强制边界测试（§11.4）。"""
import os
import platform
import sys
import time
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.sandbox import (
    ExecutionResult, SandboxPolicy, SandboxViolation, ToolInvocation,
)
from floodmind.agent.runtime.services.sandbox_backend import LocalRestrictedSandbox


_PROCESS_CAPABILITIES = {
    "process_tree", "resource_time", "resource_output", "env_restriction",
    "secret_inject", "cwd_containment", "temp_containment", "cancellation",
}


def _py(args: list) -> list:
    """用当前解释器执行一段 Python 代码，避免依赖系统 PATH 里的 python。"""
    return [sys.executable, "-c", args]


def _policy(root: Path, **kw) -> SandboxPolicy:
    return SandboxPolicy(file_root=str(root), **kw)


def test_enforced_capabilities_reflected():
    sb = LocalRestrictedSandbox()
    assert _PROCESS_CAPABILITIES <= sb.enforced_capabilities
    assert ("filesystem_root" in sb.enforced_capabilities) == sb._landlock_active()


def test_tmp_dir_created(tmp_path):
    sb = LocalRestrictedSandbox()
    res = sb.execute(
        ToolInvocation(command=_py("pass"), cwd=str(tmp_path)),
        _policy(tmp_path),
    )
    assert res.exit_code == 0
    assert (tmp_path / "tmp").is_dir()


def test_stdout_and_stderr_share_output_budget(tmp_path):
    sb = LocalRestrictedSandbox()
    res = sb.execute(
        ToolInvocation(
            command=_py("import sys; print('o' * 900); print('e' * 900, file=sys.stderr)"),
            cwd=str(tmp_path),
        ),
        _policy(tmp_path, resources={"max_output_bytes": 1000}),
    )
    assert res.output_truncated is True
    assert len(res.stdout.encode()) + len(res.stderr.encode()) <= 1000


@pytest.mark.skipif(
    platform.system() != "Linux" or not LocalRestrictedSandbox()._landlock_active(),
    reason="Landlock unavailable",
)
def test_landlock_blocks_fs_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    inside = root / "inside.txt"
    code = (
        "from pathlib import Path; "
        f"Path({str(inside)!r}).write_text('inside'); "
        f"Path({str(outside)!r}).write_text('outside')"
    )
    res = LocalRestrictedSandbox().execute(
        ToolInvocation(command=_py(code), cwd=str(root)),
        _policy(root),
    )
    assert res.exit_code != 0
    assert inside.read_text() == "inside"
    assert not outside.exists()


def test_executes_command_under_file_root(tmp_path):
    sb = LocalRestrictedSandbox()
    res = sb.execute(
        ToolInvocation(command=_py("print('hi')"), cwd=str(tmp_path)),
        _policy(tmp_path),
    )
    assert res.exit_code == 0
    assert res.stdout.strip() == "hi"


def test_cwd_outside_root_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    sb = LocalRestrictedSandbox()
    with pytest.raises(SandboxViolation):
        sb.execute(
            ToolInvocation(command=_py("pass"), cwd=str(outside)),
            _policy(root),
        )


def test_env_credentials_stripped_and_temp_rooted(tmp_path):
    env = dict(os.environ)
    env["HOME"] = "/fake/home"
    env["USERPROFILE"] = "C:\\fake\\profile"
    sb = LocalRestrictedSandbox()
    res = sb.execute(
        ToolInvocation(
            command=_py("import os; print('HOME' in os.environ); print(os.environ.get('TEMP',''))"),
            cwd=str(tmp_path),
            env=env,
        ),
        _policy(tmp_path),
    )
    lines = res.stdout.strip().splitlines()
    assert lines[0] == "False"  # HOME 被剥离
    assert Path(lines[1]).resolve().is_relative_to(tmp_path.resolve())  # TEMP 指向 file_root


def test_env_allowlist_filters(tmp_path):
    env = dict(os.environ)
    env["MY_VAR"] = "visible"
    env["OTHER_VAR"] = "hidden"
    sb = LocalRestrictedSandbox()
    res = sb.execute(
        ToolInvocation(
            command=_py("import os; print(os.environ.get('MY_VAR','')); print(os.environ.get('OTHER_VAR',''))"),
            cwd=str(tmp_path),
            env=env,
        ),
        _policy(tmp_path, env_allowlist=["MY_VAR"]),
    )
    assert res.stdout.splitlines()[0] == "visible"
    assert res.stdout.splitlines()[1] == ""  # 不在 allowlist 内被过滤


def test_secret_injected(tmp_path):
    sb = LocalRestrictedSandbox()
    res = sb.execute(
        ToolInvocation(command=_py("import os; print(os.environ.get('API_KEY',''))"), cwd=str(tmp_path)),
        _policy(tmp_path, secret_inject={"API_KEY": "sekrit"}),
    )
    assert res.stdout.strip() == "sekrit"


def test_timeout_kills_process(tmp_path):
    sb = LocalRestrictedSandbox()
    start = time.monotonic()
    res = sb.execute(
        ToolInvocation(
            command=_py("import time; time.sleep(30)"),
            cwd=str(tmp_path),
            timeout_seconds=1.0,
        ),
        _policy(tmp_path),
    )
    assert res.timed_out is True
    assert time.monotonic() - start < 15
    assert res.exit_code is not None  # 进程已被终止


def test_output_truncated(tmp_path):
    sb = LocalRestrictedSandbox()
    res = sb.execute(
        ToolInvocation(command=_py("print('x' * 10000)"), cwd=str(tmp_path)),
        _policy(tmp_path, resources={"max_output_bytes": 1000}),
    )
    assert res.output_truncated is True
    assert len(res.stdout) <= 4000


def test_cancellation_kills_process(tmp_path):
    sb = LocalRestrictedSandbox()
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] >= 3

    res = sb.execute(
        ToolInvocation(command=_py("import time; time.sleep(30)"), cwd=str(tmp_path)),
        _policy(tmp_path),
        cancellation=cancel,
    )
    assert res.cancelled is True
    assert res.exit_code is not None
