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
from floodmind.agent.runtime.reducer import initial_run_state
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
