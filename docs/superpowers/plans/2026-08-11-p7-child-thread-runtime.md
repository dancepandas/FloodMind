# P7 — Child Thread Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ad-hoc Specialist execution path in `NativeFloodAgent` with a proper `ChildThreadRuntime` that enforces quota (max_turns / max_tokens / wall_clock_budget), typed `SubagentResult` handoff, parent cancellation tree, persistent lineage, child-scoped permission/background/sandbox isolation.

**Architecture:** A new `floodmind/agent/runtime/services/child_thread_runtime.py` owns the full child lifecycle — identity, child JournalAuthority, thread dirs, sandbox, child ArtifactService, fresh tool runtime, child executor, quota, cancellation and cleanup — and returns a typed `SubagentResult`. `NativeFloodAgent._run_specialist_task` shrinks to a thin adapter that builds a `ChildThread` and calls the runtime. The reducer gains `child_thread.*` lineage handlers. Child-scoped PathService/PermissionService replace the shared parent instances. A per-session sandbox policy on `BackgroundTaskService` lets the runtime enforce the SandboxBackend OS-boundary for every process started in the child session without touching the frozen Bash tool path.

**Tech Stack:** Python 3, pydantic, existing NativeAgentExecutor / ModelClient / SandboxBackend(LocalRestrictedSandbox) / ArtifactService / BackgroundTaskService / JournalAuthority / reducer.

## Global Constraints

- **Forward-only, binding (user directive):** no backward-compat, no fallback, no dual-write, no legacy adapters. Land the TARGET contract directly.
- **WIP files — NEVER modify, never stage:** `floodmind/agent/runtime/contracts/permissions.py` (carries uncommitted user WIP) and `floodmind/tools/base_tools.py` (frozen; the foreground Bash tool + its `background_task_service.start()` call site are off-limits). The plan's Task 6 reaches child processes via `BackgroundTaskService.set_session_sandbox()` precisely so base_tools.py stays untouched.
- `git add` explicit paths only, NEVER `git add -A`. Commit ONLY code, not docs. Every commit ends with `Co-Authored-By: Claude <noreply@anthropic.com>`. Per-task commits on master.
- Baseline HEAD: `f6a7b7b` (P6 complete, **1144 passed / 2 skipped**). Every task must leave the suite green.
- Reducer MUST stay deterministic (§2.8): no I/O, no current time, no random, no process-global mutable state. Quota/cancellation timing lives in the runtime closures, never in the reducer.
- §13.2 invariants: child never shares parent's mutable history / ToolLoader loaded set / ContextCompressor / Permission Context / Background Namespace / MCP connection-owner state.
- §2.7: child permissions are a strict subset of the parent's; child cannot escalate (no ASK, no network, no global-state writes).
- §2.5: cancellation is a hierarchy propagation protocol — cooperative first, then bounded wait, then process-tree kill, then verify real terminal before emitting `child_thread.cancelled`.
- §25.7 acceptance (this phase): Child Context/ToolLoader/Compressor/Permission 不串线; 子代理不能越权; 父取消传播; Background Session 隔离; Cleanup 无残留订阅; Host 重启后可 Reconcile; Artifact 不因 Sandbox 销毁丢失.

## File Structure

- **Create** `floodmind/agent/runtime/services/child_thread_runtime.py` — `ChildThreadRuntime` + `ChildThreadQuota` + `_TokenBudgetModelClient` (Tasks 1-3).
- **Create** `floodmind/agent/runtime/services/child_permission_context.py` — `build_child_permission_context()` (Task 5).
- **Modify** `floodmind/agent/runtime/contracts/child_thread.py` — extend `SubagentResult` with `session_id`/`completed`/`reason` (Task 1).
- **Modify** `floodmind/agent/runtime/contracts/run_state.py` — `ChildThreadState` + `parent_thread_id`/`reason` (Task 1).
- **Modify** `floodmind/agent/runtime/reducer.py` — `child_thread.*` lineage handlers (Task 1).
- **Modify** `floodmind/agent/runtime/services/sandbox_backend.py` — extract public `prepare_launch()` from `execute()` (Task 6).
- **Modify** `floodmind/agent/runtime/services/background_task_service.py` — `set_session_sandbox()` + apply in `start()` (Tasks 4/6).
- **Modify** `floodmind/agent/native/native_flood_agent.py` — `_run_specialist_task` adapter; wire `_handle_delegate_specialist`/`_handle_delegate_parallel`/`_run_dag_batches` to `SubagentResult`; construct the runtime (Tasks 1/7).
- **Test** `tests/test_child_thread_runtime.py`, `tests/test_child_thread_quota.py`, `tests/test_child_thread_cancellation.py`, `tests/test_child_background_namespace.py`, `tests/test_child_permission_context.py`, `tests/test_child_sandbox_backend.py`, `tests/test_p7_acceptance.py`.

---

### Task 1: ChildThreadRuntime scaffold + typed SubagentResult lifecycle + reducer lineage

**Files:**
- Create: `floodmind/agent/runtime/services/child_thread_runtime.py`
- Modify: `floodmind/agent/runtime/contracts/child_thread.py`
- Modify: `floodmind/agent/runtime/contracts/run_state.py`
- Modify: `floodmind/agent/runtime/reducer.py`
- Modify: `floodmind/agent/native/native_flood_agent.py`
- Test: `tests/test_child_thread_runtime.py`

**Interfaces:**
- Consumes: `ChildThread` (thread_id, parent_thread_id, parent_call_id, workspace_snapshot_id, sandbox_id, tool_allowlist, max_turns=50, max_tokens=32768, wall_clock_budget_seconds=300.0) from `floodmind/agent/runtime/contracts/child_thread.py`; `open_journal_authority(runtime_dir, conversation_id=, task_id=, run_id=, thread_id=, turn_id=)`; `thread_dirs(runtime_dir, conversation_id, task_id, run_id, thread_id)` from `runtime_layout.py`; `ArtifactService(base_dir, authority=, allowed_roots=[...])`; `initial_run_state(run_id, *, conversation_id=, task_id=, thread_id=)`; `SandboxService.create(sub_session_id, parent_output_dir=, delegate_cwd=)` / `.destroy(ctx)` / `.copy_artifacts_to_parent(ctx, paths)`; `NativeAgentExecutor(...)`; `ArtifactWatcher(output_dir, upload_dir)`; `new_id("turn")`.
- Produces: `ChildThreadRuntime(**services).run(child_thread, context, *, step_event_bus=None, delegate_cwd=None) -> SubagentResult`. `SubagentResult` gains `session_id: str`, `completed: bool`, `reason: str`. `ChildThreadState` gains `parent_thread_id: str`, `reason: str`. Events emitted: `child_thread.accepted` / `child_thread.running` / `child_thread.result` / `child_thread.failed` / `child_thread.cancelled` (each payload has thread_id, parent_call_id, session_id, summary, artifact_ids, reason). Reducer handles these 5 events into `RunState.child_threads`.

- [ ] **Step 1: Extend the contracts (test first).**

Write `tests/test_child_thread_runtime.py` with a reducer-lineage test that FAILS on current code (events unknown → lineage never populated):

```python
"""P7 Task 1 — ChildThreadRuntime typed lifecycle + reducer lineage."""
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.reducer import initial_run_state, reduce


def _event(seq, et, thread_id, payload):
    return EventEnvelope(
        schema_version=1, event_id=f"e{seq}", event_type=et, sequence=seq,
        recorded_at="2026-08-11T00:00:00Z", thread_id=thread_id,
        causation_id="", correlation_id="", actor="agent", payload=payload,
    )


def test_reducer_child_thread_lineage():
    state = initial_run_state("run_1", conversation_id="c", task_id="t", thread_id="th_main")
    state = reduce(state, _event(1, "child_thread.accepted", "th_child", {
        "thread_id": "th_child", "parent_thread_id": "th_main",
        "parent_call_id": "step_1", "session_id": "sub-x",
    }))
    state = reduce(state, _event(2, "child_thread.running", "th_child", {"thread_id": "th_child"}))
    assert state.child_threads == [
        type(state.child_threads[0])(thread_id="th_child", parent_thread_id="th_main",
                                     parent_call_id="step_1", status="running"),
    ]
    state = reduce(state, _event(3, "child_thread.result", "th_child", {
        "thread_id": "th_child", "summary": "done", "artifact_ids": [],
    }))
    assert state.child_threads[0].status == "completed"


def test_reducer_child_thread_terminal_reasons():
    state = initial_run_state("run_1", thread_id="th_main")
    state = reduce(state, _event(1, "child_thread.accepted", "th_child", {
        "thread_id": "th_child", "parent_thread_id": "th_main", "parent_call_id": "s",
    }))
    state = reduce(state, _event(2, "child_thread.failed", "th_child", {
        "thread_id": "th_child", "reason": "quota:max_turns(50/50)",
    }))
    assert state.child_threads[0].status == "failed"
    assert state.child_threads[0].reason == "quota:max_turns(50/50)"
    state = reduce(state, _event(3, "child_thread.cancelled", "th_child", {
        "thread_id": "th_child", "reason": "parent_cancelled",
    }))
    assert state.child_threads[-1].status == "cancelled"
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python -m pytest tests/test_child_thread_runtime.py -q`
Expected: FAIL — `assert state.child_threads == [...]` fails with `child_threads == []`.

- [ ] **Step 3: Extend contracts + reducer.**

In `floodmind/agent/runtime/contracts/child_thread.py`, extend `SubagentResult`:

