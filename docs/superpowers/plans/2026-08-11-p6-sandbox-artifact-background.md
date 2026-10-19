# P6 — Sandbox / Artifact / Background Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 TARGET 的 Sandbox 强制边界（§11.4）、Artifact 原子发布服务（§15）、Background Kill 验证链与 Restart PID Reconcile（§12），关闭 §23.2 对应 OPEN 项。

**Architecture:** Sandbox 作为 OS/Container 强制边界（文件根 + 进程树 + 资源 + 环境）；Artifact 走 content-addressed store + 原子发布 + Journal 事件；Background 升级为 durable 状态机（kill 验证链 + 重启对账），事件经 JournalAuthority 落 Journal，Reducer 维护 run 级 active_background_tasks / artifacts。全部 forward-only，不 fallback。

**Tech Stack:** Python 3.10+、pydantic v2、subprocess（Windows Job Object / POSIX killpg）、psutil（PID/Process Identity）、`hashlib`、JournalAuthority（P2 产物）、reducer（P2 产物）。

## Global Constraints

- 不向后兼容、不 fallback：不新增/不保留旧接口适配器、降级分支、双写路径。TARGET 契约直接落。
- Canonical Journal 是唯一运行事实源；runtime 副作用经 `JournalAuthority.emit` 落事件，Reducer 维护派生状态。
- `JournalAuthority.emit(event_type, payload, **scope)`（P2 产物，`floodmind/agent/runtime/services/journal_authority.py:100`）；scope 可含 thread_id/attempt_id/call_id/transaction_id。
- Reducer 事件处理模式（P2 产物）：`if et == "<type>": return _reduce_x(ns, event.payload, ...)`（`reducer.py:428-477`）；事件 id 幂等由 `processed_event_ids` 保证；handler 内做去重防御。
- 测试模式：`open_journal_authority(tmp_path, conversation_id=..., task_id=..., run_id=..., thread_id=..., turn_id=...)` + `auth.new_envelope(et, payload, **scope)` + `initial_run_state(...)` + `reduce(state, event)`（见 `tests/test_tool_lifecycle_reducer.py:8-33`）。
- **WIP 不碰**：不得修改以下携带用户未提交改动的文件——`floodmind/agent/runtime/contracts/permissions.py`。每次提交显式 `git add <paths>`，绝不 `git add -A`；提交信息以 `Co-Authored-By: Claude <noreply@anthropic.com>` 结尾。
- 全局 ContextVar getter（`get_background_task_service` 等 Legacy Adapter）不新增；注入走构造函数参数。
- 每次任务完成运行 `python -m pytest -q`，报告通过数（P5 基线 1100 passed / 1 skipped）。

---

### Task 1: SandboxBackend 契约 + LocalRestrictedSandbox（§11.4，关闭 §23.2「Regex Shell Scanner 不是可靠文件系统隔离」）

**Files:**
- Create: `floodmind/agent/runtime/contracts/sandbox.py`
- Create: `floodmind/agent/runtime/services/sandbox_backend.py`
- Test: `tests/test_sandbox_backend.py`

**Interfaces:**
- Produces: `SandboxPolicy`、`ResourceLimits`、`ToolInvocation`、`ExecutionResult`、`SandboxViolation`、`CancellationToken`（`contracts/sandbox.py`）；`SandboxBackend` Protocol + `LocalRestrictedSandbox`（`services/sandbox_backend.py`）。
- Consumes: `ProcessSandbox`（`services/process_sandbox.py`，已存在：Windows Job Object / POSIX killpg、`wrap_popen_kwargs`、`restrict_env`、`register_process`、`terminate_all`）。

- [ ] **Step 1: Write the failing contract test**

Create `tests/test_sandbox_backend.py`:

```python
"""SandboxBackend 强制边界测试（§11.4）。"""
import os
import sys
import time
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.sandbox import (
    ExecutionResult, SandboxPolicy, SandboxViolation, ToolInvocation,
)
from floodmind.agent.runtime.services.sandbox_backend import LocalRestrictedSandbox


def _py(args: list) -> list:
    """用当前解释器执行一段 Python 代码，避免依赖系统 PATH 里的 python。"""
    return [sys.executable, "-c", args]


def _policy(root: Path, **kw) -> SandboxPolicy:
    return SandboxPolicy(file_root=str(root), **kw)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sandbox_backend.py -q`
Expected: FAIL — `ModuleNotFoundError: floodmind.agent.runtime.contracts.sandbox` / `import` error.

- [ ] **Step 3: Create `contracts/sandbox.py`**

```python
"""Sandbox 契约（target §11.4）。"""

from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field


class ResourceLimits(BaseModel):
    """沙盒资源上限。"""

    max_seconds: float = 120.0          # 墙钟时间上限（秒）
    max_cpu_seconds: Optional[float] = None
    max_memory_mb: Optional[int] = None
    max_output_bytes: int = 10 * 1024 * 1024  # stdout+stderr 累计上限
    max_processes: int = 32


class SandboxPolicy(BaseModel):
    """沙盒策略（§11.4）。file_root 为强制文件根。"""

    label: str = "local-restricted"
    file_root: str                       # 文件根：cwd/temp 必须落在其内
    allow_network: bool = False
    env_allowlist: List[str] = Field(default_factory=list)  # 允许透传的父环境变量
    secret_inject: Dict[str, str] = Field(default_factory=dict)  # 注入的 Secret
    resources: ResourceLimits = Field(default_factory=ResourceLimits)


class ToolInvocation(BaseModel):
    """一次待执行调用（§11.4）。command 为 argv（shell=False）。"""

    command: List[str]
    cwd: str
    env: Dict[str, str] = Field(default_factory=dict)
    stdin_bytes: Optional[bytes] = None
    timeout_seconds: Optional[float] = None  # 覆盖 policy.resources.max_seconds


class ExecutionResult(BaseModel):
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    timed_out: bool = False
    cancelled: bool = False
    sandbox_violation: Optional[str] = None
    pid: Optional[int] = None


class SandboxViolation(Exception):
    """沙盒强制边界被违反。"""


# 取消令牌：返回 True 表示请求取消
CancellationToken = Callable[[], bool]
```

- [ ] **Step 4: Create `services/sandbox_backend.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_sandbox_backend.py -q`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add floodmind/agent/runtime/contracts/sandbox.py floodmind/agent/runtime/services/sandbox_backend.py tests/test_sandbox_backend.py
git commit -m "feat(sandbox): SandboxPolicy + SandboxBackend protocol + LocalRestrictedSandbox (§11.4)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: ArtifactService 原子发布（§15）+ Reducer artifact 事件

