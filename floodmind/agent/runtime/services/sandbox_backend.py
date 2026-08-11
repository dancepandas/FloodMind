"""SandboxBackend 实现（target §11.4）。

LocalRestrictedSandbox 提供 OS 强制边界：
- 文件根：cwd 必须落在 file_root 内，越界抛 SandboxViolation；
- 进程树：每个 execute 独立的 ProcessSandbox（Windows Job Object / POSIX killpg）；
- 资源：超时（max_seconds / timeout_seconds）终止；stdout/stderr 有界读取；
- 环境：剥离 HOME/凭证，TEMP/TMP 指向 file_root/tmp，env_allowlist 过滤，secret_inject 注入；
- 取消：cancellation() 为 True 时终止并返回 cancelled=True。
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from floodmind.agent.runtime.contracts.sandbox import (
    CancellationToken,
    ExecutionResult,
    SandboxPolicy,
    SandboxViolation,
    ToolInvocation,
)
from floodmind.agent.runtime.services.process_sandbox import ProcessSandbox

logger = logging.getLogger(__name__)

# 被剥离的凭证类父环境变量
_CRED_KEYS = ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")
# 即使 allowlist 非空也必须保留的运行必需变量
_REQUIRED_ENV = ("PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "TEMP", "TMP", "TMPDIR")


class SandboxBackend:
    """SandboxBackend Protocol（§11.4）。"""

    def execute(
        self,
        invocation: ToolInvocation,
        policy: SandboxPolicy,
        cancellation: Optional[CancellationToken] = None,
    ) -> ExecutionResult:  # pragma: no cover - 契约
        ...


class _BoundedReader(threading.Thread):
    """从管道有界读取：超过 cap 继续排空（防止子进程阻塞）但不再存储。"""

    def __init__(self, stream, cap: int):
        super().__init__(daemon=True)
        self._stream = stream
        self._cap = max(cap, 1)
        self._buf = bytearray()
        self._truncated = False
        self._stop = threading.Event()

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                chunk = self._stream.read(65536)
                if not chunk:
                    break
                if len(self._buf) < self._cap:
                    room = self._cap - len(self._buf)
                    self._buf.extend(chunk[:room])
                    if len(chunk) > room:
                        self._truncated = True
                else:
                    self._truncated = True
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()

    def text(self) -> str:
        return bytes(self._buf).decode("utf-8", errors="replace")

    @property
    def truncated(self) -> bool:
        return self._truncated


class LocalRestrictedSandbox:
    """本地受限沙盒（§11.4 默认实现）。"""

    def __init__(self) -> None:
        self._platform = "win" if __import__("os").name == "nt" else "posix"

    def execute(
        self,
        invocation: ToolInvocation,
        policy: SandboxPolicy,
        cancellation: Optional[CancellationToken] = None,
    ) -> ExecutionResult:
        root = Path(policy.file_root).resolve()
        cwd = self._validate_cwd(root, invocation.cwd)
        env = self._build_env(invocation.env, policy, root)

        process_sandbox = ProcessSandbox(
            max_processes=policy.resources.max_processes,
            workspace_dir=root / "tmp",
        )
        pkwargs = process_sandbox.wrap_popen_kwargs({})
        try:
            proc = subprocess.Popen(
                invocation.command,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.PIPE if invocation.stdin_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **pkwargs,
            )
        except FileNotFoundError as e:
            return ExecutionResult(sandbox_violation=f"command not found: {e}")
        process_sandbox.register_process(proc)

        cap = max(policy.resources.max_output_bytes, 1)
        out = _BoundedReader(proc.stdout, cap)
        err = _BoundedReader(proc.stderr, cap)
        out.start()
        err.start()

        if invocation.stdin_bytes is not None:
            try:
                proc.stdin.write(invocation.stdin_bytes)
                proc.stdin.close()
            except Exception:
                pass

        deadline = time.monotonic() + (invocation.timeout_seconds or policy.resources.max_seconds)
        timed_out = False
        cancelled = False
        try:
            while proc.poll() is None:
                if cancellation and cancellation():
                    cancelled = True
                    process_sandbox.terminate_all()
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    process_sandbox.terminate_all()
                    break
                time.sleep(0.05)
            try:
                proc.wait(timeout=5)
            except Exception:
                process_sandbox.terminate_all()
                proc.wait(timeout=5)
        finally:
            out.stop()
            err.stop()
            out.join(5)
            err.join(5)
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass

        return ExecutionResult(
            exit_code=proc.returncode,
            stdout=out.text(),
            stderr=err.text(),
            output_truncated=out.truncated or err.truncated,
            timed_out=timed_out,
            cancelled=cancelled,
            pid=proc.pid,
        )

    # ── helpers ───────────────────────────────────────────────────

    def _validate_cwd(self, root: Path, cwd_str: str) -> Path:
        cwd = Path(cwd_str).resolve()
        if cwd != root and root not in cwd.parents:
            raise SandboxViolation(f"cwd {cwd} 逃逸 file_root {root}")
        return cwd

    def _build_env(self, base_env: Dict[str, str], policy: SandboxPolicy, root: Path) -> Dict[str, str]:
        env = dict(base_env)
        tmp_dir = str(root / "tmp")
        env["TEMP"] = tmp_dir
        env["TMP"] = tmp_dir
        env["TMPDIR"] = tmp_dir
        for key in _CRED_KEYS:
            env.pop(key, None)
        if policy.env_allowlist:
            keep = set(policy.env_allowlist)
            for key in _REQUIRED_ENV:
                if key in base_env:
                    keep.add(key)
            env = {k: v for k, v in env.items() if k in keep}
        env.update(policy.secret_inject)
        return env