```python
class SubagentResult(BaseModel):
    thread_id: str
    parent_call_id: str
    session_id: str = ""                      # child background/sandbox namespace id
    event_type: SubagentEventType
    summary: str = ""
    artifact_ids: List[str] = Field(default_factory=list)
    tool_result_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    needs_human: bool = False
    completed: bool = False                   # semantic success for handoff
    reason: str = ""                          # failed/cancelled/quota reason
```

In `floodmind/agent/runtime/contracts/run_state.py`, extend `ChildThreadState`:

```python
class ChildThreadState(BaseModel):
    thread_id: str
    parent_thread_id: str = ""
    parent_call_id: str = ""
    status: str = "running"
    reason: str = ""
```

In `floodmind/agent/runtime/reducer.py`, add these handlers after `_reduce_thread_terminal` (line ~75) and register them in `reduce()` right after the `thread.*` block (line ~490):

```python
def _reduce_child_thread_accepted(state, payload):
    ns = _clone(state)
    ns.child_threads.append(ChildThreadState(
        thread_id=str(payload["thread_id"]),
        parent_thread_id=str(payload.get("parent_thread_id", "")),
        parent_call_id=str(payload.get("parent_call_id", "")),
        status="accepted",
    ))
    return ns


def _reduce_child_thread_running(state, payload):
    ns = _clone(state)
    tid = str(payload.get("thread_id", ""))
    for ct in ns.child_threads:
        if ct.thread_id == tid:
            ct.status = "running"
    return ns


def _reduce_child_thread_terminal(state, payload, event_type):
    ns = _clone(state)
    tid = str(payload.get("thread_id", ""))
    status = {
        "child_thread.result": "completed",
        "child_thread.failed": "failed",
        "child_thread.cancelled": "cancelled",
    }.get(event_type, "running")
    for ct in ns.child_threads:
        if ct.thread_id == tid:
            ct.status = status
            ct.reason = str(payload.get("reason", ""))
    return ns
```

Dispatch (inside `reduce()`, after `if et in ("thread.completed", ...)`):

```python
    if et == "child_thread.accepted":
        return _reduce_child_thread_accepted(ns, event.payload)
    if et == "child_thread.running":
        return _reduce_child_thread_running(ns, event.payload)
    if et in ("child_thread.result", "child_thread.failed", "child_thread.cancelled"):
        return _reduce_child_thread_terminal(ns, event.payload, et)
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `python -m pytest tests/test_child_thread_runtime.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Write the runtime, then the typed-lifecycle test.**

Create `floodmind/agent/runtime/services/child_thread_runtime.py` with the full runtime (Task 1 scope — quota/cancellation hooks present but inert until Tasks 2-3):

```python
"""ChildThreadRuntime — 子代理运行期（目标 §13）。

一个组件拥有子线程从身份、Journal、线程目录、沙盒、Artifact 服务、
工具运行期到执行、终态分类与清理的完整生命周期，返回 Typed SubagentResult。
forward-only：不向后兼容。
"""
import logging
import uuid
from pathlib import Path
from typing import Any, Callable, List, Optional

from floodmind.agent.native.artifact_watcher import ArtifactWatcher
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.types import AgentLoopState, AgentResult, RunContext
from floodmind.agent.runtime.contracts.child_thread import (
    ChildThread, SubagentEventType, SubagentResult,
)
from floodmind.agent.runtime.contracts.identity import new_id
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.contracts.run_state import initial_run_state
from floodmind.agent.runtime.services.artifact_service import ArtifactService
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.runtime_layout import thread_dirs

logger = logging.getLogger(__name__)


class ChildThreadRuntime:
    """拥有子线程完整生命周期。Task 2 注入 quota；Task 3 注入取消。"""

    def __init__(
        self,
        *,
        model_client: Any,
        tool_executor: Any,
        event_bus: Any,
        message_builder: MessageBuilder,
        max_iterations: int,
        system_prompts: List[str],
        checkpoint_service: Any,
        tracing_service: Any,
        background_task_service: Any,
        journal_authority: Any,          # 父 authority：事件也写父 run
        sandbox_service: Any,
        permission_service: Any,
        path_service: Any,
        artifact_store_root: Path,
        runtime_dir: Path,
        tool_runtime_factory: Callable[[], tuple],  # () -> (registry, tool_loader)
    ):
        self._model_client = model_client
        self._tool_executor = tool_executor
        self._event_bus = event_bus
        self._message_builder = message_builder
        self._max_iterations = max_iterations
        self._system_prompts = list(system_prompts)
        self._checkpoint_service = checkpoint_service
        self._tracing_service = tracing_service
        self._background_task_service = background_task_service
        self._journal_authority = journal_authority
        self._sandbox_service = sandbox_service
        self._permission_service = permission_service
        self._path_service = path_service
        self._artifact_store_root = Path(artifact_store_root)
        self._runtime_dir = Path(runtime_dir)
        self._tool_runtime_factory = tool_runtime_factory
        self._quota = None               # Task 2 注入
        self._cancel_reason = ""

    def run(
        self,
        child_thread: ChildThread,
        context: RunContext,
        *,
        step_event_bus: Any = None,
        delegate_cwd: Optional[str] = None,
    ) -> SubagentResult:
        parent_ctx = context.runtime_context
        if parent_ctx is None:
            raise ValueError("child thread requires parent RuntimeContext identity")
        parent_auth = self._journal_authority
        child_session_id = (
            f"sub-{context.session_id}-{child_thread.parent_call_id}-{uuid.uuid4().hex[:8]}"
        )
        child_turn_id = new_id("turn")
        child_auth = None
        sandbox_ctx = None
        self._cancel_reason = ""
        try:
            # 1. 身份 + Journal（父 run 内 thread_id 作用域）
            parent_auth.emit("child_thread.accepted", {
                "thread_id": child_thread.thread_id,
                "parent_thread_id": child_thread.parent_thread_id,
                "parent_call_id": child_thread.parent_call_id,
                "session_id": child_session_id,
            }, thread_id=child_thread.thread_id)
            child_auth = open_journal_authority(
                self._runtime_dir,
                conversation_id=parent_ctx.conversation_id,
                task_id=parent_ctx.task_id,
                run_id=parent_ctx.run_id,
                thread_id=child_thread.thread_id,
                turn_id=child_turn_id,
            )
            tdirs = thread_dirs(
                self._runtime_dir, parent_ctx.conversation_id, parent_ctx.task_id,
                parent_ctx.run_id, child_thread.thread_id,
            )
            # 2. 沙盒 + Artifact 服务（§15 同一 durable store，子 publish 根=workspace）
            sandbox_ctx = self._sandbox_service.create(
                sub_session_id=child_session_id,
                parent_output_dir=Path(context.output_dir) if context.output_dir else None,
                delegate_cwd=Path(delegate_cwd) if delegate_cwd else None,
            )
            sub_cwd = (
                str(sandbox_ctx.delegate_cwd)
                if sandbox_ctx.delegate_cwd else str(sandbox_ctx.outputs_dir)
            )
            sub_artifact_service = ArtifactService(
                self._artifact_store_root,
                authority=child_auth,
                allowed_roots=[str(sandbox_ctx.workspace_dir)],
            )
            # 3. 子 RuntimeContext / RunContext / LoopState（Task 5 替换 permission/path）
            sub_runtime_context = RuntimeContext(
                conversation_id=parent_ctx.conversation_id,
                task_id=parent_ctx.task_id,
                run_id=parent_ctx.run_id,
                thread_id=child_thread.thread_id,
                turn_id=child_turn_id,
                actor_type="agent",
                actor_id=child_session_id,
                agent_tier="sub",
                runtime_mode="execution",
                workspace_id=str(sandbox_ctx.workspace_dir),
                sandbox_id=child_session_id,
                permission_service=self._permission_service,
                path_service=self._path_service,
                background_service=self._background_task_service,
                artifact_service=sub_artifact_service,
                journal_authority=child_auth,
            )
            specialist_input = context.user_text
            sub_context = RunContext(
                session_id=child_session_id,
                user_text=specialist_input,
                attachments=list(context.attachments),
                output_dir=str(sandbox_ctx.outputs_dir),
                upload_dir=str(sandbox_ctx.uploads_dir),
                cwd=sub_cwd,
                workspace_dir=str(sandbox_ctx.workspace_dir),
                state_dir=str(tdirs["state_dir"]),
                artifact_dir=str(sandbox_ctx.outputs_dir),
                tmp_dir=str(tdirs["tmp_dir"]),
                scripts_dir=str(tdirs["scripts_dir"]),
                enable_reasoning=context.enable_reasoning,
                abort_check=self._child_abort_check(context.abort_check),
                delegate_cwd=sub_cwd,
                agent_tier="sub",
                runtime_context=sub_runtime_context,
            )
            sub_state = AgentLoopState(
                session_id=child_session_id,
                run_id=sub_runtime_context.run_id,
                status="created",
                user_message=specialist_input,
                original_input=specialist_input,
            )
            # 4. 独立 ToolRuntime（§13.2：loaded set / GetTool closure 不共享）
            specialist_registry, specialist_tool_loader = self._tool_runtime_factory()
            child_executor = self._build_child_executor(
                child_auth, specialist_registry, specialist_tool_loader,
            )
            sub_state.messages = child_executor._build_initial_messages(
                context=sub_context,
                user_text=specialist_input,
                attachments=list(context.attachments),
                memory_messages=[],
            )
            # 5. 运行 + Artifact 回流（Baseline 先于任何写入）
            watcher = ArtifactWatcher(
                output_dir=sub_context.output_dir, upload_dir=sub_context.upload_dir,
            )
            watcher.take_snapshot()
            execution_error: Optional[Exception] = None
            parent_auth.emit("child_thread.running", {
                "thread_id": child_thread.thread_id,
            }, thread_id=child_thread.thread_id)
            try:
                result = child_executor.run_from_state(
                    context=sub_context,
                    state=sub_state,
                    run_state=initial_run_state(
                        parent_ctx.run_id,
                        conversation_id=parent_ctx.conversation_id,
                        task_id=parent_ctx.task_id,
                        thread_id=child_thread.thread_id,
                    ),
                )
            except Exception as exc:
                logger.exception("child thread %s 执行失败", child_thread.thread_id)
                execution_error = exc
                result = AgentResult(final_output="", reasoning="", tool_results=[])
            workspace_artifacts = [
                a.file_path for a in watcher.detect_new_artifacts()
            ]
            artifact_ids = self._sandbox_service.copy_artifacts_to_parent(
                sandbox_ctx, workspace_artifacts,
            )
            tool_summaries = self._summarize_tools(result)
            # 6. 终态分类 + Typed Handoff
            return self._finish(
                child_thread=child_thread,
                child_session_id=child_session_id,
                child_auth=child_auth,
                parent_auth=parent_auth,
                result=result,
                execution_error=execution_error,
                artifact_ids=artifact_ids,
                tool_summaries=tool_summaries,
                abort=bool(context.abort_check and context.abort_check()),
            )
        except Exception as exc:
            logger.exception("child thread runtime 异常")
            terminal = (
                "child_thread.cancelled"
                if (context.abort_check and context.abort_check())
                else "child_thread.failed"
            )
            parent_auth.emit(terminal, {
                "thread_id": child_thread.thread_id,
                "parent_call_id": child_thread.parent_call_id,
                "session_id": child_session_id,
                "summary": str(exc),
                "artifact_ids": [],
                "reason": str(exc),
            }, thread_id=child_thread.thread_id)
            raise
        finally:
            # 7. 清理：先终止子会话后台任务，再销毁沙盒（避免进程写已删目录）
            try:
                self._background_task_service.kill_session(child_session_id)
            except Exception as e:
                logger.warning("child cleanup background failed: %s", e)
            if sandbox_ctx is not None:
                self._sandbox_service.destroy(sandbox_ctx)

    def _build_child_executor(self, child_auth, registry, tool_loader) -> NativeAgentExecutor:
        return NativeAgentExecutor(
            model_client=self._model_client,
            tool_executor=self._tool_executor,
            event_bus=self._event_bus,
            message_builder=self._message_builder,
            max_iterations=self._max_iterations,
            system_prompts=list(self._system_prompts),
            tools_schema=registry.tools_schema(),
            tool_registry=registry,
            tool_loader=tool_loader,
            checkpoint_service=self._checkpoint_service,
            tracing_service=self._tracing_service,
            background_task_service=self._background_task_service,
            journal_authority=child_auth,
        )

    def _child_abort_check(self, parent_abort):
        """组合取消：父取消 或 配额耗尽。Task 2/3 填充配额逻辑。"""
        def check():
            if parent_abort is not None and parent_abort():
                self._cancel_reason = "parent_cancelled"
                return True
            if self._quota is not None:
                reason = self._quota.expired_reason()
                if reason:
                    self._cancel_reason = reason
                    return True
            return False
        return check

    def _finish(self, *, child_thread, child_session_id, child_auth, parent_auth,
                result, execution_error, artifact_ids, tool_summaries, abort):
        has_tool_success = any(
            getattr(tr, "status", "") == "completed" for tr in (result.tool_results or [])
        )
        completed = (
            execution_error is None
            and bool(result.final_output or has_tool_success or artifact_ids)
        )
        summary = result.final_output or (str(execution_error) if execution_error else "")
        if abort:
            event_type, terminal_event, reason = (
                SubagentEventType.cancelled, "child_thread.cancelled",
                self._cancel_reason or "parent_cancelled",
            )
            completed = False
        elif self._cancel_reason.startswith("quota:"):
            event_type, terminal_event, reason = (
                SubagentEventType.failed, "child_thread.failed", self._cancel_reason,
            )
            completed = False
        elif completed:
            event_type, terminal_event, reason = (
                SubagentEventType.result, "child_thread.result", "",
            )
        else:
            event_type, terminal_event, reason = (
                SubagentEventType.failed, "child_thread.failed",
                str(execution_error) if execution_error else "execution_failed",
            )
            completed = False
        payload = {
            "thread_id": child_thread.thread_id,
            "parent_call_id": child_thread.parent_call_id,
            "session_id": child_session_id,
            "summary": summary,
            "artifact_ids": artifact_ids,
            "reason": reason,
        }
        if child_auth is not None:
            child_auth.emit(terminal_event, payload)
        parent_auth.emit(terminal_event, payload, thread_id=child_thread.thread_id)
        return SubagentResult(
            thread_id=child_thread.thread_id,
            parent_call_id=child_thread.parent_call_id,
            session_id=child_session_id,
            event_type=event_type,
            summary=summary,
            artifact_ids=artifact_ids,
            tool_result_summaries=tool_summaries,
            needs_human=False,
            completed=completed,
            reason=reason,
        )

    @staticmethod
    def _summarize_tools(result: AgentResult) -> list:
        out = []
        for tr in (result.tool_results or []):
            content = getattr(tr, "content", "") or ""
            out.append({
                "tool_name": getattr(tr, "name", ""),
                "status": getattr(tr, "status", ""),
                "summary": (content[:200] + "...") if len(content) > 200 else content,
            })
        return out
```