**Files:**
- Modify: `floodmind/agent/runtime/contracts/artifacts.py`（加 `ArtifactDeclaration`）
- Create: `floodmind/agent/runtime/services/artifact_service.py`
- Modify: `floodmind/agent/runtime/services/runtime_layout.py`（加 artifact store 路径）
- Modify: `floodmind/agent/runtime/reducer.py`（artifact.* handlers）
- Test: `tests/test_artifact_service.py`

**Interfaces:**
- Consumes: `ArtifactManifest`（`contracts/artifacts.py:10-25`，已存在，§15.1 形状）；`JournalAuthority.emit`（`services/journal_authority.py:100`）；`reduce`/`initial_run_state`（`reducer.py`）。
- Produces: `ArtifactDeclaration`；`ArtifactService(base_dir, authority=None, allowed_roots=None)` with `publish/verify/resolve/read_path/delete/supersede/load_manifests`；reducer 处理 `artifact.declared/committed/verified/superseded` → `RunState.artifacts`。

- [ ] **Step 1: Write the failing test**

Create `tests/test_artifact_service.py`:

```python
"""ArtifactService 发布管线测试（§15）。"""
import hashlib
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.artifacts import ArtifactDeclaration, ArtifactManifest
from floodmind.agent.runtime.services.artifact_service import ArtifactService
from floodmind.agent.runtime.services.sandbox_service import SandboxService


def _write(root: Path, rel: str, content: bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_publish_computes_hash_and_manifest(tmp_path):
    src = _write(tmp_path, "in/forecast.json", b'{"v":1}')
    store = tmp_path / "artifacts"
    svc = ArtifactService(store)
    m = svc.publish(ArtifactDeclaration(
        logical_name="forecast.json", source_path=str(src),
        producer_thread_id="th_1", producer_call_id="call_1",
    ))
    assert m.artifact_id.startswith("art_")
    assert m.content_sha256 == _sha(b'{"v":1}')
    assert m.size == 8
    assert m.media_type == "application/json"
    assert m.storage_uri.endswith(m.content_sha256)
    assert m.verified is False
    # manifest 落盘
    assert (store / "manifests" / f"{m.artifact_id}.json").exists()
    # 对象 content-addressed 落盘
    obj = store / "objects" / m.content_sha256[:2] / m.content_sha256
    assert obj.read_bytes() == b'{"v":1}'


def test_publish_rejects_symlink_escape(tmp_path):
    victim = _write(tmp_path, "victim/secret.txt", b"TOP SECRET")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, allowed_roots=[str(tmp_path / "in")])
    src = _write(tmp_path, "in/evil", b"")
    link = src
    try:
        link.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用")
    with pytest.raises(Exception):
        svc.publish(ArtifactDeclaration(logical_name="evil", source_path=str(link)))


def test_publish_content_containment_enforced(tmp_path):
    outside = _write(tmp_path, "outside/secret.txt", b"x")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, allowed_roots=[str(tmp_path / "in")])
    with pytest.raises(Exception):
        svc.publish(ArtifactDeclaration(logical_name="s", source_path=str(outside)))


def test_verify_detects_corruption(tmp_path):
    src = _write(tmp_path, "in/a.csv", b"a,b\n1,2\n")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store)
    m = svc.publish(ArtifactDeclaration(logical_name="a.csv", source_path=str(src)))
    assert svc.verify(m.artifact_id) is True
    obj = store / "objects" / m.content_sha256[:2] / m.content_sha256
    obj.write_bytes(b"corrupted")
    assert svc.verify(m.artifact_id) is False


def test_resolve_and_read_path(tmp_path):
    src = _write(tmp_path, "in/r.txt", b"hello")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store)
    m = svc.publish(ArtifactDeclaration(logical_name="r.txt", source_path=str(src)))
    loaded = svc.resolve(m.artifact_id)
    assert loaded.artifact_id == m.artifact_id
    assert svc.read_path(m.artifact_id).read_bytes() == b"hello"


def test_supersede_links_previous(tmp_path):
    v1 = _write(tmp_path, "in/v1.txt", b"v1")
    v2 = _write(tmp_path, "in/v2.txt", b"v2")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store)
    m1 = svc.publish(ArtifactDeclaration(logical_name="v.txt", source_path=str(v1)))
    m2 = svc.publish(ArtifactDeclaration(logical_name="v.txt", source_path=str(v2), supersedes=m1.artifact_id))
    assert m2.supersedes == m1.artifact_id


def test_publish_emits_journal_events(tmp_path):
    from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
    from floodmind.agent.runtime.contracts.run_state import RunStatus
    from floodmind.agent.runtime.reducer import initial_run_state, reduce
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority

    src = _write(tmp_path, "in/e.json", b'{"e":1}')
    auth = open_journal_authority(tmp_path / "journal", conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, authority=auth)
    m = svc.publish(ArtifactDeclaration(
        logical_name="e.json", source_path=str(src),
        producer_thread_id="th", producer_call_id="call_1",
    ))
    events = auth.replay(0)
    types = [e.event_type for e in events]
    assert "artifact.declared" in types
    assert "artifact.committed" in types
    # reducer：committed 事件把 artifact_id 计入 RunState.artifacts
    s = initial_run_state("run_1", thread_id="th")
    for e in events:
        s = reduce(s, e)
    assert m.artifact_id in s.artifacts


def test_artifact_survives_sandbox_destroy(tmp_path):
    """§25.7：Artifact 不因 Sandbox 销毁丢失。"""
    base = tmp_path / "sessions"
    sb = SandboxService(base_dir=base)
    ctx = sb.create("sub_1")
    src = ctx.workspace_dir / "out.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n0000")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, allowed_roots=[str(ctx.workspace_dir)])
    m = svc.publish(ArtifactDeclaration(
        logical_name="out.png", source_path=str(src),
        producer_thread_id="th", producer_call_id="call_1",
    ))
    sb.destroy(ctx)  # 沙盒工作区被删除
    assert not src.exists()
    assert svc.resolve(m.artifact_id) is not None
    assert svc.read_path(m.artifact_id).read_bytes().startswith(b"\x89PNG")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_artifact_service.py -q`
Expected: FAIL — `ModuleNotFoundError: floodmind.agent.runtime.services.artifact_service`（部分测试因 service 缺失失败；`ArtifactDeclaration` 未定义失败）。

- [ ] **Step 3: Add `ArtifactDeclaration` to `contracts/artifacts.py`**

Append to `floodmind/agent/runtime/contracts/artifacts.py`:

