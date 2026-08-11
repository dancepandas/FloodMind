"""SandboxBackend implementations (target §11.4)."""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from floodmind.agent.runtime.contracts.sandbox import (
    CancellationToken,
    ExecutionResult,
    SandboxBackend,
    SandboxPolicy,
    SandboxViolation,
    ToolInvocation,
)
from floodmind.agent.runtime.services.process_sandbox import ProcessSandbox

logger = logging.getLogger(__name__)

_CRED_KEYS = ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")
_REQUIRED_ENV = ("PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "TEMP", "TMP", "TMPDIR")

_LL_EXECUTE = 1 << 0
_LL_WRITE_FILE = 1 << 1
_LL_READ_FILE = 1 << 2
_LL_READ_DIR = 1 << 3
_LL_REMOVE_DIR = 1 << 4
_LL_REMOVE_FILE = 1 << 5
_LL_MAKE_CHAR = 1 << 6
_LL_MAKE_DIR = 1 << 7
_LL_MAKE_REG = 1 << 8
_LL_MAKE_SYM = 1 << 9
_LL_REFER = 1 << 10
_LL_TRUNCATE = 1 << 11
_LL_BASE = (
    _LL_EXECUTE | _LL_WRITE_FILE | _LL_READ_FILE | _LL_READ_DIR | _LL_REMOVE_DIR
    | _LL_REMOVE_FILE | _LL_MAKE_CHAR | _LL_MAKE_DIR | _LL_MAKE_REG | _LL_MAKE_SYM
)
_LL_READ_EXEC = _LL_EXECUTE | _LL_READ_FILE | _LL_READ_DIR
_LL_RULE_PATH_BENEATH = 1
_LL_CREATE_RULESET_VERSION = 1
_PR_SET_NO_NEW_PRIVS = 38


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


def _landlock_functions():
    """Return Landlock callables, using libc wrappers or the Linux syscall ABI."""
    if platform.system() != "Linux":
        return None
    libc = ctypes.CDLL(None, use_errno=True)
    names = ("landlock_create_ruleset", "landlock_add_rule", "landlock_restrict_self")
    if all(hasattr(libc, name) for name in names):
        return tuple(getattr(libc, name) for name in names) + (libc,)
    machine = platform.machine().lower()
    if machine not in {"x86_64", "amd64", "aarch64", "arm64", "riscv64"}:
        return None

    def call(number):
        return lambda *args: libc.syscall(number, *args)

    return call(444), call(445), call(446), libc


def _landlock_abi() -> int:
    funcs = _landlock_functions()
    if funcs is None:
        return 0
    create_ruleset, _, _, _ = funcs
    ctypes.set_errno(0)
    result = int(create_ruleset(None, 0, _LL_CREATE_RULESET_VERSION))
    return result if result >= 1 else 0


def _landlock_access_for_abi(abi: int) -> int:
    access = _LL_BASE
    if abi >= 2:
        access |= _LL_REFER
    if abi >= 3:
        access |= _LL_TRUNCATE
    return access


def _landlock_available() -> bool:
    return _landlock_abi() >= 1


def _add_landlock_path(add_rule, ruleset_fd: int, path: Path, access: int) -> None:
    o_path = getattr(os, "O_PATH", os.O_RDONLY)
    parent_fd = os.open(str(path), o_path | getattr(os, "O_CLOEXEC", 0))
    try:
        attr = _PathBeneathAttr(allowed_access=access, parent_fd=parent_fd)
        if add_rule(ruleset_fd, _LL_RULE_PATH_BENEATH, ctypes.byref(attr), 0) < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
    finally:
        os.close(parent_fd)


def _apply_landlock(root: Path) -> None:
    """Restrict the child after fork and before exec; active enforcement fails closed."""
    funcs = _landlock_functions()
    if funcs is None:
        return
    create_ruleset, add_rule, restrict_self, libc = funcs
    abi = _landlock_abi()
    if abi < 1:
        return
    handled_access = _landlock_access_for_abi(abi)
    ruleset_fd = -1
    try:
        attr = _RulesetAttr(handled_access_fs=handled_access)
        ruleset_fd = create_ruleset(ctypes.byref(attr), ctypes.sizeof(attr), 0)
        if ruleset_fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        _add_landlock_path(add_rule, ruleset_fd, root, handled_access)
        # The restriction is installed pre-exec. Permit the host runtime to be read and
        # executed, but never written; mutable access remains exclusive to file_root.
        for runtime_root in (Path("/bin"), Path("/lib"), Path("/lib64"), Path("/usr")):
            if runtime_root.exists():
                _add_landlock_path(add_rule, ruleset_fd, runtime_root, _LL_READ_EXEC)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        if restrict_self(ruleset_fd, 0) < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
    finally:
        if ruleset_fd >= 0:
            os.close(ruleset_fd)


class _ByteBudget:
    def __init__(self, cap: int):
        self.cap = max(cap, 1)
        self.used = 0
        self.lock = threading.Lock()

    def consume(self, chunk: bytes) -> tuple[bytes, bool]:
        with self.lock:
            room = max(self.cap - self.used, 0)
            kept = chunk[:room]
            self.used += len(kept)
            return kept, len(kept) < len(chunk)


class _BoundedReader(threading.Thread):
    """Drain a pipe while both streams share one cumulative byte budget."""

    def __init__(self, stream, budget: _ByteBudget):
        super().__init__(daemon=True)
        self._stream = stream
        self._budget = budget
        self._buf = bytearray()
        self._truncated = False
        self._stop_event = threading.Event()

    def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                chunk = self._stream.read(65536)
                if not chunk:
                    break
                kept, truncated = self._budget.consume(chunk)
                self._buf.extend(kept)
                self._truncated = self._truncated or truncated
        except Exception:
            pass

    def stop(self) -> None:
        self._stop_event.set()

    def text(self) -> str:
        return bytes(self._buf).decode("utf-8", errors="replace")

    @property
    def truncated(self) -> bool:
        return self._truncated


class LocalRestrictedSandbox:
    """Local process sandbox.

    On Linux with Landlock, create/remove/rename/truncate/make operations are confined
    to ``file_root`` and an installation failure refuses the child (fail closed). On
    platforms without Landlock, ``filesystem_root`` is absent from capability
    reflection. Cross-platform filesystem isolation and network/CPU/memory enforcement
    require a container backend behind SandboxBackend.
    """

    def __init__(self) -> None:
        self._platform = "win" if os.name == "nt" else "posix"

    def _landlock_active(self) -> bool:
        return _landlock_available()

    @property
    def enforced_capabilities(self) -> set[str]:
        caps = {
            "process_tree", "resource_time", "resource_output", "env_restriction",
            "secret_inject", "cwd_containment", "temp_containment", "cancellation",
        }
        if self._landlock_active():
            caps.add("filesystem_root")
        return caps

    def prepare_launch(self, invocation: ToolInvocation, policy: SandboxPolicy) -> dict:
        """Return enforced launch parameters without spawning a process."""
        root = Path(policy.file_root).resolve()
        cwd = self._validate_cwd(root, invocation.cwd)
        (root / "tmp").mkdir(parents=True, exist_ok=True)
        env = self._build_env(invocation.env, policy, root)
        return {"env": env, "cwd": str(cwd)}

    def execute(
        self,
        invocation: ToolInvocation,
        policy: SandboxPolicy,
        cancellation: Optional[CancellationToken] = None,
    ) -> ExecutionResult:
        root = Path(policy.file_root).resolve()
        prepared = self.prepare_launch(invocation, policy)
        cwd = Path(prepared["cwd"])
        env = prepared["env"]
        process_sandbox = ProcessSandbox(
            max_processes=policy.resources.max_processes,
            workspace_dir=root / "tmp",
        )
        pkwargs = process_sandbox.wrap_popen_kwargs({})
        if self._platform == "posix" and "preexec_fn" in pkwargs:
            def restricted_child() -> None:
                os.setsid()
                _apply_landlock(root)
            pkwargs["preexec_fn"] = restricted_child

        proc = None
        out = err = None
        try:
            try:
                proc = subprocess.Popen(
                    invocation.command, cwd=str(cwd), env=env,
                    stdin=subprocess.PIPE if invocation.stdin_bytes is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, **pkwargs,
                )
            except FileNotFoundError as exc:
                return ExecutionResult(sandbox_violation=f"command not found: {exc}")
            except subprocess.SubprocessError as exc:
                return ExecutionResult(sandbox_violation=f"sandbox enforcement failed: {exc}")
            process_sandbox.register_process(proc)
            budget = _ByteBudget(policy.resources.max_output_bytes)
            out = _BoundedReader(proc.stdout, budget)
            err = _BoundedReader(proc.stderr, budget)
            out.start()
            err.start()
            if invocation.stdin_bytes is not None:
                try:
                    proc.stdin.write(invocation.stdin_bytes)
                    proc.stdin.close()
                except Exception:
                    pass

            deadline = time.monotonic() + (invocation.timeout_seconds or policy.resources.max_seconds)
            timed_out = cancelled = False
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
            out.join(5)
            err.join(5)
            return ExecutionResult(
                exit_code=proc.returncode, stdout=out.text(), stderr=err.text(),
                output_truncated=out.truncated or err.truncated,
                timed_out=timed_out, cancelled=cancelled, pid=proc.pid,
            )
        finally:
            if out is not None and err is not None:
                out.stop()
                err.stop()
                out.join(5)
                err.join(5)
            if proc is not None:
                for stream in (proc.stdout, proc.stderr):
                    try:
                        stream.close()
                    except Exception:
                        pass
            process_sandbox.close()

    def _validate_cwd(self, root: Path, cwd_str: str) -> Path:
        cwd = Path(cwd_str).resolve()
        if cwd != root and root not in cwd.parents:
            raise SandboxViolation(f"cwd {cwd} 逃逸 file_root {root}")
        return cwd

    def _build_env(self, base_env: Dict[str, str], policy: SandboxPolicy, root: Path) -> Dict[str, str]:
        env = dict(base_env)
        tmp_dir = str(root / "tmp")
        env.update({"TEMP": tmp_dir, "TMP": tmp_dir, "TMPDIR": tmp_dir})
        for key in _CRED_KEYS:
            env.pop(key, None)
        if policy.env_allowlist:
            keep = set(policy.env_allowlist)
            keep.update(key for key in _REQUIRED_ENV if key in base_env)
            env = {key: value for key, value in env.items() if key in keep}
        env.update(policy.secret_inject)
        return env


assert isinstance(LocalRestrictedSandbox(), SandboxBackend)