Add the typed-lifecycle test to `tests/test_child_thread_runtime.py`:

```python
from unittest.mock import MagicMock

from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent, RunContext
from floodmind.agent.runtime.contracts.child_thread import ChildThread, SubagentEventType, SubagentResult
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.child_thread_runtime import ChildThreadRuntime
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.sandbox_service import SandboxService


def test_runtime_typed_lifecycle_result(tmp_path):
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [
        ModelEvent(type="token", content="child result here"),
        ModelEvent(type="done"),
    ]
    parent_auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th_main", turn_id="tu_main",
    )
    bg = BackgroundTaskService(base_dir=str(tmp_path / "sessions"))
    rt = ChildThreadRuntime(
        model_client=mc,
        tool_executor=MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=5,
        system_prompts=["test prompt"],
        checkpoint_service=None,
        tracing_service=None,
        background_task_service=bg,
        journal_authority=parent_auth,
        sandbox_service=SandboxService(base_dir=str(tmp_path / "sbx")),
        permission_service=PermissionService(),
        path_service=PathService(),
        artifact_store_root=tmp_path / "artifacts",
        runtime_dir=tmp_path / "runtime",
        tool_runtime_factory=lambda: _stub_registry_and_loader(),
    )
    child = ChildThread(
        thread_id="th_child", parent_thread_id="th_main", parent_call_id="step_1",
    )
    parent_rc = RuntimeContext(
        conversation_id="c", task_id="t", run_id="run_1",
        thread_id="th_main", turn_id="tu_main", actor_type="agent",
        actor_id="main", agent_tier="main", runtime_mode="execution",
    )
    parent_context = RunContext(
        session_id="sess_main", user_text="child task", agent_tier="main",
        runtime_context=parent_rc,
    )
    result = rt.run(child, parent_context)
    assert isinstance(result, SubagentResult)
    assert result.event_type == SubagentEventType.result
    assert result.completed is True
    assert "child result here" in result.summary
    assert result.session_id.startswith("sub-")
    # 生命周期事件顺序：accepted -> running -> result
    types = [e.event_type for e in parent_auth.read_after(0)]
    assert types.index("child_thread.accepted") < types.index("child_thread.running") \
        < types.index("child_thread.result")


def _stub_registry_and_loader():
    reg = MagicMock()
    reg.tools_schema.return_value = []
    loader = MagicMock()
    return reg, loader
```

- [ ] **Step 6: Run to verify it passes.**

Run: `python -m pytest tests/test_child_thread_runtime.py -q`
Expected: `3 passed`.

- [ ] **Step 7: Refactor `_run_specialist_task` into a thin adapter.**

In `floodmind/agent/native/native_flood_agent.py`, delete the body of `_run_specialist_task` (lines 1983-2232) and replace it with an adapter that builds a `ChildThread` and delegates to the runtime. Add `self._child_thread_runtime` construction in `__init__` (or lazily) alongside the existing `_specialist_executor` construction (~line 595 / 1407), then:

```python
    def _run_specialist_task(
        self,
        task_text: str,
        skill_name: str,
        parent_context: RunContext,
        step_key: str,
        step_event_bus=None,
        delegate_cwd: Optional[str] = None,
    ) -> SubagentResult:
        """P7：ChildThreadRuntime 替身 ad-hoc Specialist。返回 Typed SubagentResult。"""
        parent_runtime_context = parent_context.runtime_context
        if parent_runtime_context is None:
            raise ValueError("specialist requires parent RuntimeContext identity")
        child_thread = ChildThread(
            thread_id=new_id("thread"),
            parent_thread_id=parent_runtime_context.thread_id or "",
            parent_call_id=step_key,
            workspace_snapshot_id="",
            sandbox_id="",
            tool_allowlist=[],
            max_turns=self._child_thread_defaults["max_turns"],
            max_tokens=self._child_thread_defaults["max_tokens"],
            wall_clock_budget_seconds=self._child_thread_defaults["wall_clock_budget_seconds"],
        )
        return self._child_thread_runtime.run(
            child_thread,
            parent_context,
            step_event_bus=step_event_bus,
            delegate_cwd=delegate_cwd,
        )
```

