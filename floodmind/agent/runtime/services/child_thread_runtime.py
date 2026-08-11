"""ChildThreadRuntime — 子代理运行期（目标 §13）。

一个组件拥有子线程从身份、Journal、线程目录、沙盒、Artifact 服务、
工具运行期到执行、终态分类与清理的完整生命周期，返回 Typed SubagentResult。
forward-only：不向后兼容。
"""
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, List, Optional

from floodmind.agent.native.artifact_watcher import ArtifactWatcher
from floodmind.agent.native.event_bus import StepEventBus
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.types import AgentLoopState, AgentResult, RunContext
from floodmind.agent.runtime.contracts.child_thread import (
    ChildThread, SubagentEventType, SubagentResult,
)
from floodmind.agent.runtime.contracts.identity import new_id
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.reducer import initial_run_state
from floodmind.agent.runtime.services.artifact_service import ArtifactService
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.runtime_layout import thread_dirs

logger = logging.getLogger(__name__)


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
        quota_factory: Optional[Callable[[ChildThread], ChildThreadQuota]] = None,
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
        self._quota_factory = quota_factory
        self._child_sandbox_policy = None
        self._child_sandbox_backend = None

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
        base_bus = step_event_bus or self._event_bus
        if isinstance(base_bus, StepEventBus) and not getattr(base_bus, "_trace_session_id", ""):
            base_bus._trace_session_id = child_session_id
            child_event_bus = base_bus
        elif isinstance(base_bus, StepEventBus):
            child_event_bus = base_bus
        else:
            child_event_bus = StepEventBus(
                base_bus,
                child_thread.parent_call_id,
                trace_session_id=child_session_id,
            )
        child_turn_id = new_id("turn")
        child_auth = None
        sandbox_ctx = None
        run_state = {"cancel_reason": ""}
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
            from floodmind.agent.runtime.contracts.sandbox import (
                ResourceLimits,
                SandboxPolicy,
            )
            from floodmind.agent.runtime.services.sandbox_backend import (
                LocalRestrictedSandbox,
            )
            self._child_sandbox_policy = SandboxPolicy(
                file_root=str(sandbox_ctx.workspace_dir),
                resources=ResourceLimits(
                    max_seconds=child_thread.wall_clock_budget_seconds,
                ),
            )
            self._child_sandbox_backend = LocalRestrictedSandbox()
            self._background_task_service.set_session_sandbox(
                child_session_id,
                self._child_sandbox_policy,
                self._child_sandbox_backend,
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
            from floodmind.agent.runtime.services.child_permission_context import (
                build_child_permission_context,
            )
            child_perm, child_path = build_child_permission_context(
                parent_path_service=self._path_service,
                parent_permission_service=self._permission_service,
                child_workspace=sandbox_ctx.workspace_dir,
                child_session_id=child_session_id,
            )
            if self._quota_factory is not None:
                quota = self._quota_factory(child_thread)
            else:
                quota = ChildThreadQuota(
                    max_turns=child_thread.max_turns,
                    max_tokens=child_thread.max_tokens,
                    wall_clock_budget_seconds=child_thread.wall_clock_budget_seconds,
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
                permission_service=child_perm,
                path_service=child_path,
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
                abort_check=self._child_abort_check(
                    context.abort_check, run_state, quota,
                ),
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
            child_model_client = _TokenBudgetModelClient(self._model_client, quota)
            child_executor = self._build_child_executor(
                child_auth, child_model_client, specialist_registry,
                specialist_tool_loader, child_event_bus,
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
            # 执行器可能在产生最终文本后直接终止；在分类前再检查一次配额，
            # 确保最后一个完整 turn 的计数参与终态判定。
            if sub_context.abort_check is not None:
                sub_context.abort_check()
            # 6. 终态分类 + 已验证清理 + Typed Handoff
            terminal_event, payload, subagent_result = self._classify(
                child_thread=child_thread,
                child_session_id=child_session_id,
                result=result,
                execution_error=execution_error,
                artifact_ids=artifact_ids,
                tool_summaries=tool_summaries,
                abort=bool(context.abort_check and context.abort_check()),
                run_state=run_state,
            )
            verified = self._cleanup_child(child_session_id, sandbox_ctx)
            terminal_event, payload, subagent_result = self._finalize_classification(
                terminal_event, payload, subagent_result, verified=verified,
            )
            if child_auth is not None:
                child_auth.emit(terminal_event, payload)
            parent_auth.emit(
                terminal_event, payload, thread_id=child_thread.thread_id,
            )
            return subagent_result
        except Exception as exc:
            logger.exception("child thread runtime 异常")
            terminal = (
                "child_thread.cancelled"
                if (context.abort_check and context.abort_check())
                else "child_thread.failed"
            )
            payload = {
                "thread_id": child_thread.thread_id,
                "parent_call_id": child_thread.parent_call_id,
                "session_id": child_session_id,
                "summary": str(exc),
                "artifact_ids": [],
                "reason": str(exc),
            }
            verified = self._cleanup_child(child_session_id, sandbox_ctx)
            if terminal == "child_thread.cancelled" and not verified:
                terminal = "child_thread.failed"
                payload["reason"] = "cleanup_incomplete"
            parent_auth.emit(
                terminal, payload, thread_id=child_thread.thread_id,
            )
            raise
        finally:
            # 7. 清理：先确认子会话后台任务终态，再销毁沙盒。
            self._cleanup_child(child_session_id, sandbox_ctx)

    def _cleanup_child(self, child_session_id: str, sandbox_ctx: Any) -> bool:
        """Clean up child resources and report whether terminal state was verified."""
        try:
            self._background_task_service.set_session_sandbox(
                child_session_id, None, None,
            )
        except Exception:
            pass
        # §25.7 Cleanup 无残留订阅：子代理结束移除其 session 的全部订阅。
        try:
            self._background_task_service.clear_session_subscriptions(child_session_id)
        except Exception:
            pass
        background_verified = False
        try:
            deadline = time.monotonic() + 10.0
            while (
                self._background_task_service.has_active(child_session_id)
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            self._background_task_service.kill_session(child_session_id)
            background_verified = not self._background_task_service.has_active(
                child_session_id,
            )
        except Exception as exc:
            logger.warning("child cleanup background failed: %s", exc)

        sandbox_verified = sandbox_ctx is None
        if sandbox_ctx is not None:
            try:
                self._sandbox_service.destroy(sandbox_ctx)
                sandbox_verified = True
            except Exception as exc:
                logger.warning("child sandbox destroy failed: %s", exc)
        return background_verified and sandbox_verified

    def _build_child_executor(
        self, child_auth, child_model_client, registry, tool_loader, event_bus,
    ) -> NativeAgentExecutor:
        return NativeAgentExecutor(
            model_client=child_model_client,
            tool_executor=self._tool_executor,
            event_bus=event_bus,
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

    def _child_abort_check(self, parent_abort, run_state, quota):
        """组合取消：父取消 或 配额耗尽。Task 2/3 填充配额逻辑。"""
        def check():
            if parent_abort is not None and parent_abort():
                run_state["cancel_reason"] = "parent_cancelled"
                return True
            reason = quota.expired_reason()
            if reason:
                run_state["cancel_reason"] = reason
                return True
            return False
        return check

    def _classify(self, *, child_thread, child_session_id, result,
                  execution_error, artifact_ids, tool_summaries, abort,
                  run_state):
        """Classify a child terminal state without publishing journal events."""
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
                run_state["cancel_reason"] or "parent_cancelled",
            )
            completed = False
        elif run_state["cancel_reason"].startswith("quota:"):
            event_type, terminal_event, reason = (
                SubagentEventType.failed, "child_thread.failed", run_state["cancel_reason"],
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
        return terminal_event, payload, SubagentResult(
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
    def _finalize_classification(terminal_event, payload, result, *, verified):
        requires_verified_cleanup = (
            terminal_event == "child_thread.cancelled"
            or result.reason.startswith("quota:")
        )
        if verified or not requires_verified_cleanup:
            return terminal_event, payload, result
        failed_payload = dict(payload)
        failed_payload["reason"] = "cleanup_incomplete"
        failed_result = result.model_copy(update={
            "event_type": SubagentEventType.failed,
            "completed": False,
            "reason": "cleanup_incomplete",
        })
        return "child_thread.failed", failed_payload, failed_result

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