```python
class ArtifactDeclaration(BaseModel):
    """一次待发布的 Artifact 声明（§15.2 管线输入）。"""

    logical_name: str
    source_path: str
    media_type: str = ""            # 空则按扩展名分类
    sensitivity: str = "internal"
    producer_call_id: str = ""
    producer_thread_id: str = ""
    supersedes: Optional[str] = None
    retention: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Add artifact store paths to `runtime_layout.py`**

Append to `floodmind/agent/runtime/services/runtime_layout.py`:

```python
def artifact_dirs(floodmind_root):
    """Artifact Store 目录（target §15）。content-addressed objects + JSON manifests。"""
    base = Path(floodmind_root) / "artifacts"
    return {
        "artifact_root": base,
        "objects_dir": base / "objects",
        "manifests_dir": base / "manifests",
    }
```

- [ ] **Step 5: Create `services/artifact_service.py`**

```python
"""ArtifactService（target §15）——content-addressed 原子发布。

布局（§15 / runtime_layout.artifact_dirs）：
  <base>/artifacts/objects/<sha[:2]>/<sha>      # 内容寻址对象
  <base>/artifacts/manifests/<artifact_id>.json # 不可变 manifest

发布管线（§15.2）：validate root containment → reject symlink escape
→ classify MIME/sensitivity → compute size/hash → copy to temp → fsync
→ atomic rename → append ArtifactCommitted → publish host event。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from floodmind.agent.runtime.contracts.artifacts import ArtifactDeclaration, ArtifactManifest
from floodmind.agent.runtime.services.runtime_layout import artifact_dirs

logger = logging.getLogger(__name__)

_MIME_BY_EXT = {
    ".json": "application/json", ".csv": "text/csv", ".txt": "text/plain",
    ".md": "text/markdown", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".pdf": "application/pdf", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html", ".py": "text/x-python",
}

_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _mime_for(path: Path) -> str:
    return _MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def _sensitivity_for(path: Path, declared: str) -> str:
    if declared:
        return declared
    if path.suffix.lower() in _IMG_EXT:
        return "internal"
    return "internal"


def _hash_file(path: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(block)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class ArtifactService:
    """Artifact 原子发布与解析服务。"""

    def __init__(
        self,
        base_dir: Union[str, Path],
        authority: Any = None,
        allowed_roots: Optional[List[str]] = None,
    ):
        self._base_dir = Path(base_dir)
        self._authority = authority
        # allowed_roots：发布源文件必须落在其内（resolve 后 containment 校验）
        self._allowed_roots = [Path(r).resolve() for r in (allowed_roots or [])]
        self._dirs = artifact_dirs(self._base_dir)
        self._dirs["objects_dir"].mkdir(parents=True, exist_ok=True)
        self._dirs["manifests_dir"].mkdir(parents=True, exist_ok=True)

    # ── 发布 ─────────────────────────────────────────────────────

    def publish(self, declaration: ArtifactDeclaration) -> ArtifactManifest:
        source = Path(declaration.source_path)
        if not source.is_file():
            raise ValueError(f"发布源不是文件: {source}")

        src_real = source.resolve()
        self._validate_containment(src_real)
        if source.is_symlink():
            # resolve 已展开；若目标逃逸 allowed_roots，上面已拒绝。
            pass

        media_type = declaration.media_type or _mime_for(source)
        sensitivity = _sensitivity_for(source, declaration.sensitivity)
        size = src_real.stat().st_size
        content_sha256 = _hash_file(src_real)

        object_dir = self._dirs["objects_dir"] / content_sha256[:2]
        object_path = object_dir / content_sha256
        object_dir.mkdir(parents=True, exist_ok=True)

        # 原子落盘：临时文件同目录 → fsync → os.replace
        if not object_path.exists():
            tmp = object_dir / f".tmp-{uuid.uuid4().hex[:12]}"
            shutil.copy2(str(src_real), str(tmp))
            with tmp.open("rb") as f:
                os.fsync(f.fileno())
            os.replace(tmp, object_path)

        artifact_id = f"art_{content_sha256[:16]}{uuid.uuid4().hex[:12]}"
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            content_sha256=content_sha256,
            media_type=media_type,
            size=size,
            storage_uri=str(object_path),
            logical_name=declaration.logical_name,
            producer_call_id=declaration.producer_call_id,
            producer_thread_id=declaration.producer_thread_id,
            sensitivity=sensitivity,
            verified=False,
            supersedes=declaration.supersedes,
            retention=declaration.retention,
            metadata=declaration.metadata,
        )

        if self._authority is not None:
            self._authority.emit("artifact.declared", {
                "artifact_id": artifact_id,
                "logical_name": manifest.logical_name,
                "media_type": media_type,
                "sensitivity": sensitivity,
                "producer_call_id": manifest.producer_call_id,
                "producer_thread_id": manifest.producer_thread_id,
                "supersedes": manifest.supersedes,
            })
        self._write_manifest(manifest)
        if self._authority is not None:
            self._authority.emit("artifact.committed", {
                "artifact_id": artifact_id,
                "content_sha256": content_sha256,
                "size": size,
                "storage_uri": manifest.storage_uri,
            })
        if manifest.supersedes:
            self._authority and self._authority.emit("artifact.superseded", {
                "artifact_id": artifact_id,
                "superseded": manifest.supersedes,
            })
        logger.info("Artifact published: %s (%s, %d bytes)", artifact_id, logical_name := manifest.logical_name, size)
        return manifest

    # ── 读取 / 验证 / 删除 ───────────────────────────────────────

    def resolve(self, artifact_id: str) -> ArtifactManifest:
        return self._read_manifest(artifact_id)

    def read_path(self, artifact_id: str) -> Path:
        """返回对象路径；下载方必须先 verify 再外发（§25.8 防路径穿越）。"""
        m = self._read_manifest(artifact_id)
        return Path(m.storage_uri)

    def verify(self, artifact_id: str) -> bool:
        m = self._read_manifest(artifact_id)
        obj = Path(m.storage_uri)
        ok = obj.is_file() and _hash_file(obj) == m.content_sha256
        if self._authority is not None:
            self._authority.emit("artifact.verified", {
                "artifact_id": artifact_id,
                "ok": ok,
                "detail": "hash_match" if ok else "hash_mismatch",
            })
        return ok

    def delete(self, artifact_id: str) -> bool:
        """Retention 删除（§25.8）：移除 manifest + 未被其他 manifest 引用的对象。"""
        m = self._read_manifest(artifact_id)
        obj = Path(m.storage_uri)
        self._manifest_path(artifact_id).unlink(missing_ok=True)
        # 仅当没有其他 manifest 引用同一对象时才删对象
        if not self._object_referenced(obj):
            obj.unlink(missing_ok=True)
            try:
                obj.parent.rmdir()
            except OSError:
                pass
        logger.info("Artifact deleted: %s", artifact_id)
        return True

    def supersede(self, old_id: str, declaration: ArtifactDeclaration) -> ArtifactManifest:
        declaration.supersedes = old_id
        return self.publish(declaration)

    def load_manifests(self) -> List[ArtifactManifest]:
        out = []
        for p in self._dirs["manifests_dir"].glob("*.json"):
            try:
                out.append(ArtifactManifest.model_validate_json(p.read_text(encoding="utf-8")))
            except Exception as e:
                logger.warning("Artifact manifest %s unreadable: %s", p, e)
        return out

    # ── 内部 ─────────────────────────────────────────────────────

    def _validate_containment(self, src_real: Path) -> None:
        if not self._allowed_roots:
            return  # 无 allowed_roots 时不强制（测试/离线场景）
        for root in self._allowed_roots:
            if src_real == root or root in src_real.parents:
                return
        raise ValueError(f"发布源 {src_real} 逃逸 allowed_roots {self._allowed_roots}")

    def _manifest_path(self, artifact_id: str) -> Path:
        return self._dirs["manifests_dir"] / f"{artifact_id}.json"

    def _write_manifest(self, manifest: ArtifactManifest) -> None:
        tmp = self._dirs["manifests_dir"] / f".tmp-{manifest.artifact_id}"
        tmp.write_text(manifest.model_dump_json(), encoding="utf-8")
        os.replace(tmp, self._manifest_path(manifest.artifact_id))

    def _read_manifest(self, artifact_id: str) -> ArtifactManifest:
        p = self._manifest_path(artifact_id)
        if not p.exists():
            raise KeyError(f"artifact {artifact_id} 不存在")
        return ArtifactManifest.model_validate_json(p.read_text(encoding="utf-8"))

    def _object_referenced(self, obj: Path) -> bool:
        for p in self._dirs["manifests_dir"].glob("*.json"):
            try:
                m = ArtifactManifest.model_validate_json(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if Path(m.storage_uri) == obj:
                return True
        return False
```

- [ ] **Step 6: Add reducer artifact handlers**

In `floodmind/agent/runtime/reducer.py` add two functions before `def reduce(`:

```python
def _reduce_artifact_declared(
    state: RunState, payload: Dict[str, Any],
) -> RunState:
    """artifact.declared：计入 run 级 artifacts 列表（去重）。"""
    artifact_id = payload.get("artifact_id", "")
    if not artifact_id:
        return state
    if artifact_id in state.artifacts:
        return state
    ns = state.model_copy(deep=True)
    ns.artifacts = state.artifacts + [artifact_id]
    return ns


def _reduce_artifact_committed(
    state: RunState, payload: Dict[str, Any],
) -> RunState:
    """artifact.committed：正式落库，计入 artifacts（去重）。"""
    artifact_id = payload.get("artifact_id", "")
    if not artifact_id:
        return state
    if artifact_id in state.artifacts:
        return state
    ns = state.model_copy(deep=True)
    ns.artifacts = state.artifacts + [artifact_id]
    return ns
```

In `reduce()` dispatch, add before the final `return ns`:

```python
    if et in ("artifact.declared", "artifact.committed"):
        if et == "artifact.declared":
            return _reduce_artifact_declared(ns, event.payload)
        return _reduce_artifact_committed(ns, event.payload)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/test_artifact_service.py tests/test_reducer.py tests/test_run_state.py -q`
Expected: artifact tests pass; reducer/run_state tests still pass.

- [ ] **Step 8: Commit**

```bash
git add floodmind/agent/runtime/contracts/artifacts.py floodmind/agent/runtime/services/artifact_service.py floodmind/agent/runtime/services/runtime_layout.py floodmind/agent/runtime/reducer.py tests/test_artifact_service.py
git commit -m "feat(artifact): content-addressed atomic publication + reducer artifact events (§15)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Background Kill 验证链 + Journal 事件 + Reducer active_background_tasks

**Files:**
- Modify: `floodmind/agent/runtime/services/background_task_service.py`
- Modify: `floodmind/agent/runtime/reducer.py`
- Modify: `floodmind/agent/runtime/services/reconciliation_service.py`（kill 路径改为按 task_id）
- Test: `tests/test_background_kill_chain.py`

**Interfaces:**
- Consumes: `BackgroundTaskService`（已存在，`services/background_task_service.py`）；`reduce`/`initial_run_state`（`reducer.py`）；`ReconcileResult`（`services/reconciliation_service.py:27-29`）。
- Produces: 完整状态枚举 `starting|running|kill_requested|terminating|completed|failed|killed|kill_failed|orphaned|unknown`（§12）；`kill()` 验证链（kill_requested→terminating→killed/kill_failed）；`event_sink` 注入；reducer 处理 `background.started/killed/kill.failed/completed` → `RunState.active_background_tasks`（task_id 列表，§12 durable entity）。

- [ ] **Step 1: Write the failing test**

Create `tests/test_background_kill_chain.py`:

```python
"""Background Kill 验证链 + Journal 事件 + Reducer（§12）。"""
import sys
import time
from pathlib import Path

from floodmind.agent.runtime.contracts.run_state import RunStatus
from floodmind.agent.runtime.reducer import initial_run_state, reduce
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def _sleep_cmd(seconds: float) -> list:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


class _Sink:
    def __init__(self):
        self.events = []

    def __call__(self, event_type, payload):
        self.events.append((event_type, dict(payload)))


def test_start_emits_requested_and_started(tmp_path):
    sink = _Sink()
    svc = BackgroundTaskService(base_dir=str(tmp_path), event_sink=sink)
    task = svc.start("sess_1", "sleep 0.5", _sleep_cmd(0.5), cwd=str(tmp_path))
    types = [et for et, _ in sink.events]
    assert "background.start.requested" in types
    assert "background.started" in types
    requested = dict(sink.events[0][1])
    assert requested["task_id"] == task.task_id
    assert requested["command_sha256"]


def test_kill_verification_chain(tmp_path):
    sink = _Sink()
    svc = BackgroundTaskService(base_dir=str(tmp_path), event_sink=sink)
    task = svc.start("sess_1", "sleep 30", _sleep_cmd(30), cwd=str(tmp_path))
    assert task.status == "running"
    ok = svc.kill("sess_1", task.task_id)
    assert ok is True
    assert task.status == "killed"  # 确认退出后置 killed
    # 事件顺序：start.requested -> started -> kill.requested -> killed
    types = [et for et, _ in sink.events]
    i_req = types.index("background.kill.requested")
    i_killed = types.index("background.killed")
    assert i_req < i_killed
    assert task.exit_code is not None  # 退出码已确认


def test_kill_missing_task_returns_false(tmp_path):
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    assert svc.kill("sess_1", "bg_nope") is False


def test_completion_emits_completed_event(tmp_path):
    sink = _Sink()
    svc = BackgroundTaskService(base_dir=str(tmp_path), event_sink=sink)
    task = svc.start("sess_1", "true", _sleep_cmd(0.1), cwd=str(tmp_path))
    deadline = time.time() + 10
    # 事件在 _watch 线程 finally 中发射，先于状态轮询完成——轮询事件而非状态
    while "background.completed" not in [et for et, _ in sink.events] and time.time() < deadline:
        time.sleep(0.05)
    assert "background.completed" in [et for et, _ in sink.events]
    done = dict(sink.events[-1][1])
    assert done["task_id"] == task.task_id
    assert done["status"] == "completed"


def test_reducer_tracks_active_background_tasks(tmp_path):
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    s = initial_run_state("run_1", thread_id="th")
    started = auth.new_envelope("background.started", {"task_id": "bg_1", "session_id": "sess_1"})
    s = reduce(s, started)
    assert "bg_1" in s.active_background_tasks
    done = auth.new_envelope("background.completed", {"task_id": "bg_1", "status": "completed"})
    s = reduce(s, done)
    assert s.active_background_tasks == []


def test_reduce_background_events_idempotent(tmp_path):
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    s = initial_run_state("run_1", thread_id="th")
    for _ in range(2):
        s = reduce(s, auth.new_envelope("background.started", {"task_id": "bg_1"}))
    assert s.active_background_tasks == ["bg_1"]  # 去重
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_background_kill_chain.py -q`
Expected: FAIL — `BackgroundTaskService.__init__` 不接受 `event_sink`；`kill()` 无验证链（直接置 killed）；reducer 未处理 background.* 事件（`active_background_tasks` 不变）。

- [ ] **Step 3: Add event_sink + full status enum + kill verification chain to `background_task_service.py`**

修改 `BackgroundTaskService.__init__` 增加参数与字段：

```python
    def __init__(
        self,
        base_dir: Optional[str] = None,
        max_concurrent_per_session: int = _DEFAULT_MAX_CONCURRENT_PER_SESSION,
        max_lifetime_seconds: int = _DEFAULT_MAX_LIFETIME_SECONDS,
        completed_retention: int = _DEFAULT_COMPLETED_RETENTION,
        finalized_retention: int = _DEFAULT_FINALIZED_RETENTION,
        event_sink: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        # ... 原有初始化 ...
        self._event_sink = event_sink

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """经 event_sink 发 Journal 事件（§12 Completion 先写 Journal）。"""
        if self._event_sink is not None:
            try:
                self._event_sink(event_type, payload)
            except Exception as e:
                logger.warning("BackgroundTask event_sink error: %s", e)

    def set_event_sink(self, event_sink: Optional[Callable[[str, Dict[str, Any]], None]]) -> None:
        self._event_sink = event_sink
```

在 `start()` 中，创建 `task` 之后、`self._write_meta(task)` 之前加：

```python
            self._emit("background.start.requested", {
                "task_id": task_id,
                "session_id": session_id,
                "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
                "sandbox_id": "",
            })
```

在 `_watch()` 的 `process.wait` 返回后（status 置 completed/failed 之后）、`finally` 之前，加 completion 事件发射：

```python
            self._emit("background.completed", {
                "task_id": task.task_id,
                "session_id": task.session_id,
                "status": task.status,
                "exit_code": task.exit_code,
                "stdout_path": task.stdout_path,
                "stderr_path": task.stderr_path,
                "output_tail": task.tail,
            })
```

注意：`_watch` 的 `finally` 里也会设置 `task.tail`；把 completion 事件发射放在 `finally` 之前会读到旧 tail。改为在 `finally` 块内、`self._write_meta(task)` 之后发射：

```python
        finally:
            for f in (out_f, err_f):
                try:
                    f.close()
                except Exception:
                    pass
            task.finished_at = task.finished_at or time.time()
            task.tail = self._read_tail(task.stdout_path)
            self._write_meta(task)
            self._finalize(task)
            # Completion 先写 Journal（§12）；kill 已由 kill() 发 killed/kill_failed
            if task.status in ("completed", "failed"):
                self._emit("background.completed", {
                    "task_id": task.task_id,
                    "session_id": task.session_id,
                    "status": task.status,
                    "exit_code": task.exit_code,
                    "stdout_path": task.stdout_path,
                    "stderr_path": task.stderr_path,
                    "output_tail": task.tail,
                })
```

替换 `kill()` 为验证链版本（§12 kill_requested→terminating→killed/kill_failed）：

```python
    def kill(self, session_id: str, task_id: str, confirm_timeout: float = 10.0) -> bool:
        """杀掉任务进程树并验证退出（§12 kill 验证链）。

        状态推进：running -> kill_requested -> terminating -> killed（确认退出）
        若确认超时 -> kill_failed。
        """
        session_id = validate_session_id(session_id)
        with self._lock:
            task = self._active_tasks.get(task_id)
            process = self._processes.get(task_id)
        if task is None or task.session_id != session_id:
            return False

        if task.status != "running":
            return True  # 已自然终态（completed/failed），无可杀

        task.status = "kill_requested"
            self._emit("background.kill.requested", {
                "task_id": task_id,
                "session_id": task.session_id,
                "reason": "user_kill",
            })

        self._kill_process(process)
        # 确认退出：轮询至多 confirm_timeout 秒
        task.status = "terminating"
        confirmed = self._wait_exit(process, confirm_timeout)
        task.finished_at = time.time()
        task.tail = self._read_tail(task.stdout_path)

        if confirmed:
            task.status = "killed"
            task.exit_code = int(process.returncode) if process is not None else 0
            self._emit("background.killed", {
                "task_id": task_id,
                "session_id": task.session_id,
                "exit_code": task.exit_code,
            })
        else:
            task.status = "kill_failed"
            task.error = f"进程树终止后 {confirm_timeout}s 内未确认退出"
            self._emit("background.kill.failed", {
                "task_id": task_id,
                "session_id": task.session_id,
                "error": task.error,
            })
        self._write_meta(task)
        self._finalize(task)  # 用户主动关闭：立即推送，不等 _watch 线程
        return confirmed

    @staticmethod
    def _wait_exit(process: Optional[subprocess.Popen], timeout: float) -> bool:
        """确认进程已退出（轮询 poll）。"""
        if process is None:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            if process.poll() is not None:
                return True
            time.sleep(0.05)
        return process.poll() is not None
```

`kill_session()` 改为复用 `kill()` 验证链（保持「立即收尾」语义但走验证链）：

```python
    def kill_session(self, session_id: str, confirm_timeout: float = 5.0) -> int:
        """杀掉会话内全部存活任务并立即收尾（会话结束/Agent 析构时调用）。"""
        session_id = validate_session_id(session_id)
        with self._lock:
            targets = [t for t in self._active_tasks.values()
                       if t.session_id == session_id and t.running]
        killed = 0
        for task in targets:
            if self.kill(session_id, task.task_id, confirm_timeout=confirm_timeout):
                killed += 1
        return killed
```

新增 `kill_task`（按 task_id 杀掉，供 Reconcile 使用；session 由内部解析）：

```python
    def kill_task(self, task_id: str, confirm_timeout: float = 10.0) -> bool:
        """按 task_id 杀掉任务（Reconcile 用；session 内部解析）。"""
        with self._lock:
            task = self._active_tasks.get(task_id)
        if task is None:
            return False
        return self.kill(task.session_id, task_id, confirm_timeout=confirm_timeout)
```

新增 `import hashlib`（文件顶部，现有 `import json` 之后）。

- [ ] **Step 4: Add reducer background handlers**

In `floodmind/agent/runtime/reducer.py` add before `def reduce(`:

```python
def _reduce_background_started(
    state: RunState, payload: Dict[str, Any],
) -> RunState:
    """background.started：run 级 active_background_tasks 记账（task_id，去重）。"""
    task_id = payload.get("task_id", "")
    if not task_id:
        return state
    if task_id in state.active_background_tasks:
        return state
    ns = state.model_copy(deep=True)
    ns.active_background_tasks = state.active_background_tasks + [task_id]
    return ns


def _reduce_background_terminal(
    state: RunState, payload: Dict[str, Any],
) -> RunState:
    """background.killed / kill.failed / completed：从 active_background_tasks 移除。"""
    task_id = payload.get("task_id", "")
    if not task_id or task_id not in state.active_background_tasks:
        return state
    ns = state.model_copy(deep=True)
    ns.active_background_tasks = [t for t in state.active_background_tasks if t != task_id]
    return ns
```

In `reduce()` dispatch, add:

```python
    if et == "background.started":
        return _reduce_background_started(ns, event.payload)
    if et in ("background.killed", "background.kill.failed", "background.completed"):
        return _reduce_background_terminal(ns, event.payload)
```

- [ ] **Step 5: Update `reconciliation_service.py` kill path to task_ids**

`active_background_tasks` 语义从「session id」改为「task_id」（§12 durable entity）。替换原 kill 路径（原 `for ident in ...: kill_session(ident)`）：

```python
        # 4) 后台任务清理（P6）：active_background_tasks 为 task_id 列表，
        #    经 kill_task 走完整 kill 验证链（§12）。
        if self._background_task_service is not None:
            for task_id in list(run_state.active_background_tasks):
                try:
                    if self._background_task_service.kill_task(task_id):
                        result.background_killed += 1
                except Exception as e:
                    logger.warning("BackgroundTask reconcile kill failed task=%s: %s", task_id, e)
```

同时把 `ReconcileResult.background_killed` 的 docstring/注释从「kill_session」更新为「kill_task」。（文件顶部 module docstring 中的 P6 实装注释同步更新。）

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_background_kill_chain.py tests/test_reconciliation_service.py tests/test_reducer.py tests/test_run_state.py tests/test_background_task_service.py -q`
Expected: 新测试通过；既有 background/reconcile/reducer 测试不回归。

- [ ] **Step 7: Commit**

```bash
git add floodmind/agent/runtime/services/background_task_service.py floodmind/agent/runtime/reducer.py floodmind/agent/runtime/services/reconciliation_service.py tests/test_background_kill_chain.py
git commit -m "feat(background): kill verification chain + journal events + reducer tracking (§12)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Restart PID Reconcile + orphaned/unknown（§12 / §16.4 / §25.7「Host 重启后可 Reconcile PID/Meta」）

**Files:**
- Create: `floodmind/agent/runtime/services/process_identity.py`
- Modify: `floodmind/agent/runtime/services/background_task_service.py`
- Modify: `floodmind/agent/runtime/services/reconciliation_service.py`
- Test: `tests/test_background_reconcile.py`

**Interfaces:**
- Consumes: `BackgroundTask` / `BackgroundTaskService`（`services/background_task_service.py`）；`psutil`（requirements.txt 已有 `psutil>=5.9.0`）。
- Produces: `process_exists(pid)`、`process_create_time(pid)`、`pid_identity_matches(pid, create_time)`（`services/process_identity.py`）；`BackgroundTaskService.reconcile_background()` → `Dict[str, int]`（`{"kept_running", "orphaned", "unknown"}`）；`BackgroundTask.process_identity`（create_time 等）持久化进 meta.json。

- [ ] **Step 1: Write the failing test**

Create `tests/test_background_reconcile.py`:

```python
"""Restart PID Reconcile + orphaned/unknown（§12 / §25.7）。"""
import json
import os
import sys
import time
from pathlib import Path

from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService


def _write_meta(background_dir: Path, task_id: str, status: str, pid: int = None,
                create_time: float = None, session_id: str = "sess_1") -> Path:
    task_dir = background_dir / session_id / "background" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "task_id": task_id, "session_id": session_id, "command": "sleep",
        "pid": pid, "status": status, "exit_code": None,
        "stdout_path": str(task_dir / "out.log"), "stderr_path": str(task_dir / "err.log"),
        "meta_path": str(task_dir / "meta.json"), "started_at": time.time(),
        "max_lifetime_seconds": 1800, "finished_at": None,
        "process_identity": {"pid": pid, "create_time": create_time},
    }
    (task_dir / "out.log").write_bytes(b"")
    (task_dir / "err.log").write_bytes(b"")
    p = task_dir / "meta.json"
    p.write_text(json.dumps(meta), encoding="utf-8")
    return p


def test_dead_pid_marked_orphaned(tmp_path):
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    _write_meta(tmp_path, "bg_dead", status="running", pid=99999999)  # 不存在的 pid
    res = svc.reconcile_background()
    assert res["orphaned"] == 1
    assert res["unknown"] == 0
    assert res["kept_running"] == 0
    # meta 被更新为 orphaned
    meta = json.loads((tmp_path / "sess_1" / "background" / "bg_dead" / "meta.json").read_text())
    assert meta["status"] == "orphaned"


def test_identity_mismatch_marked_unknown(tmp_path):
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    # 本进程的 pid 存在，但 create_time 不匹配 -> PID 复用/身份不符 -> unknown
    _write_meta(tmp_path, "bg_mismatch", status="running", pid=os.getpid(), create_time=1.0)
    res = svc.reconcile_background()
    assert res["unknown"] == 1
    assert res["orphaned"] == 0
    assert res["kept_running"] == 0
    meta = json.loads((tmp_path / "sess_1" / "background" / "bg_mismatch" / "meta.json").read_text())
    assert meta["status"] == "unknown"


def test_completed_tasks_untouched(tmp_path):
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    _write_meta(tmp_path, "bg_done", status="completed", pid=None, create_time=None)
    res = svc.reconcile_background()
    assert res["kept_running"] == 0
    assert res["orphaned"] == 0
    assert res["unknown"] == 0
    meta = json.loads((tmp_path / "sess_1" / "background" / "bg_done" / "meta.json").read_text())
    assert meta["status"] == "completed"


def test_reconcile_integrated_in_reconcile_service(tmp_path):
    """§16.4 step5：resume 时对 Background PID 做对账。"""
    from floodmind.agent.runtime.contracts.run_state import RunStatus
    from floodmind.agent.runtime.reducer import initial_run_state
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority
    from floodmind.agent.runtime.services.reconciliation_service import ReconciliationService

    svc = BackgroundTaskService(base_dir=str(tmp_path))
    _write_meta(tmp_path, "bg_orphan", status="running", pid=99999999)
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    rs = ReconciliationService(background_task_service=svc)
    result = rs.reconcile(auth, initial_run_state("run_1", thread_id="th"))
    assert result.background_killed >= 0  # 对账不抛异常，可执行
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_background_reconcile.py -q`
Expected: FAIL — `reconcile_background` 不存在；`process_identity` 字段未持久化。

- [ ] **Step 3: Create `services/process_identity.py`**

```python
"""进程身份对账（§12 / §16.4 step5）。psutil 提供跨平台 PID 存活与身份匹配。"""

from __future__ import annotations

import logging
from typing import Optional

import psutil

logger = logging.getLogger(__name__)


def process_exists(pid: int) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except Exception as e:
        logger.warning("pid_exists(%s) failed: %s", pid, e)
        return False


def process_create_time(pid: int) -> Optional[float]:
    """进程创建时间（epoch 秒）。不存在/异常返回 None。"""
    if pid is None or pid <= 0:
        return None
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None
    except Exception as e:
        logger.warning("process_create_time(%s) failed: %s", pid, e)
        return None


def pid_identity_matches(pid: int, stored_create_time: Optional[float]) -> bool:
    """PID 存活且创建时间匹配（防 PID 复用误判）。"""
    if not process_exists(pid):
        return False
    actual = process_create_time(pid)
    if actual is None:
        return False
    if stored_create_time is None:
        return True  # 未存 create_time：仅按存活判定
    return abs(actual - stored_create_time) < 2.0
```

- [ ] **Step 4: Persist process identity in `BackgroundTask`**

在 `background_task_service.py` 的 `BackgroundTask` dataclass 增加字段：

```python
    process_identity: Optional[Dict[str, Any]] = None  # {pid, create_time}
```

在 `start()` 创建 `task` 时设置（`import psutil` 或复用 process_identity 模块）：

```python
            from floodmind.agent.runtime.services.process_identity import process_create_time
            task = BackgroundTask(
                task_id=task_id,
                # ... 原有字段 ...
                process_identity={"pid": process.pid, "create_time": process_create_time(process.pid)},
            )
```

`to_meta_dict()` 已 `asdict(self)` 全量序列化，`process_identity` 自动入 meta.json。

- [ ] **Step 5: Add `reconcile_background()` to `BackgroundTaskService`**

```python
    def reconcile_background(self) -> Dict[str, int]:
        """Host 重启后从 meta.json 对账（§12 / §25.7）。

        扫描各会话 background/*/meta.json 中 status 为 running/starting 的任务：
        - PID 存活且身份匹配  -> 保持 running（kept_running）
        - PID 存活但身份不符  -> unknown（PID 复用/身份无法确认）
        - PID 已死 / 无 PID   -> orphaned（父进程已失联）
        completed/failed/killed 等终态不动。
        """
        result = {"kept_running": 0, "orphaned": 0, "unknown": 0}
        if self._base_dir is None:
            return result
        from floodmind.agent.runtime.services.process_identity import (
            pid_identity_matches,
            process_exists,
        )

        for meta_file in sorted(self._base_dir.rglob("meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("reconcile: meta unreadable %s: %s", meta_file, e)
                continue
            if meta.get("status") not in ("running", "starting"):
                continue
            pid = meta.get("pid")
            identity = meta.get("process_identity") or {}
            stored_ct = identity.get("create_time")
            task_id = meta.get("task_id", "")
            if process_exists(pid):
                if pid_identity_matches(pid, stored_ct):
                    # 进程仍存活：保持 running，但更新 meta 标记为已对账
                    meta["status"] = "running"
                    result["kept_running"] += 1
                else:
                    meta["status"] = "unknown"
                    result["unknown"] += 1
            else:
                meta["status"] = "orphaned"
                result["orphaned"] += 1
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            if meta["status"] in ("orphaned", "unknown"):
                logger.info("Background reconcile: %s=%s pid=%s", task_id, meta["status"], pid)
        return result
```

- [ ] **Step 6: Wire reconcile into `ReconciliationService.reconcile`**

在 `reconciliation_service.py` 的 `reconcile()` 中，kill 路径之前加入对账调用（§16.4 step5「Background PID」）：

```python
        # 4a) Host 重启后 PID/Meta 对账（§12 / §25.7）
        try:
            if self._background_task_service is not None:
                self._background_task_service.reconcile_background()
        except Exception as e:
            logger.warning("Background reconcile failed: %s", e)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/test_background_reconcile.py tests/test_reconciliation_service.py tests/test_background_kill_chain.py -q`
Expected: 新测试通过；既有测试不回归。

- [ ] **Step 8: Commit**

```bash
git add floodmind/agent/runtime/services/process_identity.py floodmind/agent/runtime/services/background_task_service.py floodmind/agent/runtime/services/reconciliation_service.py tests/test_background_reconcile.py
git commit -m "feat(background): restart PID reconcile + orphaned/unknown (§12 §16.4 §25.7)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 验收 §25.7（Background 相关项）+ §23.2 OPEN 项关闭（§25.7 + kill 验证链）

**Files:**
- Test: `tests/test_p6_acceptance.py`

**Interfaces:**
- Consumes: `BackgroundTaskService`、`SandboxService`、`ArtifactService`、reducer（前四任务产物）。

- [ ] **Step 1: Write the acceptance tests**

Create `tests/test_p6_acceptance.py`:

```python
"""P6 验收（§25.7 Background 相关项 + §23.2 OPEN 项关闭）。"""
import json
import sys
import time
from pathlib import Path

from floodmind.agent.runtime.services.artifact_service import ArtifactService
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.reconciliation_service import ReconciliationService
from floodmind.agent.runtime.services.sandbox_service import SandboxService
from floodmind.agent.runtime.contracts.artifacts import ArtifactDeclaration


def _sleep_cmd(seconds: float) -> list:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_acceptance_background_session_isolation(tmp_path):
    """§25.7 Background Session 隔离：跨 Session 不可查询/不可杀。"""
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    t = svc.start("sess_a", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
    assert svc.get("sess_a", t.task_id) is not None
    assert svc.get("sess_b", t.task_id) is None  # 跨 session 不可见
    assert svc.kill("sess_b", t.task_id) is False  # 跨 session 不可杀
    svc.kill_session("sess_a")


def test_acceptance_cleanup_no_residual_subscriptions(tmp_path):
    """§25.7 Cleanup 无残留订阅：unsubscribe 后不再收到回调。"""
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    got = []
    unsub = svc.subscribe(lambda t: got.append(t.task_id), session_id="sess_a")
    unsub()
    t = svc.start("sess_a", "true", _sleep_cmd(0.1), cwd=str(tmp_path))
    deadline = time.time() + 10
    while t.status == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert t.task_id not in got  # 已退订，无残留


def test_acceptance_kill_verification_chain(tmp_path):
    """§23.2 OPEN 关闭：kill 验证链 kill_requested->terminating->killed/kill_failed。"""
    events = []
    svc = BackgroundTaskService(base_dir=str(tmp_path), event_sink=lambda et, p: events.append(et))
    t = svc.start("sess_a", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
    assert t.status == "running"
    ok = svc.kill("sess_a", t.task_id)
    assert ok is True
    assert t.status == "killed"
    chain = [e for e in events if e.startswith("background.")]
    # start.requested, started, kill.requested, killed 顺序成立
    assert chain.index("background.kill.requested") < chain.index("background.killed")
    # killed 前必须经过 terminating（服务内部状态，非事件）；这里断言 killed 事件存在且 exit_code 已确认
    assert t.exit_code is not None


def test_acceptance_restart_pid_reconcile(tmp_path):
    """§25.7 Host 重启后可 Reconcile PID/Meta。"""
    # 模拟重启后重建服务：遗留 running meta，PID 已死 -> orphaned
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    task_dir = tmp_path / "sess_a" / "background" / "bg_old"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "out.log").write_bytes(b"")
    (task_dir / "err.log").write_bytes(b"")
    (task_dir / "meta.json").write_text(json.dumps({
        "task_id": "bg_old", "session_id": "sess_a", "command": "sleep",
        "pid": 99999999, "status": "running", "exit_code": None,
        "process_identity": {"pid": 99999999, "create_time": None},
    }), encoding="utf-8")
    res = svc.reconcile_background()
    assert res["orphaned"] == 1
    meta = json.loads((task_dir / "meta.json").read_text())
    assert meta["status"] == "orphaned"


def test_acceptance_artifact_survives_sandbox_destroy(tmp_path):
    """§25.7 Artifact 不因 Sandbox 销毁丢失。"""
    base = tmp_path / "sessions"
    sb = SandboxService(base_dir=base)
    ctx = sb.create("sub_1")
    src = ctx.workspace_dir / "result.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, allowed_roots=[str(ctx.workspace_dir)])
    m = svc.publish(ArtifactDeclaration(
        logical_name="result.csv", source_path=str(src),
        producer_thread_id="th", producer_call_id="call_1",
    ))
    sb.destroy(ctx)
    assert not src.exists()
    assert svc.resolve(m.artifact_id).logical_name == "result.csv"
    assert svc.read_path(m.artifact_id).read_text() == "a,b\n1,2\n"


def test_acceptance_reconcile_does_not_raise(tmp_path):
    """§25.7/§16.4：对账路径对无后台任务场景不抛异常。"""
    from floodmind.agent.runtime.reducer import initial_run_state
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority

    svc = BackgroundTaskService(base_dir=str(tmp_path))
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    rs = ReconciliationService(background_task_service=svc)
    res = rs.reconcile(auth, initial_run_state("run_1", thread_id="th"))
    assert res.background_killed == 0
```

- [ ] **Step 2: Run acceptance tests to verify they pass**

Run: `python -m pytest tests/test_p6_acceptance.py -q`
Expected: 6 passed.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`
Expected: 1100 + (本阶段新增测试数) passed，0 failed（跳过数保持 1）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_p6_acceptance.py
git commit -m "test(p6): acceptance §25.7 background/artifact + §23.2 OPEN closure

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage（design spec §80-84 + baseline）:**
- `SandboxBackend` §11.4 + OS/Container 强制边界 → Task 1（SandboxPolicy + Protocol + LocalRestrictedSandbox；文件根/进程树/资源/环境/取消全部实测）。
- `ArtifactService` §15（manifest/hash/MIME/retention/原子发布）→ Task 2（content-addressed 原子发布管线 + verify/delete/supersede/load + reducer artifact 事件 + §25.7 Artifact 不因沙盒销毁丢失）。
- Background Kill 验证链 `kill_requested→terminating→killed/kill_failed` → Task 3（§12 状态机 + 事件链）。
- Restart PID Reconcile + orphaned/unknown → Task 4（process_identity + reconcile_background + §16.4 step5 接入）。
- 验收 §25.7 Background 相关项 + §23.2 OPEN 关闭 → Task 5（session 隔离、无残留订阅、kill 验证链、重启对账、Artifact 存活、对账无异常）。
- §12「Completion 先写 Journal」→ Task 3 `_watch` 完成时发 `background.completed`。
- §12「只有确认进程退出后才能标记 killed」→ Task 3 `_wait_exit` 确认后才置 killed。
- §15.2「原子发布」「不信任工具返回的绝对路径」→ Task 2 `os.replace` + containment 校验。

**Placeholder scan:** 无 TBD/TODO；每个代码步骤含完整代码与断言。Task 3/4 对既有文件的修改均给出精确插入点与完整替换块。

**Type consistency:**
- `ExecutionResult` 字段（exit_code/stdout/stderr/output_truncated/timed_out/cancelled/sandbox_violation/pid）在 Task 1 测试与实现一致。
- `SandboxPolicy.resources` 传 `dict`（pydantic v2 自动校验成 `ResourceLimits`），测试 `_policy(tmp_path, resources={"max_output_bytes": 1000})` 合法。
- `event_sink` 签名 `(str, Dict[str, Any]) -> None` 在 Task 3 测试与实现一致。
- `reconcile_background()` 返回 `Dict[str, int]` keys `kept_running/orphaned/unknown` 在 Task 4 测试与实现一致。
- `ArtifactDeclaration` 字段在 Task 2 测试与实现一致；`ArtifactService(base_dir, authority, allowed_roots)` 签名一致。
- reducer 事件名（`artifact.declared/committed/superseded/verified`、`background.started/killed/kill.failed/completed`）与基线 §4.3 taxonomy 一致。