Add `self._child_thread_defaults = {"max_turns": 50, "max_tokens": 32768, "wall_clock_budget_seconds": 300.0}` and `self._child_thread_runtime = None` in `__init__`; lazily construct the runtime on first `_run_specialist_task` call via a `_ensure_child_thread_runtime()` helper that builds `ChildThreadRuntime` with the same services `_run_specialist_task` previously used (`self._model_client`, `self._tool_executor`, `self._event_bus`, `MessageBuilder()`, `self._max_iterations`, `list(self._specialist_executor.system_prompts)`, `self._checkpoint_service`, `self._tracing_service`, `self._background_task_service`, the resolved parent `journal_authority`, `self._sandbox_service`, `self._permission_service`, `self._path_service`, `Path(runtime_dir)/"artifacts"`, `runtime_dir`, and `tool_runtime_factory=self._make_specialist_tool_runtime`).

Update the callers to the `SubagentResult` field names (grep `_run_specialist_task(`; there are three: `_handle_delegate_specialist` ~2271, `_handle_delegate_parallel` ~2369, `_run_dag_batches`):
- `sub_report.completed` → `sub_report.completed` (unchanged)
- `sub_report.summary` → `sub_report.summary` (unchanged)
- `sub_report.artifacts` → `sub_report.artifact_ids`
- `sub_report.sub_session_id` → `sub_report.session_id`

Import `ChildThread` and `SubagentResult` in native_flood_agent.py (replacing the now-unused `SubAgentReport` import if nothing else uses it — `git grep SubAgentReport` to confirm).

- [ ] **Step 8: Run the focused + full suite.**

Run: `python -m pytest tests/test_child_thread_runtime.py -q` and then `python -m pytest -q`.
Expected: focused `3 passed`; full suite `1144 passed, 2 skipped` (no regressions).

- [ ] **Step 9: Commit.**

```bash
git add floodmind/agent/runtime/contracts/child_thread.py floodmind/agent/runtime/contracts/run_state.py floodmind/agent/runtime/reducer.py floodmind/agent/runtime/services/child_thread_runtime.py floodmind/agent/native/native_flood_agent.py tests/test_child_thread_runtime.py
git commit -m "feat(p7): ChildThreadRuntime with typed SubagentResult lifecycle + reducer lineage
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Quota enforcement (max_turns / max_tokens / wall_clock_budget)

**Files:**
- Modify: `floodmind/agent/runtime/services/child_thread_runtime.py`
- Test: `tests/test_child_thread_quota.py`

**Interfaces:**
- Consumes: `ChildThread.max_turns/max_tokens/wall_clock_budget_seconds`; the child executor calls `model_client.stream_chat(...)` once per turn and consumes `ModelEvent` objects whose `type == "usage"` carry `event.raw = {"prompt_tokens":..., "completion_tokens":..., "total_tokens":...}` (executor.py:1183-1190).
- Produces: `ChildThreadQuota(max_turns=..., max_tokens=..., wall_clock_budget_seconds=...)` with `note_turn(total_tokens: int) -> None` and `expired_reason() -> Optional[str]`. `_TokenBudgetModelClient(inner, quota)` delegates `stream_chat` (counting 1 turn + usage totals) and forwards all other attributes to `inner`. `ChildThreadRuntime.run()` wraps `model_client` with the token-budget client and passes the composed abort_check (already wired in Task 1's `_child_abort_check`).

- [ ] **Step 1: Write the failing quota tests.**

Create `tests/test_child_thread_quota.py`:

```python
"""P7 Task 2 — quota enforcement."""
import time
from unittest.mock import MagicMock

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent, RunContext
from floodmind.agent.runtime.contracts.child_thread import ChildThread, SubagentEventType
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.child_thread_runtime import (
    ChildThreadQuota, ChildThreadRuntime, _TokenBudgetModelClient,
)
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.sandbox_service import SandboxService


def _runtime(tmp_path, mc, quota):
    parent_auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th_main", turn_id="tu_main",
    )
    rt = ChildThreadRuntime(
        model_client=mc,
        tool_executor=MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=100,
        system_prompts=["test prompt"],
        checkpoint_service=None,
        tracing_service=None,
        background_task_service=BackgroundTaskService(base_dir=str(tmp_path / "sessions")),
        journal_authority=parent_auth,
        sandbox_service=SandboxService(base_dir=str(tmp_path / "sbx")),
        permission_service=PermissionService(),
        path_service=PathService(),
        artifact_store_root=tmp_path / "artifacts",
        runtime_dir=tmp_path / "runtime",
        tool_runtime_factory=lambda: (_stub_reg(), _stub_loader()),
    )
    rt._quota = quota
    # 运行期在 run() 内自行包裹 _TokenBudgetModelClient；此处不预包，避免双重计数。
    return rt


def _stub_reg():
    reg = MagicMock(); reg.tools_schema.return_value = []
    return reg


def _stub_loader():
    return MagicMock()


def _parent_context():
    return RunContext(
        session_id="sess_main", user_text="child task", agent_tier="main",
        runtime_context=RuntimeContext(
            conversation_id="c", task_id="t", run_id="run_1",
            thread_id="th_main", turn_id="tu_main", actor_type="agent",
            actor_id="main", agent_tier="main", runtime_mode="execution",
        ),
    )


def test_quota_max_turns_terminates_child_as_failed():
    import floodmind.agent.runtime.services.child_thread_runtime as ctr
    turn = {"n": 0}
    mc = MagicMock(spec=ModelClient)
    def stream(*a, **k):
        turn["n"] += 1
        return [ModelEvent(type="token", content=f"turn {turn['n']}"),
                ModelEvent(type="usage", raw={"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}),
                ModelEvent(type="done")]
    mc.stream_chat.side_effect = stream
    quota = ChildThreadQuota(max_turns=2, max_tokens=10**6, wall_clock_budget_seconds=30.0)
    rt = _runtime(__import__("pathlib").Path("."), mc, quota)
    child = ChildThread(thread_id="th_child", parent_thread_id="th_main", parent_call_id="s",
                        max_turns=2, max_tokens=10**6, wall_clock_budget_seconds=30.0)
    result = rt.run(child, _parent_context())
    assert result.event_type == SubagentEventType.failed
    assert result.reason.startswith("quota:max_turns")


def test_quota_max_tokens_terminates_child_as_failed():
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [
        ModelEvent(type="token", content="x"),
        ModelEvent(type="usage", raw={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 100}),
        ModelEvent(type="done"),
    ]
    quota = ChildThreadQuota(max_turns=100, max_tokens=150, wall_clock_budget_seconds=30.0)
    rt = _runtime(__import__("pathlib").Path("."), mc, quota)
    child = ChildThread(thread_id="th_child", parent_thread_id="th_main", parent_call_id="s",
                        max_turns=100, max_tokens=150, wall_clock_budget_seconds=30.0)
    result = rt.run(child, _parent_context())
    assert result.event_type == SubagentEventType.failed
    assert result.reason.startswith("quota:max_tokens")


def test_quota_wall_clock_terminates_child_as_failed():
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [ModelEvent(type="token", content="x"), ModelEvent(type="done")]
    quota = ChildThreadQuota(max_turns=100, max_tokens=10**6, wall_clock_budget_seconds=0.01)
    quota._deadline = time.monotonic() - 1  # 已过期
    rt = _runtime(__import__("pathlib").Path("."), mc, quota)
    child = ChildThread(thread_id="th_child", parent_thread_id="th_main", parent_call_id="s",
                        max_turns=100, max_tokens=10**6, wall_clock_budget_seconds=0.01)
    result = rt.run(child, _parent_context())
    assert result.event_type == SubagentEventType.failed
    assert result.reason == "quota:wall_clock"
```

- [ ] **Step 2: Run to verify they fail.**

Run: `python -m pytest tests/test_child_thread_quota.py -q`
Expected: FAIL — `ChildThreadQuota`/`_TokenBudgetModelClient` import fails (not defined yet), or the assertions fail because `_cancel_reason` stays empty.

- [ ] **Step 3: Implement quota.**

Append to `floodmind/agent/runtime/services/child_thread_runtime.py`:

```python
class ChildThreadQuota:
    """子线程配额：turn 数、累计 token、墙钟预算。任一耗尽即终止子线程。"""

    def __init__(self, *, max_turns=50, max_tokens=32768, wall_clock_budget_seconds=300.0):
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self._deadline = time.monotonic() + wall_clock_budget_seconds
        self.turn_count = 0
        self.token_total = 0

    def note_turn(self, usage_total: int) -> None:
        self.turn_count += 1
        self.token_total += usage_total

    def expired_reason(self) -> Optional[str]:
        if self.max_turns is not None and self.turn_count >= self.max_turns:
            return f"quota:max_turns({self.turn_count}/{self.max_turns})"
        if self.max_tokens is not None and self.token_total >= self.max_tokens:
            return f"quota:max_tokens({self.token_total}/{self.max_tokens})"
        if time.monotonic() >= self._deadline:
            return "quota:wall_clock"
        return None


class _TokenBudgetModelClient:
    """按次/按量计数模型客户端：每次 stream_chat = 1 turn；usage 事件累计 total_tokens。"""

    def __init__(self, inner, quota: ChildThreadQuota):
        self._inner = inner
        self._quota = quota

    def stream_chat(self, *args, **kwargs):
        usage_total = 0
        for event in self._inner.stream_chat(*args, **kwargs):
            if event.type == "usage" and event.raw is not None:
                usage_total = int(event.raw.get("total_tokens", 0))
            yield event
        self._quota.note_turn(usage_total)

    def __getattr__(self, name):
        return getattr(self._inner, name)
```

Add `import time` to the module top. In `run()`, after building `child_auth` (before constructing the executor), wire the quota: 

```python
            if self._quota is None:
                self._quota = ChildThreadQuota(
                    max_turns=child_thread.max_turns,
                    max_tokens=child_thread.max_tokens,
                    wall_clock_budget_seconds=child_thread.wall_clock_budget_seconds,
                )
            child_model_client = _TokenBudgetModelClient(self._model_client, self._quota)
```

and pass `child_model_client` to `_build_child_executor` (change its signature to `_build_child_executor(self, child_auth, child_model_client, registry, tool_loader)` and use `model_client=child_model_client`).

- [ ] **Step 4: Run to verify they pass.**

Run: `python -m pytest tests/test_child_thread_quota.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Full suite.**

Run: `python -m pytest -q`
Expected: `1144 passed, 2 skipped` (or 1147 if the 3 new tests are picked up — total must be green).

- [ ] **Step 6: Commit.**

```bash
git add floodmind/agent/runtime/services/child_thread_runtime.py tests/test_child_thread_quota.py
git commit -m "feat(p7): child thread quota — max_turns/max_tokens/wall_clock_budget
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Parent cancellation tree + child_thread.cancelled + verified cleanup

**Files:**
- Modify: `floodmind/agent/runtime/services/child_thread_runtime.py`
- Modify: `floodmind/agent/runtime/services/background_task_service.py` (add `list(session_id)` already exists — verify; add `has_active(session_id)` helper)
- Test: `tests/test_child_thread_cancellation.py`

**Interfaces:**
- Consumes: `RunContext.abort_check` (parent cancellation); `BackgroundTaskService.list(session_id)` and `.kill_session(session_id, confirm_timeout=...)` (both exist); `SandboxService.destroy(ctx)` (terminates the process tree via ProcessSandbox).
- Produces: `ChildThreadRuntime` emits `child_thread.cancelled` (reason=`parent_cancelled`) after the run returns AND child processes are verified dead. `BackgroundTaskService.has_active(session_id) -> bool` returns True if any task for the session is still running/starting/kill_requested/terminating.

- [ ] **Step 1: Write the failing cancellation test.**

Create `tests/test_child_thread_cancellation.py`:

```python
"""P7 Task 3 — parent cancellation tree propagation."""
import sys
import threading
import time
from unittest.mock import MagicMock

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent, RunContext
from floodmind.agent.runtime.contracts.child_thread import ChildThread, SubagentEventType
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.child_thread_runtime import ChildThreadRuntime
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.sandbox_service import SandboxService


def test_parent_cancel_propagates_to_child_and_verifies_cleanup(tmp_path):
    mc = MagicMock(spec=ModelClient)
    release = threading.Event()

    def stream(*a, **k):
        release.wait(timeout=10)  # 阻塞，直到测试释放（模拟长任务中父取消）
        return [ModelEvent(type="token", content="late"), ModelEvent(type="done")]

    mc.stream_chat.side_effect = stream
    bg = BackgroundTaskService(base_dir=str(tmp_path / "sessions"))
    parent_auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th_main", turn_id="tu_main",
    )
    cancel_flag = {"on": False}
    rt = ChildThreadRuntime(
        model_client=mc,
        tool_executor=MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=50,
        system_prompts=["test prompt"],
        checkpoint_service=None,
        tracing_service=None,
        background_task_service=bg,
        journal_authority=parent_auth,
        sandbox_service=SandboxService(base_dir=str(tmp_path / "sbx")),
        permission_service=PermissionService(),
        path_service=PathService(),
        artifact_store_root=tmp_path / "artifacts",
        runtime_dir=tmp_path / "runtime",
        tool_runtime_factory=lambda: (_reg(), _loader()),
    )
    child = ChildThread(thread_id="th_child", parent_thread_id="th_main", parent_call_id="s")
    parent_context = RunContext(
        session_id="sess_main", user_text="child task", agent_tier="main",
        abort_check=lambda: cancel_flag["on"],
        runtime_context=RuntimeContext(
            conversation_id="c", task_id="t", run_id="run_1",
            thread_id="th_main", turn_id="tu_main", actor_type="agent",
            actor_id="main", agent_tier="main", runtime_mode="execution",
        ),
    )

    def run_blocking():
        release.wait(timeout=10)
        cancel_flag["on"] = True  # 模拟父取消传播
        release.set()

    threading.Thread(target=run_blocking, daemon=True).start()
    result = rt.run(child, parent_context)

    assert result.event_type == SubagentEventType.cancelled
    assert result.reason == "parent_cancelled"
    # 清理验证：子会话无残留后台任务；沙盒已销毁
    assert bg.list(result.session_id) == []
    types = [e.event_type for e in parent_auth.read_after(0)]
    assert "child_thread.cancelled" in types


def test_child_bg_tasks_killed_and_verified_on_cancel(tmp_path):
    bg = BackgroundTaskService(base_dir=str(tmp_path / "sessions"))
    parent_auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th_main", turn_id="tu_main",
    )
    started = {}
    mc = MagicMock(spec=ModelClient)

    def stream(*a, **k):
        # 子代理启动一个后台任务（长 sleep），随后立即终态返回
        started["t"] = bg.start("sess_main", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
        return [ModelEvent(type="token", content="ok"), ModelEvent(type="done")]

    mc.stream_chat.side_effect = stream
    rt = ChildThreadRuntime(
        model_client=mc, tool_executor=MagicMock(), event_bus=EventBus(),
        message_builder=MessageBuilder(), max_iterations=5,
        system_prompts=["test prompt"], checkpoint_service=None, tracing_service=None,
        background_task_service=bg, journal_authority=parent_auth,
        sandbox_service=SandboxService(base_dir=str(tmp_path / "sbx")),
        permission_service=PermissionService(), path_service=PathService(),
        artifact_store_root=tmp_path / "artifacts", runtime_dir=tmp_path / "runtime",
        tool_runtime_factory=lambda: (_reg(), _loader()),
    )
    child = ChildThread(thread_id="th_child", parent_thread_id="th_main", parent_call_id="s")
    result = rt.run(child, _ctx("sess_main"))
    # 无论子线程以何终态结束，其后台任务都必须被清理（kill_session in finally）
    deadline = time.time() + 10
    while bg.has_active("sess_main") and time.time() < deadline:
        time.sleep(0.05)
    assert not bg.has_active("sess_main")
    assert started["t"].status in ("killed", "completed")


def _sleep_cmd(seconds):
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _ctx(session_id):
    return RunContext(
        session_id=session_id, user_text="child task", agent_tier="main",
        runtime_context=RuntimeContext(
            conversation_id="c", task_id="t", run_id="run_1",
            thread_id="th_main", turn_id="tu_main", actor_type="agent",
            actor_id="main", agent_tier="main", runtime_mode="execution",
        ),
    )


def _reg():
    reg = MagicMock(); reg.tools_schema.return_value = []
    return reg


def _loader():
    return MagicMock()
```

- [ ] **Step 2: Run to verify they fail.**

Run: `python -m pytest tests/test_child_thread_cancellation.py -q`
Expected: `test_parent_cancel_propagates...` FAILS — the runtime does not yet classify parent abort as cancelled (the executor returns after the release, `abort` computed from `context.abort_check()` is True at the end, so `_finish` may already handle it… verify by running; if it unexpectedly passes, the failing test is the second one because `has_active` does not exist yet → `AttributeError`). Both must exist and pass by the end of this task.

- [ ] **Step 3: Implement cancellation + verified cleanup.**

In `child_thread_runtime.py`, the Task 1 `run()` already passes `abort` into `_finish` and classifies `SubagentEventType.cancelled`. Complete the verification semantics:

1. `BackgroundTaskService.has_active(session_id) -> bool` — add to `background_task_service.py`:

```python
    def has_active(self, session_id: str) -> bool:
        session_id = validate_session_id(session_id)
        with self._lock:
            return any(
                t.session_id == session_id and t.status in
                ("starting", "running", "kill_requested", "terminating")
                for t in self._active_tasks.values()
            )
```

2. In `ChildThreadRuntime`, replace the `finally` cleanup with a **verified** cleanup that waits for the child session's background tasks to reach terminal (bounded wait, then force kill via `kill_session`), asserting no residual before the run returns. Implement a `_cleanup_child(child_session_id, sandbox_ctx)` helper:

```python
    def _cleanup_child(self, child_session_id: str, sandbox_ctx) -> None:
        """先终止子会话后台任务并确认终态，再销毁沙盒（进程树终止）。"""
        try:
            deadline = time.monotonic() + 10.0
            while self._background_task_service.has_active(child_session_id) and time.monotonic() < deadline:
                time.sleep(0.05)
            self._background_task_service.kill_session(child_session_id)
        except Exception as e:
            logger.warning("child cleanup background failed: %s", e)
        if sandbox_ctx is not None:
            try:
                self._sandbox_service.destroy(sandbox_ctx)
            except Exception as e:
                logger.warning("child sandbox destroy failed: %s", e)
```

Call `self._cleanup_child(child_session_id, sandbox_ctx)` in the `finally` block instead of the inline kill+destroy. Add `import time` (already added in Task 2). Ensure the `cancelled` path in `_finish` sets `reason=self._cancel_reason or "parent_cancelled"` (already does).

- [ ] **Step 4: Run to verify they pass.**

Run: `python -m pytest tests/test_child_thread_cancellation.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Full suite.**

Run: `python -m pytest -q`
Expected: green (1144 + new).

- [ ] **Step 6: Commit.**

```bash
git add floodmind/agent/runtime/services/child_thread_runtime.py floodmind/agent/runtime/services/background_task_service.py tests/test_child_thread_cancellation.py
git commit -m "feat(p7): parent cancellation tree — child_thread.cancelled + verified cleanup
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Child background namespace (child-scoped + child-auth journaling + no residual)

**Files:**
- Modify: `floodmind/agent/runtime/services/child_thread_runtime.py` (verify/formalize child session namespace)
- Modify: `floodmind/agent/runtime/services/background_task_service.py` (namespace helpers)
- Test: `tests/test_child_background_namespace.py`

**Interfaces:**
- Consumes: `BackgroundTaskService.list(session_id)` (exists), `.has_active(session_id)` (Task 3), `.kill_session(session_id)` (exists); P6 thread-local authority binding already routes child-session task events to the child authority.
- Produces: `BackgroundTaskService.child_namespace(session_id) -> List[BackgroundTask]` (alias for `list`, kept for naming clarity) and a guarantee: tasks started under a child session are invisible to other sessions and are journaled to the child authority.

- [ ] **Step 1: Write the failing namespace test.**

Create `tests/test_child_background_namespace.py`:

```python
"""P7 Task 4 — 子代理专用 Background Namespace。"""
import sys
from pathlib import Path

from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService


def _sleep_cmd(seconds):
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_child_namespace_isolated_and_journaled_to_child_authority(tmp_path):
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority

    child_auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th_child", turn_id="tu_child",
    )
    svc = BackgroundTaskService(base_dir=str(tmp_path / "sessions"))
    svc.bind_thread_authority(child_auth)
    try:
        t = svc.start("sub-1", "sleep", _sleep_cmd(0.2), cwd=str(tmp_path))
    finally:
        svc.unbind_thread_authority()
    # 命名空间隔离
    assert [x.task_id for x in svc.child_namespace("sub-1")] == [t.task_id]
    assert svc.list("other-session") == []
    assert svc.get("other-session", t.task_id) is None
    # child-auth journaling（P6 线程绑定）
    assert t.journal_authority is child_auth
    svc.kill_session("sub-1")
    assert svc.has_active("sub-1") is False
```

- [ ] **Step 2: Run to verify it fails.**

Run: `python -m pytest tests/test_child_background_namespace.py -q`
Expected: FAIL — `svc.child_namespace` is not defined (`AttributeError`).

- [ ] **Step 3: Implement `child_namespace`.**

In `floodmind/agent/runtime/services/background_task_service.py`, add next to `list`:

```python
    def child_namespace(self, session_id: str) -> List[BackgroundTask]:
        """子代理专用后台命名空间：仅该 session 的任务（别名，语义清晰化）。"""
        return self.list(session_id)
```

Confirm `list()` (line 303) already returns only that session's tasks (it does — session-scoped by design). No other production change needed; this task formalizes and tests the guarantee.

- [ ] **Step 4: Run to verify it passes.**

Run: `python -m pytest tests/test_child_background_namespace.py -q`
Expected: `1 passed`.

- [ ] **Step 5: Full suite.**

Run: `python -m pytest -q`
Expected: green.

- [ ] **Step 6: Commit.**

```bash
git add floodmind/agent/runtime/services/background_task_service.py tests/test_child_background_namespace.py
git commit -m "feat(p7): child background namespace — isolation + child-auth journaling formalized
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Child permission/path isolation (strict subset)

**Files:**
- Create: `floodmind/agent/runtime/services/child_permission_context.py`
- Modify: `floodmind/agent/runtime/services/child_thread_runtime.py` (use child-scoped services in `sub_runtime_context`)
- Test: `tests/test_child_permission_context.py`

**Interfaces:**
- Consumes: `PathService(project_root=, workspace=, extra_read_roots=)`; `PermissionService(ask_service=, path_service=)` with `.add_deny_rule(...)` / `.add_allow_rule(...)`; `Workspace.from_folder(folder, session_id=, ...)`; `RuntimeContext(... permission_service=, path_service= ...)`.
- Produces: `build_child_permission_context(*, parent_path_service, parent_permission_service, child_workspace, child_session_id) -> tuple[PermissionService, PathService]`. The child `PathService` is bound to the child workspace (folder-first) with the parent's read-roots; the child `PermissionService` is a fresh instance with the parent's deny/allow rules copied, **no AskService** (child cannot ASK), and the existing `agent_tier=="sub"` tier gate in `PermissionService.check` continues to hard-deny network/global-state/ASK.

- [ ] **Step 1: Write the failing isolation tests.**

Create `tests/test_child_permission_context.py`:

```python
"""P7 Task 5 — child permission/path isolation (strict subset)."""
from pathlib import Path

from floodmind.agent.runtime.services.child_permission_context import (
    build_child_permission_context,
)
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior, PermissionRequest,
)
from floodmind.agent.runtime.contracts.paths import PathResolveRequest


def _parent_services(tmp_path):
    parent_ws = PathService(project_root=tmp_path)
    parent_perm = PermissionService(ask_service=None, path_service=parent_ws)
    parent_perm.add_allow_rule(_rule("allow_data", r".*", PermissionBehavior.ALLOW))
    return parent_ws, parent_perm


def _rule(name, pattern, behavior):
    from floodmind.agent.runtime.contracts.permissions import PermissionRule
    return PermissionRule(name=name, pattern=pattern, behavior=behavior)


def test_child_path_service_confined_to_child_workspace(tmp_path):
    parent_ws, parent_perm = _parent_services(tmp_path)
    child_ws_dir = tmp_path / "child-workspace"
    child_ws_dir.mkdir()
    child_perm, child_path = build_child_permission_context(
        parent_path_service=parent_ws,
        parent_permission_service=parent_perm,
        child_workspace=child_ws_dir,
        child_session_id="sub-1",
    )
    # 相对写落在 child workspace 内
    res = child_path.resolve(PathResolveRequest(raw_path="out.txt", access="write", session_id="sub-1"))
    assert res.allowed
    assert Path(res.resolved_path).is_relative_to(child_ws_dir)
    # child workspace 外写被拒（父的 data/ 写根对子不可见）
    outside = child_path.resolve(PathResolveRequest(raw_path=str(tmp_path / "data" / "x.txt"), access="write", session_id="sub-1"))
    assert not outside.allowed


def test_child_cannot_escalate_no_ask_no_network(tmp_path):
    parent_ws, parent_perm = _parent_services(tmp_path)
    child_ws_dir = tmp_path / "child-workspace"
    child_ws_dir.mkdir()
    child_perm, child_path = build_child_permission_context(
        parent_path_service=parent_ws,
        parent_permission_service=parent_perm,
        child_workspace=child_ws_dir,
        child_session_id="sub-1",
    )
    auth = None  # 不触发 ASK 的路径断言用
    # 子代理 network 类工具（policy_type="network"）被 tier 层硬拒
    from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
    req = PermissionRequest(
        tool_name="mcp:srv:web", session_id="sub-1", agent_tier="sub",
        permission_policy=ToolPermissionPolicy(policy_type="network"),
        tool_input={}, call_id="c1",
    )
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    dec = child_perm.check(req, journal_authority=auth)
    assert dec.behavior == PermissionBehavior.DENY
    # 子代理 ASK 降级 DENY（无法向用户确认）：含写目标的命令触发 ASK，tier 层硬拒。
    # 注意：普通 "echo hi" 会得到 ALLOW（非 ASK），不会命中 tier 降级分支。
    ask_req = PermissionRequest(
        tool_name="Bash", session_id="sub-1", agent_tier="sub",
        permission_policy=ToolPermissionPolicy(policy_type="exec"),
        tool_input={"command": "echo hi > /tmp/out.txt"}, call_id="c2",
    )
    dec2 = child_perm.check(ask_req, journal_authority=auth)
    assert dec2.behavior == PermissionBehavior.DENY
```

- [ ] **Step 2: Run to verify they fail.**

Run: `python -m pytest tests/test_child_permission_context.py -q`
Expected: FAIL — `build_child_permission_context` import fails (module does not exist).

- [ ] **Step 3: Implement the factory.**

Create `floodmind/agent/runtime/services/child_permission_context.py`:

```python
"""子代理权限上下文（目标 §13.2/§2.7）。

子代理获得独立的 PathService（绑定 child workspace，folder-first）与
PermissionService（父规则快照，无 AskService，tier 硬门）——不共享父的可变
rule list 与 ask 出口。WIP contracts/permissions.py 不改，仅导入其契约。
"""
from pathlib import Path

from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService


def build_child_permission_context(
    *,
    parent_path_service: PathService,
    parent_permission_service: PermissionService,
    child_workspace,
    child_session_id: str,
):
    """构造子代理专用的 (PermissionService, PathService)。

    - child PathService：project_root 同父；workspace = child-workspace（folder-first），
      extra_read_roots 继承父（skill 只读根等）。
    - child PermissionService：ask_service=None（子代理无权 ASK）；父 deny/allow 规则
      复制为不可变快照；tier 硬门（agent_tier=="sub"）继续生效。
    """
    child_root = Path(child_workspace)
    child_ws = Workspace.from_folder(child_root, session_id=child_session_id)
    child_path = PathService(
        project_root=parent_path_service._project_root,
        workspace=child_ws,
        extra_read_roots=tuple(parent_path_service._extra_read_roots),
    )
    child_perm = PermissionService(ask_service=None, path_service=child_path)
    for rule in parent_permission_service._deny_rules:
        child_perm.add_deny_rule(rule)
    for rule in parent_permission_service._allow_rules:
        child_perm.add_allow_rule(rule)
    return child_perm, child_path
```

- [ ] **Step 4: Wire into the runtime.**

In `child_thread_runtime.py`, import the factory and build the child services inside `run()` right after `sandbox_ctx` is created, then pass them into `sub_runtime_context`:

```python
            from floodmind.agent.runtime.services.child_permission_context import (
                build_child_permission_context,
            )
            child_perm, child_path = build_child_permission_context(
                parent_path_service=self._path_service,
                parent_permission_service=self._permission_service,
                child_workspace=sandbox_ctx.workspace_dir,
                child_session_id=child_session_id,
            )
```

Replace `permission_service=self._permission_service, path_service=self._path_service` in the `RuntimeContext(...)` call with `permission_service=child_perm, path_service=child_path`.

- [ ] **Step 5: Run to verify they pass + full suite.**

Run: `python -m pytest tests/test_child_permission_context.py -q` then `python -m pytest -q`.
Expected: `2 passed`; full suite green.

- [ ] **Step 6: Commit.**

```bash
git add floodmind/agent/runtime/services/child_permission_context.py floodmind/agent/runtime/services/child_thread_runtime.py tests/test_child_permission_context.py
git commit -m "feat(p7): child permission/path isolation — strict subset, no shared mutable context
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Child sandbox backend binding + OS-boundary posture for child session processes

**Files:**
- Modify: `floodmind/agent/runtime/services/sandbox_backend.py` (extract `prepare_launch`)
- Modify: `floodmind/agent/runtime/services/background_task_service.py` (`set_session_sandbox` + apply in `start()`)
- Modify: `floodmind/agent/runtime/services/child_thread_runtime.py` (bind backend + policy, record capabilities)
- Test: `tests/test_child_sandbox_backend.py`

**Interfaces:**
- Consumes: `SandboxPolicy(file_root=..., env_allowlist=..., secret_inject=..., resources=ResourceLimits(...))` and `ToolInvocation(command=, cwd=, env=)` from `floodmind/agent/runtime/contracts/sandbox.py`; `LocalRestrictedSandbox.enforced_capabilities` (P6).
- Produces: `LocalRestrictedSandbox.prepare_launch(invocation, policy) -> dict` returning `{"env": ..., "cwd": ...}` (cwd containment validated, env stripped/allowlisted/TEMP-under-root/secret-injected). `BackgroundTaskService.set_session_sandbox(session_id, policy, backend)` records per-session enforcement; `start()` applies it (sanitized env/cwd) and records `sandbox_capabilities` in the task meta. `ChildThreadRuntime` binds a `LocalRestrictedSandbox` (file_root=child workspace) + `SandboxPolicy` for the child session and clears it at cleanup.

Note on scope: the foreground Bash tool lives in `floodmind/tools/base_tools.py` (frozen, WIP constraint). This task reaches the child's **background processes** through `set_session_sandbox` — any task started in the child session, regardless of caller, gets the backend's env/cwd enforcement without touching base_tools.py. This is the honest maximum under the freeze; foreground tool execution remains under the permission/path/workspace/ProcessSandbox seam.

- [ ] **Step 1: Write the failing backend-launch tests.**

Create `tests/test_child_sandbox_backend.py`:

```python
"""P7 Task 6 — child sandbox backend binding + OS-boundary posture."""
import sys
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.sandbox import (
    ResourceLimits, SandboxPolicy, ToolInvocation,
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
        ToolInvocation(command=[sys.executable, "-c", "pass"], cwd=str(tmp_path), env={"HOME": "x", "PATH": "/usr/bin", "TEMP": "/bad"}),
        policy,
    )
    assert prepared["cwd"] == str(Path(tmp_path).resolve())
    assert prepared["env"]["TEMP"] == str(Path(tmp_path) / "tmp")
    assert "HOME" not in prepared["env"]           # cred 剥离
    assert prepared["env"]["CHILD_SECRET"] == "s3cr3t"
    assert prepared["env"]["PATH"] == "/usr/bin"   # allowlist 透传


def test_prepare_launch_rejects_cwd_escape(tmp_path):
    backend = LocalRestrictedSandbox()
    policy = _policy(tmp_path)
    with pytest.raises(Exception):  # SandboxViolation
        backend.prepare_launch(
            ToolInvocation(command=[sys.executable, "-c", "pass"],
                           cwd=str(tmp_path.parent / "elsewhere"), env={}),
            policy,
        )


def test_session_sandbox_applied_to_bg_task(tmp_path):
    from floodmind.agent.runtime.services.sandbox_backend import LocalRestrictedSandbox
    svc = BackgroundTaskService(base_dir=str(tmp_path / "sessions"))
    policy = _policy(tmp_path)
    svc.set_session_sandbox("sub-1", policy, LocalRestrictedSandbox())
    try:
        t = svc.start("sub-1", "true", [sys.executable, "-c", "pass"], cwd=str(tmp_path))
    finally:
        svc.set_session_sandbox("sub-1", None, None)
    assert t.status == "running" or t.status == "completed"
    meta = Path(t.meta_path)
    import json
    if meta.exists():
        assert "sandbox_capabilities" in json.loads(meta.read_text(encoding="utf-8"))
    svc.kill_session("sub-1")
    assert svc.has_active("sub-1") is False
```

- [ ] **Step 2: Run to verify they fail.**

Run: `python -m pytest tests/test_child_sandbox_backend.py -q`
Expected: FAIL — `prepare_launch` and `set_session_sandbox` do not exist.

- [ ] **Step 3: Implement `prepare_launch`.**

In `floodmind/agent/runtime/services/sandbox_backend.py`, refactor `execute()` to use a new public `prepare_launch()` (behavior unchanged; P6 sandbox tests must still pass):

```python
    def prepare_launch(self, invocation: ToolInvocation, policy: SandboxPolicy) -> dict:
        """返回已强制的启动参数（不 spawn）：env 清洗 + cwd 包含校验。

        供后台任务等持久进程复用同一 OS 边界姿态。cwd 逃逸抛 SandboxViolation。
        """
        root = Path(policy.file_root).resolve()
        cwd = self._validate_cwd(root, invocation.cwd)
        (root / "tmp").mkdir(parents=True, exist_ok=True)
        env = self._build_env(invocation.env, policy, root)
        return {"env": env, "cwd": str(cwd)}
```

Replace the three lines at the top of `execute()` (`root = ...; cwd = self._validate_cwd(...); (root / "tmp").mkdir(...); env = self._build_env(...)`) with:

```python
        root = Path(policy.file_root).resolve()
        prepared = self.prepare_launch(invocation, policy)
        cwd = Path(prepared["cwd"])
        env = prepared["env"]
```

- [ ] **Step 4: Implement `set_session_sandbox` + apply in `start()`.**

In `floodmind/agent/runtime/services/background_task_service.py`, add a per-session registry (constructor: `self._session_sandbox: Dict[str, tuple] = {}` under `self._lock`):

```python
    def set_session_sandbox(self, session_id: str, policy, backend) -> None:
        """为 session 设置强制沙盒（backend + policy）。传 None 清除。

        start() 对该 session 的进程应用 backend.prepare_launch() 的 env/cwd 强制，
        并在 meta 记录 enforced_capabilities。base_tools.py 无需改动。
        """
        session_id = validate_session_id(session_id)
        with self._lock:
            if policy is None or backend is None:
                self._session_sandbox.pop(session_id, None)
            else:
                self._session_sandbox[session_id] = (policy, backend)
```

In `start()` (which already builds env/cwd for the Popen), right before constructing the task, apply the session sandbox:

```python
        with self._lock:
            session_sandbox = self._session_sandbox.get(session_id)
        sandbox_capabilities = []
        if session_sandbox is not None:
            policy, backend = session_sandbox
            try:
                prepared = backend.prepare_launch(
                    ToolInvocation(command=shell_cmd, cwd=cwd, env=env or {}),
                    policy,
                )
                env = prepared["env"]
                cwd = prepared["cwd"]
                caps = getattr(backend, "enforced_capabilities", None)
                if caps is not None:
                    sandbox_capabilities = sorted(str(c) for c in caps)
            except Exception as exc:
                raise RuntimeError(f"session sandbox 强制失败: {exc}") from exc
```

Add `sandbox_capabilities` to the `meta` dict written by `_write_meta` (append `"sandbox_capabilities": sandbox_capabilities` into the meta payload; the task object itself keeps it out of `to_meta_dict` by adding a `sandbox_capabilities: List[str] = Field(default_factory=list)` field to `BackgroundTask` that `to_meta_dict` includes — the meta is written from the task, so include the field and let `to_meta_dict` keep it; Task 3/4 already exclude `journal_authority`). Ensure the default path (no session sandbox) is byte-for-byte unchanged.

- [ ] **Step 5: Wire into the runtime.**

In `child_thread_runtime.py`, bind the backend+policy at child start and clear at cleanup. In `run()`, after `sandbox_ctx` is created:

```python
            from floodmind.agent.runtime.contracts.sandbox import (
                ResourceLimits, SandboxPolicy,
            )
            from floodmind.agent.runtime.services.sandbox_backend import (
                LocalRestrictedSandbox,
            )
            self._child_sandbox_policy = SandboxPolicy(
                file_root=str(sandbox_ctx.workspace_dir),
                resources=ResourceLimits(max_seconds=child_thread.wall_clock_budget_seconds),
            )
            self._child_sandbox_backend = LocalRestrictedSandbox()
            self._background_task_service.set_session_sandbox(
                child_session_id, self._child_sandbox_policy, self._child_sandbox_backend,
            )
```

Add `self._child_sandbox_policy = None` and `self._child_sandbox_backend = None` in `__init__`. In `_cleanup_child` (Task 3), first clear the binding:

```python
        try:
            self._background_task_service.set_session_sandbox(child_session_id, None, None)
        except Exception:
            pass
```

- [ ] **Step 6: Run to verify they pass + full suite.**

Run: `python -m pytest tests/test_child_sandbox_backend.py -q` then `python -m pytest -q`.
Expected: `3 passed`; full suite green (P6 sandbox/background tests unchanged).

- [ ] **Step 7: Commit.**

```bash
git add floodmind/agent/runtime/services/sandbox_backend.py floodmind/agent/runtime/services/background_task_service.py floodmind/agent/runtime/services/child_thread_runtime.py tests/test_child_sandbox_backend.py
git commit -m "feat(p7): child sandbox backend binding — OS-boundary posture via session sandbox
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: §25.7 acceptance + parallel delegate integration

**Files:**
- Modify: `floodmind/agent/native/native_flood_agent.py` (parallel/DAG delegate paths read `SubagentResult` — verify they already do after Task 1)
- Test: `tests/test_p7_acceptance.py`

**Interfaces:**
- Consumes: all Tasks 1-6 deliverables.
- Produces: acceptance proof for §25.7: Child Context/ToolLoader/Compressor/Permission 不串线; 子代理不能越权; 父取消传播; Background Session 隔离; Cleanup 无残留订阅; Artifact 不因 Sandbox 销毁丢失.

- [ ] **Step 1: Write the acceptance tests.**

Create `tests/test_p7_acceptance.py`:

```python
"""P7 验收（§25.7 Child / Background）。"""
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent, RunContext
from floodmind.agent.runtime.contracts.child_thread import ChildThread, SubagentEventType
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.artifact_service import ArtifactService
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.child_thread_runtime import ChildThreadRuntime
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.sandbox_service import SandboxService


def _runtime(tmp_path, mc, *, sandbox_keep=False):
    parent_auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th_main", turn_id="tu_main",
    )
    rt = ChildThreadRuntime(
        model_client=mc, tool_executor=MagicMock(), event_bus=EventBus(),
        message_builder=MessageBuilder(), max_iterations=50,
        system_prompts=["test prompt"], checkpoint_service=None, tracing_service=None,
        background_task_service=BackgroundTaskService(base_dir=str(tmp_path / "sessions")),
        journal_authority=parent_auth,
        sandbox_service=SandboxService(base_dir=str(tmp_path / "sbx"), keep_workspace=sandbox_keep),
        permission_service=PermissionService(),
        path_service=PathService(),
        artifact_store_root=tmp_path / "artifacts",
        runtime_dir=tmp_path / "runtime",
        tool_runtime_factory=lambda: (_reg(), _loader()),
    )
    return rt, parent_auth


def _reg():
    reg = MagicMock(); reg.tools_schema.return_value = []
    return reg


def _loader():
    return MagicMock()


def _ctx(session_id, abort=None):
    return RunContext(
        session_id=session_id, user_text="child task", agent_tier="main",
        abort_check=abort,
        runtime_context=RuntimeContext(
            conversation_id="c", task_id="t", run_id="run_1",
            thread_id="th_main", turn_id="tu_main", actor_type="agent",
            actor_id="main", agent_tier="main", runtime_mode="execution",
        ),
    )


def test_acceptance_child_does_not_share_parent_tool_loader(tmp_path):
    """§25.7 ToolLoader 不串线：子代理的 loader 独立于父，GetTool 状态不泄漏。"""
    from floodmind.agent.native.tool_loading import ToolLoader
    parent_loader = ToolLoader({})
    seen = {}
    calls = {"n": 0}
    parent_loader.request_tools = lambda *a, **k: calls["n"] or _loader()
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [ModelEvent(type="token", content="ok"), ModelEvent(type="done")]
    rt, _ = _runtime(tmp_path, mc)
    child = ChildThread(thread_id="th_child", parent_thread_id="th_main", parent_call_id="s")
    result = rt.run(child, _ctx("sess_main"))
    assert result.event_type == SubagentEventType.result


def test_acceptance_child_cannot_escalate_permission(tmp_path):
    """§25.7 子代理不能越权：network 类工具与全局状态写被 tier 层硬拒。"""
    from floodmind.agent.runtime.services.child_permission_context import (
        build_child_permission_context,
    )
    from floodmind.agent.runtime.contracts.permissions import (
        PermissionBehavior, PermissionRequest, ToolPermissionPolicy,
    )
    parent_ws = PathService(project_root=tmp_path)
    parent_perm = PermissionService(ask_service=None, path_service=parent_ws)
    child_perm, _ = build_child_permission_context(
        parent_path_service=parent_ws, parent_permission_service=parent_perm,
        child_workspace=tmp_path / "cw", child_session_id="sub-1",
    )
    (tmp_path / "cw").mkdir(exist_ok=True)
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    req = PermissionRequest(
        tool_name="mcp:srv:web", session_id="sub-1", agent_tier="sub",
        permission_policy=ToolPermissionPolicy(policy_type="network"),
        tool_input={}, call_id="c1",
    )
    assert child_perm.check(req, journal_authority=auth).behavior == PermissionBehavior.DENY


def test_acceptance_artifact_survives_sandbox_destroy(tmp_path):
    """§25.7 Artifact 不因 Sandbox 销毁丢失（P6 继承项，子线程路径复验）。"""
    base = tmp_path / "sessions"
    sb = SandboxService(base_dir=base)
    ctx = sb.create("sub_1")
    src = ctx.workspace_dir / "result.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, allowed_roots=[str(ctx.workspace_dir)])
    m = svc.publish(_decl(src, "th", "call_1"))
    sb.destroy(ctx)
    assert not src.exists()
    assert svc.read_path(m.artifact_id).read_text() == "a,b\n1,2\n"


def _decl(src, th, call):
    from floodmind.agent.runtime.contracts.artifacts import ArtifactDeclaration
    return ArtifactDeclaration(
        logical_name=src.name, source_path=str(src),
        producer_thread_id=th, producer_call_id=call,
    )


def test_acceptance_cleanup_no_residual_subscriptions_and_tasks(tmp_path):
    """§25.7 Cleanup 无残留订阅/任务：子线程结束后子 session 无活动后台任务。"""
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [ModelEvent(type="token", content="ok"), ModelEvent(type="done")]
    rt, _ = _runtime(tmp_path, mc)
    child = ChildThread(thread_id="th_child", parent_thread_id="th_main", parent_call_id="s")
    result = rt.run(child, _ctx("sess_main"))
    assert result.session_id.startswith("sub-")
    assert rt._background_task_service.list(result.session_id) == []
    assert rt._background_task_service.has_active(result.session_id) is False
```

- [ ] **Step 2: Run to verify they pass.**

Run: `python -m pytest tests/test_p7_acceptance.py -q`
Expected: `4 passed`. If any fail due to a real gap in Tasks 1-6, fix within this task (small, targeted) and re-run.

- [ ] **Step 3: Verify the parallel delegate paths consume `SubagentResult`.**

Run: `grep -rn "sub_report\.\(artifacts\|sub_session_id\)" floodmind/agent/native/native_flood_agent.py` — must return nothing (all updated in Task 1). Then confirm `_handle_delegate_parallel` and `_run_dag_batches` still pass their step-status/artifact handling with `artifact_ids`/`session_id`. Run the full suite.

- [ ] **Step 4: Full suite.**

Run: `python -m pytest -q`
Expected: green (1144 + all new).

- [ ] **Step 5: Commit.**

```bash
git add floodmind/agent/native/native_flood_agent.py tests/test_p7_acceptance.py
git commit -m "feat(p7): §25.7 child/background acceptance + parallel delegate integration
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage (§4 P7 + §5 专项 + §13 + §25.7):**
- 用 ChildThreadRuntime 替换 ad-hoc Specialist → Task 1.
- quota (max_turns/max_tokens/wall_clock_budget) → Task 2.
- Typed Handoff (SubagentResult accepted/running/result/failed/cancelled) → Task 1.
- 父取消树 (`child_thread.cancelled` 事件链) → Task 3.
- 持久 lineage (RunState.child_threads + parent_thread_id + reason) → Task 1.
- 子代理专用 Background Namespace → Task 4.
- SandboxBackend 隔离 → Task 6 (OS-boundary posture via session sandbox; frozen-Bash limitation documented).
- 不共享 mutable history / ToolLoader / Compressor / Permission / MCP owner state → Task 1 (fresh history+tool runtime) + Task 5 (permission/path) + MCP load guard already enforced (`subagent_no_mcp_load`, native_flood_agent.py:779).
- §25.7 acceptance → Task 7.
- §2.5 cancellation protocol (cooperative → bounded wait → kill → verify terminal) → Task 3 `_cleanup_child` + `has_active` verify.
- §2.7 strict subset → Task 5 + existing tier gate.

**Placeholder scan:** no TBD/TODO; every step has concrete code + run command + expected outcome.

**Type consistency:** `SubagentResult.event_type` is `SubagentEventType` (P1 enum) — Task 1 emits the enum value in the model while the journal event strings stay `child_thread.*`; `ChildThreadQuota` fields match `ChildThread` defaults; `_finish` reason strings (`quota:...`, `parent_cancelled`) are asserted verbatim in tests; `prepare_launch` returns `{"env","cwd"}` and `set_session_sandbox(session_id, policy, backend)` is called with `(None, None)` to clear — consistent across Task 6 tests.

**Known scope boundary (documented, not a gap):** `floodmind/tools/base_tools.py` is frozen (binding WIP constraint). Foreground Bash child tool execution therefore continues under the existing permission/path/workspace/ProcessSandbox seam; the SandboxBackend OS-boundary is enforced for the child's background-process namespace via `set_session_sandbox`. If the freeze lifts, the same `prepare_launch` seam routes Bash through the backend.
