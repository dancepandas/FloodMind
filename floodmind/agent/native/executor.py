"""
Native Agent Runtime - NativeAgentExecutor

自研工具调用循环，替代 LangChain AgentExecutor。
支持流式 token/reasoning/tool_call 输出、工具执行、产物检测、
状态机驱动、Checkpoint 持久化与恢复。
"""

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

from floodmind.agent.native.types import (
    AgentLoopState,
    AgentLoopStatus,
    AgentResult,
    ModelEvent,
    RunContext,
    TerminalReason,
    TokenUsage,
)
from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult
from floodmind.agent.runtime.contracts.run_state import RunState
from floodmind.agent.runtime.contracts.identity import new_id
from floodmind.agent.runtime.contracts.tool_transaction import (
    canonical_arguments,
    arguments_sha256,
)
from floodmind.agent.runtime.services.idempotency import (
    derive_idempotency_key,
    side_effect_class_for_spec,
)
from floodmind.agent.runtime.services.journal_authority import JournalAuthority
from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.retry import RetryPolicy, should_retry

from floodmind.agent.runtime.services.tracing_service import TracingService

# 上下文压缩（可选）
from floodmind.agent.native.context_compressor import (
    CompactionOverBudgetError,
    ContextCompressor,
)
from floodmind.agent.native.capabilities import ModelCapabilities, default_registry
from floodmind.agent.native.projection import compute_input_budget

logger = logging.getLogger(__name__)


_RUN_TO_LOOP_STATUS = {
    "created": "created",
    "projecting_context": "created",
    "awaiting_model": "awaiting_llm",
    "streaming_model": "awaiting_llm",
    "awaiting_tool": "awaiting_tool",
    "awaiting_approval": "awaiting_permission",
    "executing_tool": "awaiting_tool",
    "compacting": "context_compress",
    "paused": "paused",
    "cancelling": "failed",
    "cancelled": "failed",
    "completed": "completed",
    "failed": "failed",
}


def project_run_state_to_loop_state(
    loop_state: AgentLoopState,
    run_state: RunState,
) -> AgentLoopState:
    """Project authoritative reducer fields onto the mutable loop driver state."""
    projected = loop_state.model_copy(deep=True)
    projected.status = _RUN_TO_LOOP_STATUS[run_state.status.value]
    current_thread_id = run_state.current_thread_id
    scoped_turns = [
        turn for turn in run_state.turns
        if not current_thread_id or turn.get("thread_id", "") in ("", current_thread_id)
    ]
    projected.iteration = sum(
        1 for turn in scoped_turns if turn.get("role") == "assistant"
    )
    projected.journal_cursor = run_state.last_committed_sequence
    system_prefix: List[Dict[str, Any]] = []
    for message in loop_state.messages:
        if message.get("role") != "system":
            break
        system_prefix.append(dict(message))
    journal_messages: List[Dict[str, Any]] = []
    for turn in scoped_turns:
        role = turn.get("role")
        if role == "user":
            journal_messages.append({"role": "user", "content": turn.get("content", "")})
        elif role == "assistant":
            message: Dict[str, Any] = {
                "role": "assistant",
                "content": turn.get("content", ""),
            }
            if turn.get("tool_calls"):
                message["tool_calls"] = list(turn["tool_calls"])
            journal_messages.append(message)
        elif role == "tool":
            journal_messages.append({
                "role": "tool",
                "tool_call_id": turn.get("tool_call_id", ""),
                "content": turn.get("content", ""),
            })
    if journal_messages or not current_thread_id:
        projected.messages = system_prefix + journal_messages
    projected.pending_tool_calls = []
    projected.pending_ask_id = None
    projected.pending_tool_transaction_id = ""
    if run_state.pending_tool_transactions:
        projected.pending_tool_transaction_id = (
            run_state.pending_tool_transactions[-1].transaction_id
        )
    if run_state.pending_approvals:
        projected.pending_ask_id = run_state.pending_approvals[-1].ask_id
    return projected


class NativeAgentExecutor:
    """Native Agent 执行器。

    采用显式状态机驱动主循环：
        created → awaiting_llm → awaiting_tool → awaiting_llm → ... → completed/failed

    每个状态转移边界自动保存 checkpoint，支持崩溃恢复和 resume。
    """

    MAX_CONSECUTIVE_TOOL_FAILURES = 5
    DOOM_LOOP_THRESHOLD = 3  # 连续相同工具+相同参数次数阈值

    # 终止状态集合
    _TERMINAL_STATUSES = {"completed", "failed"}

    def __init__(
        self,
        model_client: ModelClient,
        tool_executor: Any,
        event_bus: EventBus,
        message_builder: Optional[MessageBuilder] = None,
        max_iterations: int = 10000,
        extra_body: Optional[dict] = None,
        system_prompt: str = "",
        system_prompts: Optional[List[str]] = None,
        tools_schema: Optional[List[dict]] = None,
        tool_registry: Optional[Any] = None,
        tool_loader: Optional[Any] = None,
        checkpoint_service: Optional[Any] = None,
        tracing_service: Optional[TracingService] = None,
        context_compressor: Optional[ContextCompressor] = None,
        context_window: int = 128000,
        memory: Optional[Any] = None,
        background_task_service: Optional[Any] = None,
        journal_authority: Optional[JournalAuthority] = None,
    ):
        self.model_client = model_client
        self.tool_executor = tool_executor
        self.event_bus = event_bus
        self.message_builder = message_builder or MessageBuilder()
        self.max_iterations = max_iterations
        self.extra_body = extra_body or {}
        if system_prompts is not None:
            self._system_prompts: List[str] = [p for p in system_prompts if p]
        elif system_prompt:
            self._system_prompts = [system_prompt]
        else:
            self._system_prompts = []
        self._tools_schema = tools_schema
        self._tool_registry = tool_registry
        self._tool_loader = tool_loader
        self._checkpoint_service = checkpoint_service
        self._tracing_service = tracing_service
        self._context_compressor = context_compressor
        self.context_window = context_window
        self._memory = memory
        self._journal_authority = journal_authority
        # 后台任务服务：任务完成通知注入（None 时回退全局单例 getter）
        self._background_task_service = background_task_service
        self._compressor_session_id: Optional[str] = None
        # §7.6 能力快照（一次解析，跨压缩/投影复用）；capability_snapshot_id 供 Manifest 引用
        self._capability_snapshot_id: str = ""
        self._capabilities: ModelCapabilities = self._resolve_capabilities()
        # 压缩失败抑制：同一轮（消息未变）压缩 fail-closed 后不再重触发，避免
        # awaiting_llm ↔ context_compress 忙循环（见 _on_awaiting_llm 的触发守卫）。
        self._compaction_failed: bool = False
        self._state_handlers: Dict[AgentLoopStatus, Callable[[AgentLoopState, RunContext], AgentLoopState]] = {
            "created": self._on_created,
            "awaiting_llm": self._on_awaiting_llm,
            "awaiting_tool": self._on_awaiting_tool,
            "awaiting_permission": self._on_awaiting_permission,
            "context_compress": self._on_context_compress,
            # completed / failed 是终止状态，不需要处理器
            # paused 已废弃：暂停 = abort → failed（见 run_from_state）
        }

    # --- 公共访问接口（保持向后兼容） ---
    @property
    def system_prompt(self) -> str:
        """所有 system prompts 的合并视图（向后兼容；仅用于日志/长度计算）。"""
        return "\n".join(self._system_prompts)

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        """向后兼容的 setter：覆盖为单条 prompt。"""
        self._system_prompts = [value] if value else []

    @property
    def system_prompts(self) -> List[str]:
        return self._system_prompts

    @system_prompts.setter
    def system_prompts(self, value: List[str]) -> None:
        self._system_prompts = [p for p in value if p]

    def set_tools_schema(self, schema: List[dict]) -> None:
        self._tools_schema = schema

    # --- 主入口 ---

    def run(
        self,
        context: RunContext,
        user_text: str,
        attachments: Optional[list] = None,
        memory_messages: Optional[List[dict]] = None,
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> AgentResult:
        """向后兼容的入口：从用户输入构建初始状态并运行。"""
        initial_state = self._build_initial_state(
            context=context,
            user_text=user_text,
            attachments=attachments,
            memory_messages=memory_messages,
            abort_check=abort_check,
        )
        return self.run_from_state(context, initial_state)

    def run_from_state(
        self,
        context: RunContext,
        state: AgentLoopState,
        run_state: Optional[RunState] = None,
    ) -> AgentResult:
        """从给定状态开始运行状态机。

        memory 是唯一历史源：每次 stream 从 memory 起步，无需 checkpoint resume。
        用户暂停 = abort（终态 failed，未完成轮丢弃不落 history）；无单独的 paused 软状态。
        """
        # Checkpoint snapshots are projections. Replay the canonical journal before
        # driving the loop so authoritative fields come from reducer state.
        if run_state is not None:
            state = project_run_state_to_loop_state(state, run_state)
        elif self._journal_authority is not None:
            replayed_state = self._journal_authority.replay()
            if replayed_state.last_committed_sequence:
                state = project_run_state_to_loop_state(state, replayed_state)

        # 每次 run 干净的压缩失败抑制状态（executor 可跨 run/session 复用）
        self._compaction_failed = False

        # C2: 把后台任务生命周期事件接入 Canonical Journal（§12）
        background_service = getattr(self, "_background_task_service", None)
        if self._journal_authority is not None and background_service is not None:
            try:
                thread_id = getattr(state, "thread_id", "") or getattr(
                    getattr(context, "runtime_context", None), "thread_id", ""
                ) or self._journal_authority.thread_id
                background_service.set_event_sink(
                    lambda event_type, payload: self._journal_authority.emit(
                        event_type, payload, thread_id=thread_id,
                    )
                )
            except Exception as exc:
                logger.warning("[EXEC] wire background event sink failed: %s", exc)

        # 用户中断检查回调
        effective_abort = context.abort_check

        while True:
            # 跑到终态（completed / failed）
            while state.status not in self._TERMINAL_STATUSES:
                # 用户中断检查（终态）：暂停即中止，丢弃当前未完成轮
                if effective_abort and effective_abort():
                    logger.info("NativeAgentExecutor aborted at status=%s iteration=%d", state.status, state.iteration)
                    state.final_output = state.final_output or "任务已被用户中断。"
                    state.status = "failed"
                    self._save_checkpoint(state, context)
                    break

                handler = self._state_handlers.get(state.status)
                if handler is None:
                    logger.error("NativeAgentExecutor: 未知状态 %s", state.status)
                    state.status = "failed"
                    self._save_checkpoint(state, context)
                    break

                logger.info("[EXEC] status=%s iteration=%d messages=%d", state.status, state.iteration, len(state.messages))
                if self._tracing_service is not None:
                    self._tracing_service.record_event(
                        context.session_id,
                        "state_transition",
                        f"enter_{state.status}",
                        input={"iteration": state.iteration, "checkpoint_id": state.checkpoint_id or ""},
                    )
                state = handler(state, context)
                state.mark_updated()
                self._save_checkpoint(state, context)

            # 终态后兜底：若运行中追加了排队指令且非用户主动中断，继续处理（避免排队消息在
            # failed/DOOM 等非 completed 终态下静默丢失）。每轮至少消费一条排队指令，有限轮收敛。
            if effective_abort and effective_abort():
                break  # 用户暂停：排队指令留给下一次发送，不在此接续
            if self._memory is None or not hasattr(self._memory, "get_user_messages"):
                break
            if self._inject_queued_user_messages(state) == 0:
                break
            logger.info("[EXEC] terminal revived: queued message(s) pending, continuing (iter=%d)", state.iteration)
            state.final_output = ""
            state.status = "awaiting_llm"

        if self._tracing_service is not None:
            self._tracing_service.flush(context.session_id)

        if self._journal_authority is not None:
            terminal_reason = (
                state.terminal_reason.code
                if state.terminal_reason is not None
                else state.status
            )
            if state.status == "completed":
                self._journal_authority.emit(
                    "run.completed",
                    {
                        "final_output": state.final_output,
                        "terminal_reason": terminal_reason,
                    },
                )
            else:
                self._journal_authority.emit(
                    "run.failed",
                    {
                        "error": state.final_output,
                        "terminal_reason": terminal_reason,
                    },
                )

        return self._build_result(state)

    # --- 状态处理器 ---

    def _on_created(self, state: AgentLoopState, context: RunContext) -> AgentLoopState:
        """初始状态：system prompt + memory + user message 已构建完成，进入 LLM 调用。"""
        state.status = "awaiting_llm"
        return state

    def _on_context_compress(self, state: AgentLoopState, context: RunContext) -> AgentLoopState:
        """上下文压缩：当消息长度达到窗口阈值时，对中间历史进行摘要压缩。

        压缩后保留头部（system/初始需求）和尾部（最近几轮），中间部分替换为摘要。
        """
        if not state.messages:
            state.status = "awaiting_llm"
            return state

        if self._context_compressor is None:
            logger.warning("[EXEC] context_compress 状态但无 ContextCompressor，跳过压缩")
            state.status = "awaiting_llm"
            return state

        # 会话切换时重置 compressor 内部摘要状态，防止跨会话污染
        if self._compressor_session_id != state.session_id:
            self._context_compressor.reset()
            self._compressor_session_id = state.session_id

        before_messages = len(state.messages)
        reason = "context_window_threshold"
        if self._journal_authority is not None:
            self._journal_authority.emit(
                "context.compaction.started",
                {"reason": reason, "before_messages": before_messages},
            )
        try:
            caps = getattr(self, "_capabilities", None)
            if caps is None:
                caps = self._resolve_capabilities()
                self._capabilities = caps
            # §9.3 有效输入预算：从能力快照计算，压缩不得越过该预算
            budget = compute_input_budget(caps)
            result = self._context_compressor.compress_journal(
                state.messages,
                self._journal_authority,
                capabilities=caps,
                budget=budget,
                max_context_tokens=budget.effective_input or self.context_window,
            )
            if result.saved_tokens > 0:
                state.messages = result.compressed_messages
                self._compaction_failed = False
                self.event_bus.emit({
                    "type": "context_compress",
                    "summary": result.summary,
                    "saved_tokens": result.saved_tokens,
                    "original_messages": len(result.original_messages),
                    "compressed_messages": len(result.compressed_messages),
                })
                logger.info(
                    "[EXEC] context compressed: %d -> %d messages, saved ~%d tokens",
                    len(result.original_messages),
                    len(result.compressed_messages),
                    result.saved_tokens,
                )
            else:
                logger.info("[EXEC] context_compress triggered but no compression performed")
            # Summary Event（§9.5 CompactSummary）已由 compress_journal 经 authority
            # 落 journal——只 append context.compaction.completed，不改原始事件。
        except CompactionOverBudgetError as e:
            # F4 fail-closed：不静默截断当前用户请求，也不返回超预算投影。
            # 保持原始 messages 继续运行，交由宿主/检索缩减（P6）处理。
            # 同一轮消息未变，重试压缩必再失败 → 抑制再触发，避免忙循环。
            self._compaction_failed = True
            logger.error("[EXEC] context compression over input budget, keeping original messages: %s", e)
            self.event_bus.emit_error(f"上下文压缩失败（超输入预算）: {str(e)[:200]}")
        except Exception as e:
            # 与上同理：任何压缩失败都保持原始 messages，必须抑制再触发。
            self._compaction_failed = True
            logger.error("[EXEC] context compression failed: %s", e)
            self.event_bus.emit_error(f"上下文压缩失败: {str(e)[:200]}")

        state.status = "awaiting_llm"
        return state

    def _on_awaiting_llm(self, state: AgentLoopState, context: RunContext) -> AgentLoopState:
        """调用 LLM stream，消费事件，得到 tool_calls 或 final_answer。"""
        if state.iteration >= self.max_iterations:
            logger.warning("NativeAgentExecutor reached max_iterations=%d", self.max_iterations)
            state.final_output = state.final_output or self._fallback_final_output(state)
            state.status = "completed"
            return state

        # 检测运行中追加的排队指令：若 memory 中出现了尚未并入 state.messages 的用户新消息，
        # 在本次 LLM 调用前注入（= 排队到下一次 LLM 调用）。
        self._inject_queued_user_messages(state)
        # 后台任务完成通知注入（loop 活跃时；已终态的留给宿主唤醒路径）
        self._inject_background_notifications(state)

        # 主动上下文压缩：达到阈值时先进入 context_compress 状态。
        # 压缩已 fail-closed（同一轮消息未变，重试必再失败）→ 跳过，直接走 LLM 调用，
        # 避免 awaiting_llm ↔ context_compress 忙循环（不消耗 iteration，唯一出口是外部中断）。
        if (
            not self._compaction_failed
            and self._context_compressor is not None
            and self._context_compressor.should_compress(state.messages, self.context_window)
        ):
            logger.info(
                "[EXEC] context ratio over threshold, entering context_compress (messages=%d, window=%d)",
                len(state.messages),
                self.context_window,
            )
            state.status = "context_compress"
            return state

        if self._tool_loader is not None:
            tools_param = self._tool_loader.request_tools(
                self._tool_registry,
                fallback_schema=self._tools_schema,
            )
        else:
            tools_param = self._tools_schema if self._tools_schema else None
        state.current_answer = ""
        state.round_assistant_message = None
        tool_calls: List[ToolCall] = []
        invalid_tool_calls: List[Any] = []
        step_tokens = TokenUsage()
        state.terminal_reason = None
        # 记录本轮开始前 reasoning 长度，重试时截断本轮残片，避免两段拼接
        reasoning_before = len(state.reasoning)

        state.attempt_id = new_id("attempt")
        if self._journal_authority is not None:
            self._journal_authority.emit(
                "model.attempt.started",
                {
                    "model": getattr(self.model_client, "model_name", ""),
                    "iteration": state.iteration,
                    "messages_count": len(state.messages),
                },
                attempt_id=state.attempt_id,
            )
        # §9.2 每次模型调用前落投影 Manifest（回答「模型看到了什么」）
        self._emit_projection_committed(state)
        self.event_bus.emit_llm_step_start(
            model_name=getattr(self.model_client, 'model_name', ''),
            iteration=state.iteration,
        )

        # LLM 流消费（带自动重试）
        retry_policy = RetryPolicy(max_retries=3, base_delay=2.0, max_delay=30.0)
        attempt = 0
        while True:
            attempt_output_events: List[tuple[str, str]] = []
            try:
                for event in self.model_client.stream_chat(
                    messages=state.messages,
                    tools=tools_param,
                    extra_body=self.extra_body or None,
                    abort_check=context.abort_check,
                ):
                    self._consume_llm_event(
                        event, state, tool_calls, step_tokens, invalid_tool_calls,
                        emit_output=False,
                    )
                    if event.type in {"reasoning", "token"}:
                        attempt_output_events.append((event.type, event.content))
                for event_type, content in attempt_output_events:
                    if event_type == "reasoning":
                        self.event_bus.emit_reasoning(content)
                    else:
                        self.event_bus.emit_token(content)
                break  # stream completed successfully

            except Exception as e:
                # §7.7 Orchestrator 决策：Transport 只给 Retry Advice（model_client 门面），
                # 是否重试由 should_retry 结合终态判定。
                advice = self.model_client.classify_error(e)
                terminal_reason = state.terminal_reason
                if attempt >= retry_policy.max_retries or not should_retry(advice, terminal_reason):
                    self.event_bus.emit_error(str(e)[:500])
                    self.event_bus.emit_llm_step_end(reason="error")
                    state.final_output = f"模型调用失败: {str(e)[:300]}"
                    state.status = "failed"
                    return state
                attempt += 1
                delay = retry_policy.delay_for(attempt)
                logger.warning(
                    "[EXEC] LLM stream error, retrying in %.1fs (%d/%d): %s",
                    delay, attempt, retry_policy.max_retries, str(e)[:200],
                )
                self.event_bus.emit({
                    "type": "retry_attempt",
                    "attempt": attempt,
                    "error": str(e)[:200],
                    "delay": delay,
                })
                # 清空本轮已收集的内容，重试重新生成
                state.current_answer = ""
                state.reasoning = state.reasoning[:reasoning_before]
                tool_calls = []
                invalid_tool_calls = []
                state.terminal_reason = None
                step_tokens = TokenUsage()
                time.sleep(delay)

        # 中断（用户暂停）在 LLM 流式阶段生效：ModelClient 收到 abort 信号后会干净返回，
        # 这里显式拦截，丢弃本轮半截产物，**不写 memory**，直接终态 failed。
        if context.abort_check and context.abort_check():
            logger.info("[EXEC] aborted during LLM stream, discarding partial round (iter=%d)", state.iteration)
            self.event_bus.emit_llm_step_end(reason="aborted")
            state.final_output = state.final_output or "任务已被用户中断。"
            state.status = "failed"
            return state

        # 本轮 reasoning 切片（跨轮 state.reasoning 累加，按本轮起点切片），写 memory 用
        state.round_reasoning = state.reasoning[reasoning_before:]

        terminal = state.terminal_reason or TerminalReason.from_raw(
            "tool_calls" if tool_calls or invalid_tool_calls else "stop"
        )
        self.event_bus.emit_llm_step_end(
            reason=terminal.raw or terminal.code,
            tokens={
                "prompt_tokens": step_tokens.prompt_tokens,
                "completion_tokens": step_tokens.completion_tokens,
                "total_tokens": step_tokens.total_tokens,
            },
        )

        # 更新 token 用量
        state.token_usage.prompt_tokens += step_tokens.prompt_tokens
        state.token_usage.completion_tokens += step_tokens.completion_tokens
        state.token_usage.total_tokens += step_tokens.total_tokens

        if invalid_tool_calls:
            # Keep the provider assistant snapshot (with the original malformed call) and
            # return a structured tool error. Crucially, no ToolCall reaches execution.
            assistant_message = getattr(state, "round_assistant_message", None)
            if assistant_message:
                state.messages.append(assistant_message)
            for invalid in invalid_tool_calls:
                feedback = (
                    f"工具 `{invalid.name}` 的参数 JSON 无法解析，工具未执行。"
                    f"请修正后重试。错误: {invalid.error}"
                )
                state.messages.append(
                    self.message_builder.build_tool_result_message(invalid.id, feedback)
                )
                self.event_bus.emit_tool_result(
                    tool_name=invalid.name,
                    status="error",
                    content=feedback,
                    tool_input=invalid.raw_arguments,
                    call_id=invalid.id,
                )
            state.iteration += 1
            state.status = "awaiting_llm"
            return state

        if terminal.code == "max_tokens":
            if state.max_token_continuation_count < state.max_token_continuations:
                state.max_token_continuation_count += 1
                partial = state.current_answer
                if partial:
                    state.final_output += partial
                    state.messages.append(
                        self.message_builder.build_assistant_tool_calls_message([], partial)
                    )
                state.messages.append(self.message_builder.build_user_message(
                    "上一条回复因达到输出长度限制而中断。请直接从中断处继续，不要重复已有内容。"
                ))
                state.current_answer = ""
                state.status = "awaiting_llm"
                return state
            state.final_output = (
                state.final_output + state.current_answer
                or "模型输出达到长度限制，未能完整完成。"
            )
            state.status = "failed"
            return state

        if terminal.code in {"filtered", "refused", "paused", "aborted", "error", "unknown"}:
            labels = {
                "filtered": "模型输出被内容过滤器截断。",
                "refused": "模型拒绝了该请求。",
                "paused": "模型暂停了当前回合，未正常完成。",
                "aborted": "模型调用已中止。",
                "error": "模型回合异常结束。",
                "unknown": f"模型以未知原因结束: {terminal.raw or 'empty'}",
            }
            state.final_output = labels[terminal.code]
            state.status = "failed"
            return state

        if not tool_calls:
            self._emit_round_events(
                state, tool_calls_records=[], is_final=True, attempt_id=state.attempt_id
            )
            state.final_output += state.current_answer
            # 终态后再检查一次排队指令：运行中若有追加的新指令，继续处理而非结束
            if self._inject_queued_user_messages(state) > 0:
                logger.info("[EXEC] terminal round deferred: %d queued message(s) pending", state.iteration)
                state.final_output = ""
                state.status = "awaiting_llm"
                return state
            state.status = "completed"
            return state

        # 记录本轮模型给出的完整 assistant message，并追加到 API 历史。
        # MiniMax 等厂商要求多轮 Function Call 原样回传 reasoning_details / <think> content。
        assistant_message = getattr(state, "round_assistant_message", None)
        if not assistant_message:
            assistant_message = self.message_builder.build_assistant_tool_calls_message(
                tool_calls,
                state.current_answer,
            )
        state.messages.append(assistant_message)
        state.pending_tool_calls = tool_calls
        state.status = "awaiting_tool"
        return state

    def _on_awaiting_tool(self, state: AgentLoopState, context: RunContext) -> AgentLoopState:
        """顺序执行 pending_tool_calls，检测 DOOM LOOP / 连续失败。"""
        tool_calls = state.pending_tool_calls
        completed_ask_calls = list(getattr(state, "_pending_completed_ask_calls", []) or [])
        state._pending_completed_ask_calls = []

        # 本轮工具记录（写 memory 用：tool_name/input/output/status）
        round_tool_records: List[Dict[str, Any]] = list(
            getattr(state, "_pending_round_tool_records", []) or []
        )
        state._pending_round_tool_records = []

        for idx, call in enumerate(tool_calls):
            # 中断（用户暂停）在工具阶段生效：执行到可中断点终止，本轮整轮丢弃不落 history
            if context.abort_check and context.abort_check():
                logger.info("[EXEC] aborted during tool execution, discarding round (iter=%d)", state.iteration)
                state.final_output = state.final_output or "任务已被用户中断。"
                state.status = "failed"
                return state

            tool_input_str = json.dumps(call.arguments, ensure_ascii=False) if call.arguments else ""

            # DOOM LOOP 检测
            input_sig = self._build_input_signature(call)
            if self._is_doom_loop(state, call.name, input_sig):
                doom_msg = (
                    f"工具 {call.name} 已连续 {self.DOOM_LOOP_THRESHOLD} 次"
                    f"使用相同参数调用，疑似死循环，强制终止。"
                )
                logger.warning("[EXEC] DOOM LOOP: %s, sig=%s", doom_msg, input_sig[:200])
                self._emit_tool_error(call, doom_msg, state)
                state.final_output = doom_msg
                state.status = "failed"
                return state

            transaction_id = new_id("transaction")
            # §6.1/§6.5：先建立事务（tool.call.proposed，含幂等键）。tool.execution.started
            # 不再由 executor 提前发出 —— 它由 ToolExecutionService 在真正副作用前发出，
            # 保证校验/权限评估发生在事务标记 running 之前（§6.6 生命周期时序）。
            identity = self._tool_transaction_identity(call)
            canon = identity["canonical_arguments_str"]
            side_effect_class = identity["side_effect_class"]
            idempotency_key = identity["idempotency_key"]
            if self._journal_authority is not None:
                self._journal_authority.emit(
                    "tool.call.proposed",
                    {
                        "transaction_id": transaction_id,
                        "call_id": call.id,
                        "tool_id": call.name,
                        "tool_version": "1",
                        "canonical_arguments": canon,
                        "arguments_sha256": identity["arguments_sha256"],
                        "side_effect_class": side_effect_class,
                        "idempotency_key": idempotency_key,
                        "preconditions": [],
                    },
                    call_id=call.id,
                )
            self.event_bus.emit_tool_status(call.name, "running", tool_input=tool_input_str, call_id=call.id)
            logger.info("[EXEC] executing tool: name=%s, call_id=%s, input_len=%d", call.name, call.id, len(tool_input_str))

            # 执行工具。progressive 模式下 fail-closed：未加载工具不能绕过目录直接执行。
            # 阶段E：将 state.mode 注入 context（_resolve_mode 优先读 context.mode）
            context.mode = getattr(state, "mode", "execution")
            if self._tool_loader is not None and not self._tool_loader.is_executable(call.name):
                result = ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=(
                        f"工具 `{call.name}` 当前未加载，不能执行。"
                        "工具目录已列出全部可用工具；请调用 GetTool(tool_name=工具名) 查看参数并加载该工具。"
                    ),
                    status="error",
                )
            else:
                result = self.tool_executor.execute(
                    call,
                    context,
                    registry=self._tool_registry,
                    journal_authority=self._journal_authority,
                    transaction_id=transaction_id,
                    idempotency_key=idempotency_key,
                    side_effect_class=side_effect_class,
                    canonical_arguments_str=canon,
                    arguments_sha256=identity["arguments_sha256"],
                )
            logger.info("[EXEC] tool done: name=%s, status=%s, result_len=%d", call.name, result.status, len(result.content) if result.content else 0)

            state.tool_results.append(result)
            state.doom_loop_tracker.append((call.name, input_sig))
            # 防止长时间运行导致 tracker 无限增长
            tracker_limit = max(self.DOOM_LOOP_THRESHOLD * 10, 100)
            state.doom_loop_tracker = state.doom_loop_tracker[-tracker_limit:]

            # 处理 awaiting_permission：保存当前未完成的工具调用，暂停执行
            if result.status == "awaiting_permission":
                state.pending_tool_transaction_id = transaction_id
                state.pending_tool_calls = tool_calls[idx:]  # 包含当前这个
                state.pending_ask_id = result.metadata.get("ask_id")
                state.status = "awaiting_permission"
                return state

            # 连续失败检测
            if result.status == "error" or (result.content and "错误" in result.content[:50]):
                state.consecutive_failures[call.name] = state.consecutive_failures.get(call.name, 0) + 1
            else:
                state.consecutive_failures[call.name] = 0

            if state.consecutive_failures.get(call.name, 0) >= self.MAX_CONSECUTIVE_TOOL_FAILURES:
                self._emit_tool_execution_result(transaction_id, call, result, idempotency_key=idempotency_key)
                failure_msg = (
                    f"工具 {call.name} 已连续失败 {self.MAX_CONSECUTIVE_TOOL_FAILURES} 次，"
                    f"强制终止执行循环。请检查参数是否正确。"
                )
                logger.warning("[EXEC] %s", failure_msg)
                self._emit_tool_error(call, failure_msg, state)
                state.final_output = failure_msg
                state.status = "failed"
                return state

            inline_content = result.content
            self._emit_tool_execution_result(transaction_id, call, result, idempotency_key=idempotency_key)

            # 记录本轮工具调用/结果（写 memory 用）
            round_tool_records.append({
                "tool_name": call.name,
                "tool_input": tool_input_str,
                "tool_output": inline_content or "",
                "status": result.status,
            })

            self.event_bus.emit_tool_result(
                tool_name=call.name,
                status=result.status,
                content=inline_content,
                tool_input=tool_input_str,
                call_id=call.id,
            )

            if result.artifacts:
                state.artifacts.extend(result.artifacts)

            state.messages.append(
                self.message_builder.build_tool_result_message(call.id, inline_content)
            )

        self._emit_round_events(
            state,
            tool_calls_records=round_tool_records,
            is_final=False,
            attempt_id=state.attempt_id,
        )

        # 本轮工具全部执行完毕，进入下一轮 LLM
        state.pending_tool_calls = []
        self._auto_advance_plan(state)
        state.iteration += 1
        state.status = "awaiting_llm"
        return state

    # --- canonical round events + queued-message projection ---

    def _emit_round_events(
        self,
        state: AgentLoopState,
        *,
        tool_calls_records: List[Dict[str, Any]],
        is_final: bool,
        attempt_id: str,
    ) -> None:
        if self._journal_authority is None:
            return
        terminal_reason = (
            state.terminal_reason.code
            if state.terminal_reason is not None
            else ("tool_calls" if tool_calls_records else "completed")
        )
        self._journal_authority.emit(
            "model.attempt.completed",
            {
                "attempt_id": attempt_id,
                "terminal_reason": terminal_reason,
                "content": state.current_answer or "",
                "reasoning": state.round_reasoning or "",
                "tool_calls": tool_calls_records,
                "is_final": bool(is_final),
                "usage": state.token_usage.model_dump(),
            },
            attempt_id=attempt_id,
        )

    def _inject_background_notifications(self, state: AgentLoopState) -> int:
        """把本会话已完成的后台任务作为 user 消息注入，使下一次 LLM 调用感知其完成。

        与排队用户消息同通道（user 角色 + 方括号前缀，厂商兼容性最好——system 角色
        插在会话中间有的厂商不认）。只在 loop 活跃（awaiting_llm）时调用；loop 已
        终态的由宿主经 subscribe/EventBus 唤醒路径自行决定开新回合。
        """
        svc = self._background_task_service
        if svc is None:
            return 0
        try:
            tasks = svc.drain_completions(state.session_id)
        except Exception as e:
            logger.warning("[EXEC] drain background completions failed: %s", e)
            return 0
        injected = 0
        for task in tasks:
            if task.status == "killed":
                outcome = "被终止"
            elif task.exit_code == 0:
                outcome = "完成"
            else:
                outcome = "失败"
            tail = (task.tail or "").strip()
            text = (
                f"[后台任务{outcome}] {task.command!r} exit={task.exit_code}\n"
                + (f"输出尾部:\n{tail}\n" if tail else "")
                + f"完整输出: {task.stdout_path}"
            )
            state.messages.append(self.message_builder.build_user_message(text))
            logger.info("[EXEC] injected background completion: task=%s status=%s", task.task_id, task.status)
            injected += 1
        return injected

    def _inject_queued_user_messages(self, state: AgentLoopState) -> int:
        """检测运行中追加的排队指令并注入 state.messages。返回本次注入的条数。

        memory 是唯一历史源：用户在 agent 运行中发送的新指令会 append 到 memory。
        本方法在每次 LLM 调用前，把尚未并入 state.messages 的新用户消息追加到末尾，
        使下一次 LLM 调用带上新指令（排队语义）。
        """
        if self._memory is None or not hasattr(self._memory, "get_user_messages"):
            return 0
        try:
            all_users = self._memory.get_user_messages()
            # 首次调用：当前 memory 中的用户消息均已体现在 state.messages
            # （初始 user message + 历史摘要文本），标记为已消费，避免重复注入。
            if state.consumed_user_message_count == 0:
                state.consumed_user_message_count = len(all_users)
                return 0
            new_msgs = all_users[state.consumed_user_message_count:]
            injected = 0
            for m in new_msgs:
                if m:
                    state.messages.append(self.message_builder.build_user_message(m))
                    logger.info("[EXEC] injected queued user message: %s", m[:60])
                    injected += 1
            state.consumed_user_message_count = len(all_users)
            return injected
        except Exception as e:
            logger.warning("[EXEC] inject queued user messages failed: %s", e)
            return 0

    def _round_artifacts_diff(self, state: AgentLoopState) -> List[str]:
        """返回本轮新增的 artifact（文件）路径列表。"""
        before = getattr(state, "_round_artifacts_before", None)
        if before is None:
            return list(state.artifacts)
        before_set = set(before)
        return [a for a in state.artifacts if a not in before_set]

    def _auto_advance_plan(self, state: AgentLoopState) -> None:
        """乐观自动推进：本轮每产出一个新文件，推进一个 pending 步骤。

        多个产物会推进多个 pending 步骤（1:1），避免一轮多文件只推进 1 步的漏推进。
        委派路径(SubAgent)的精确推进已把对应步骤标为 running/completed，
        next_pending_step() 会跳过它们，因此不会重复推进。
        agent 不认可时可调用 update_plan 回退。
        """
        plan = getattr(state, "plan", None)
        if plan is None or not getattr(plan, "steps", None):
            return
        round_artifacts = self._round_artifacts_diff(state)
        if not round_artifacts:
            return

        advanced = 0
        for artifact in round_artifacts:
            pending = plan.next_pending_step()
            if pending is None:
                break
            pending["status"] = "completed"
            existing = list(pending.get("output_artifacts", []) or [])
            if artifact not in existing:
                existing.append(artifact)
            pending["output_artifacts"] = existing
            self.event_bus.emit_workflow_step(
                step_key=pending.get("step_id", ""),
                status="completed",
                title=pending.get("title", ""),
                outcome=f"自动推进（产出文件 {os.path.basename(artifact) if isinstance(artifact, str) else artifact}）",
                subtasks=pending.get("subtasks", []),
            )
            advanced += 1

        if advanced:
            logger.info(
                "[EXEC] auto-advance: %d step(s) -> completed (%d new artifacts)",
                advanced,
                len(round_artifacts),
            )

    def _on_awaiting_permission(self, state: AgentLoopState, context: RunContext) -> AgentLoopState:
        """等待用户授权。"""
        if not state.pending_ask_id:
            # 没有 pending ask_id，说明状态异常，转失败
            logger.error("NativeAgentExecutor: awaiting_permission 状态缺少 pending_ask_id")
            state.final_output = state.final_output or "授权状态异常，无法继续执行。"
            state.status = "failed"
            return state

        # 检查用户是否已响应
        ask_service = self._get_ask_service()
        if ask_service is None:
            logger.error("NativeAgentExecutor: AskService 未初始化")
            state.final_output = "授权服务未初始化，无法继续执行。"
            state.status = "failed"
            return state

        approved = ask_service.get_response(state.pending_ask_id)
        if approved is None:
            # 宿主无响应：超过 AskService 配置超时后自动拒绝，避免无限轮询卡死
            # （web 宿主无人响应 / 前端无 permission 处理时，此前会永久 sleep 循环）。
            ask_timeout = getattr(ask_service, "get_timeout", lambda: 300.0)() or 300.0
            age = getattr(ask_service, "age", lambda _a: None)(state.pending_ask_id)
            if age is not None and age > ask_timeout:
                logger.warning(
                    "[EXEC] ASK %s 等待 %.0fs 超过超时 %.0fs，自动拒绝",
                    state.pending_ask_id, age, ask_timeout,
                )
                if hasattr(ask_service, "reject"):
                    ask_service.reject(state.pending_ask_id)
                approved = False  # 走拒绝分支
            else:
                # 仍未响应，让出 CPU 避免忙等；保持 awaiting_permission 状态由主循环重新进入
                logger.info("NativeAgentExecutor: awaiting_permission %s still pending", state.pending_ask_id)
                time.sleep(0.5)
                return state

        # 用户已响应
        if not approved:
            # 区分"真实的用户拒绝"与"ASK 记录丢失（如进程重启）"
            if ask_service.is_pending(state.pending_ask_id):
                # 用户拒绝，记录错误并进入下一轮 LLM（让模型决定）
                logger.info("NativeAgentExecutor: awaiting_permission %s denied", state.pending_ask_id)
                pending_call = state.pending_tool_calls[0] if state.pending_tool_calls else None
                if pending_call:
                    denial_msg = f"用户拒绝了工具 {pending_call.name} 的执行请求。"
                    self._emit_tool_error(pending_call, denial_msg, state)
                    transaction_id = getattr(state, "pending_tool_transaction_id", "")
                    if transaction_id:
                        denied_result = ToolResult(
                            tool_call_id=pending_call.id,
                            name=pending_call.name,
                            content=denial_msg,
                            status="error",
                        )
                        self._emit_tool_execution_result(
                            transaction_id,
                            pending_call,
                            denied_result,
                            idempotency_key=self._tool_idempotency_key(pending_call),
                        )
                state.pending_tool_calls = []
                state.pending_tool_transaction_id = ""
                state.pending_ask_id = None
                state.status = "awaiting_llm"
                return state

            # ASK 记录已丢失，尝试重新发起授权请求（崩溃恢复）
            logger.warning(
                "NativeAgentExecutor: awaiting_permission %s lost, reissuing ASK",
                state.pending_ask_id,
            )
            pending_call = state.pending_tool_calls[0] if state.pending_tool_calls else None
            if pending_call and hasattr(self.tool_executor, "execute"):
                context.mode = getattr(state, "mode", "execution")
                identity = self._tool_transaction_identity(pending_call)
                result = self.tool_executor.execute(
                    pending_call,
                    context,
                    registry=self._tool_registry,
                    journal_authority=self._journal_authority,
                    transaction_id=getattr(state, "pending_tool_transaction_id", "") or new_id("transaction"),
                    idempotency_key=identity["idempotency_key"],
                    side_effect_class=identity["side_effect_class"],
                    canonical_arguments_str=identity["canonical_arguments_str"],
                    arguments_sha256=identity["arguments_sha256"],
                )
                if result.status == "awaiting_permission":
                    state.pending_ask_id = result.metadata.get("ask_id")
                    return state
            state.final_output = state.final_output or "授权请求已丢失且无法重新发起，执行中止。"
            state.status = "failed"
            return state

        # 用户同意，继续执行当前 pending 的工具
        logger.info("NativeAgentExecutor: awaiting_permission %s approved", state.pending_ask_id)
        authorized_ask_id = state.pending_ask_id
        state.pending_ask_id = None

        # 重新执行当前 pending_tool_calls（传入已授权 ask_id 以跳过再次 ASK）
        # 这里我们不直接改 pending_tool_calls，而是让 awaiting_tool 处理
        # 但需要让 ToolExecutionService 知道这次调用已被授权
        # 通过临时把 ask_id 存入 context 或 call 的 metadata 传递
        # 简单做法：直接执行第一个 pending_call，传入 authorized_ask_id
        pending_calls = state.pending_tool_calls
        if not pending_calls:
            state.status = "awaiting_llm"
            return state

        first_call = pending_calls[0]
        context.mode = getattr(state, "mode", "execution")
        transaction_id = getattr(state, "pending_tool_transaction_id", "") or new_id("transaction")
        identity = self._tool_transaction_identity(first_call)
        result = self.tool_executor.execute(
            first_call,
            context,
            registry=self._tool_registry,
            authorized_ask_id=authorized_ask_id,
            journal_authority=self._journal_authority,
            transaction_id=transaction_id,
            idempotency_key=identity["idempotency_key"],
            side_effect_class=identity["side_effect_class"],
            canonical_arguments_str=identity["canonical_arguments_str"],
            arguments_sha256=identity["arguments_sha256"],
        )
        state.tool_results.append(result)
        tool_input_str = json.dumps(first_call.arguments, ensure_ascii=False) if first_call.arguments else ""
        inline_content = result.content
        self._emit_tool_execution_result(
            transaction_id,
            first_call,
            result,
            idempotency_key=identity["idempotency_key"],
        )
        state.pending_tool_transaction_id = ""
        state._pending_round_tool_records = [{
            "tool_name": first_call.name,
            "tool_input": tool_input_str,
            "tool_output": inline_content or "",
            "status": result.status,
        }]

        self.event_bus.emit_tool_result(
            tool_name=first_call.name,
            status=result.status,
            content=inline_content,
            tool_input=tool_input_str,
            call_id=first_call.id,
        )

        if result.artifacts:
            state.artifacts.extend(result.artifacts)

        state.messages.append(
            self.message_builder.build_tool_result_message(first_call.id, inline_content)
        )

        # 移除已执行的第一个 call，并保留它供统一的轮末 journal/memory 处理
        state._pending_completed_ask_calls = [first_call]
        state.pending_tool_calls = pending_calls[1:]
        # 继续执行剩余工具
        state.status = "awaiting_tool"
        return state

    # --- 辅助方法 ---

    def _consume_llm_event(
        self,
        event: ModelEvent,
        state: AgentLoopState,
        tool_calls_ref: List[ToolCall],
        step_tokens: TokenUsage,
        invalid_tool_calls_ref: Optional[List[Any]] = None,
        emit_output: bool = True,
    ) -> None:
        """消费单个 LLM 事件。"""
        if event.type == "reasoning":
            state.reasoning += event.content
            if emit_output:
                self.event_bus.emit_reasoning(event.content)
        elif event.type == "token":
            state.current_answer += event.content
            if emit_output:
                self.event_bus.emit_token(event.content)
        elif event.type == "tool_call_done":
            if event.tool_call is not None:
                tool_calls_ref.append(event.tool_call)
        elif event.type == "invalid_tool_call":
            if invalid_tool_calls_ref is not None and event.invalid_tool_call is not None:
                invalid_tool_calls_ref.append(event.invalid_tool_call)
        elif event.type == "assistant_message_done":
            payload = event.raw or {}
            message = payload.get("message") if isinstance(payload, dict) else None
            if isinstance(message, dict):
                state.round_assistant_message = message
        elif event.type == "error":
            self.event_bus.emit_error(event.content)
            self.event_bus.emit_llm_step_end(reason="error")
            raise RuntimeError(event.content)
        elif event.type == "timeout":
            self.event_bus.emit_error(event.content)
            self.event_bus.emit_llm_step_end(reason="timeout")
            raise TimeoutError(event.content)
        elif event.type == "done":
            if event.terminal_reason is not None:
                state.terminal_reason = event.terminal_reason
        elif event.type == "usage":
            try:
                payload = json.loads(event.content) if event.content else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            step_tokens.prompt_tokens = payload.get("prompt_tokens", 0)
            step_tokens.completion_tokens = payload.get("completion_tokens", 0)
            step_tokens.total_tokens = payload.get("total_tokens", 0)
            logger.info("[EXEC] usage event: prompt=%s, completion=%s, total=%s",
                        payload.get("prompt_tokens"), payload.get("completion_tokens"), payload.get("total_tokens"))
            self.event_bus.emit_token_usage(
                prompt_tokens=payload.get("prompt_tokens", 0),
                completion_tokens=payload.get("completion_tokens", 0),
                total_tokens=payload.get("total_tokens", 0),
            )

    def _is_doom_loop(self, state: AgentLoopState, tool_name: str, input_sig: str) -> bool:
        recent_same_tool = [(n, s) for n, s in state.doom_loop_tracker if n == tool_name]
        if len(recent_same_tool) >= self.DOOM_LOOP_THRESHOLD:
            last_n = recent_same_tool[-self.DOOM_LOOP_THRESHOLD:]
            return all(s == input_sig for _, s in last_n)
        return False

    def _tool_transaction_identity(self, call: ToolCall) -> dict:
        """派生一次调用的确定性事务身份字段（§6.5/§6.6）。

        与 ToolExecutionService 同源（同一 canonical 形态派生），供 tool.call.proposed
        与 execute() 中段生命周期事件（validated/permission/started）共用。
        """
        spec = self._tool_registry.get(call.name) if self._tool_registry is not None else None
        canon = canonical_arguments(call.arguments)
        side_effect_class = side_effect_class_for_spec(spec)
        idempotency_key = derive_idempotency_key(
            tool_id=call.name,
            canonical_arguments=canon,
            side_effect_class=side_effect_class,
        )
        return {
            "canonical_arguments_str": canon,
            "arguments_sha256": arguments_sha256(call.name, "1", canon),
            "side_effect_class": side_effect_class,
            "idempotency_key": idempotency_key,
        }

    def _tool_idempotency_key(self, call: ToolCall) -> str:
        """为单个 call 派生幂等键（与 tool.call.proposed 同源，§6.5）。"""
        return self._tool_transaction_identity(call)["idempotency_key"]

    def _emit_tool_execution_result(
        self,
        transaction_id: str,
        call: ToolCall,
        result: ToolResult,
        *,
        idempotency_key: str = "",
    ) -> None:
        if self._journal_authority is None:
            return
        # 结果不确定（超时等）：发 indeterminate，事务保留 pending 供 reconcile。
        if (result.metadata or {}).get("indeterminate"):
            self._journal_authority.emit(
                "tool.execution.indeterminate",
                {
                    "transaction_id": transaction_id,
                    "call_id": call.id,
                    "tool_id": call.name,
                    "reason": "timeout",
                    "idempotency_key": idempotency_key,
                },
                call_id=call.id,
            )
            return
        succeeded = result.status in {"completed", "succeeded", "success"}
        self._journal_authority.emit(
            "tool.execution.completed" if succeeded else "tool.execution.failed",
            {
                "transaction_id": transaction_id,
                "call_id": call.id,
                "tool_id": call.name,
                "status": "succeeded" if succeeded else result.status,
                "result_summary": result.content or "",
                "full_ref": str((result.metadata or {}).get("full_ref", "")),
                "artifacts": list(result.artifacts or []),
                "idempotency_key": idempotency_key,
            },
            call_id=call.id,
        )

    def _emit_tool_error(self, call: ToolCall, msg: str, state: AgentLoopState) -> None:
        """向事件总线和 messages 发送一个工具错误结果。"""
        tool_input_str = json.dumps(call.arguments, ensure_ascii=False) if call.arguments else ""
        self.event_bus.emit_tool_result(
            tool_name=call.name,
            status="error",
            content=msg,
            tool_input=tool_input_str,
            call_id=call.id,
        )
        state.messages.append(self.message_builder.build_tool_result_message(call.id, msg))

    def _save_checkpoint(
        self,
        state: AgentLoopState,
        context: RunContext,
    ) -> None:
        """保存 checkpoint，失败不阻塞执行。"""
        if self._checkpoint_service is None:
            return
        try:
            journal_cursor = (
                self._journal_authority.cursor()
                if self._journal_authority is not None
                else state.journal_cursor
            )
            identity_metadata = {}
            if self._journal_authority is not None:
                identity_metadata = {
                    "conversation_id": self._journal_authority.conversation_id,
                    "task_id": self._journal_authority.task_id,
                    "run_id": self._journal_authority.run_id,
                    "thread_id": self._journal_authority.thread_id,
                    "turn_id": self._journal_authority.turn_id,
                    "runtime_dir": str(self._journal_authority._writer._base_dir),
                }
            run_state = None
            if self._journal_authority is not None:
                run_state = self._journal_authority.replay()
            record = self._checkpoint_service.save(
                state,
                metadata={
                    "model_name": getattr(self.model_client, 'model_name', ''),
                    "status": state.status,
                    **identity_metadata,
                },
                journal_cursor=journal_cursor,
                reducer_version="1",
                run_state=run_state,
            )
            if self._journal_authority is not None:
                self._journal_authority.emit(
                    "checkpoint.created",
                    {
                        "checkpoint_id": record.checkpoint_id,
                        "cursor": self._journal_authority.cursor(),
                        "iteration": state.iteration,
                        "status": state.status,
                    },
                )
        except Exception as e:
            logger.error("NativeAgentExecutor: checkpoint save failed: %s", e)

    def _resolve_run_id(self, context: RunContext) -> str:
        """Resolve the canonical run id for a new run (identity §3.1).

        Priority order:
        1. context.runtime_context.run_id — the RuntimeContext carries the
           authoritative run identity (see _run_specialist_task child states).
        2. self._journal_authority.run_id — so the state agrees with the journal
           it writes to when an authority is injected.
        3. new_id("run") — canonical generator (run_-prefixed), never time-based.
        """
        runtime_context = getattr(context, "runtime_context", None)
        if runtime_context is not None:
            runtime_run_id = getattr(runtime_context, "run_id", "") or ""
            if isinstance(runtime_run_id, str) and runtime_run_id:
                return runtime_run_id
        if self._journal_authority is not None:
            authority_run_id = getattr(self._journal_authority, "run_id", "") or ""
            if isinstance(authority_run_id, str) and authority_run_id:
                return authority_run_id
        return new_id("run")

    def _build_initial_state(
        self,
        context: RunContext,
        user_text: str,
        attachments: Optional[list],
        memory_messages: Optional[List[dict]],
        abort_check: Optional[Callable[[], bool]],
    ) -> AgentLoopState:
        messages = self._build_initial_messages(context, user_text, attachments, memory_messages)
        return AgentLoopState(
            session_id=context.session_id,
            run_id=self._resolve_run_id(context),
            status="created",
            iteration=0,
            max_iterations=self.max_iterations,
            messages=messages,
            original_input=user_text,
            user_message=user_text,
        )

    def _build_initial_messages(
        self,
        context: RunContext,
        user_text: str,
        attachments: Optional[list],
        memory_messages: Optional[List[dict]],
    ) -> List[dict]:
        messages: List[dict] = []
        for sp in self._system_prompts:
            messages.append(self.message_builder.build_system_message(sp))
        if memory_messages:
            messages.extend(memory_messages)
        messages.append(self.message_builder.build_user_message(user_text, attachments))
        return messages

    @staticmethod
    def _build_input_signature(call: ToolCall) -> str:
        """构建工具调用参数签名（用于 DOOM LOOP 检测）。"""
        if call.arguments:
            return json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
        return "{}"

    def _fallback_final_output(self, state: AgentLoopState) -> str:
        last_tool_output = ""
        for tr in reversed(state.tool_results):
            if tr.status == "completed" and tr.content:
                last_tool_output = tr.content
                break
        return (
            last_tool_output
            or "Agent 达到最大迭代次数，请检查任务是否过于复杂或参数是否缺失。"
        )

    def _build_result(self, state: AgentLoopState) -> AgentResult:
        return AgentResult(
            final_output=state.final_output,
            reasoning=state.reasoning,
            tool_results=state.tool_results,
            artifacts=state.artifacts,
            is_timeout=state.status == "failed" and "超时" in state.final_output,
        )

    def _get_ask_service(self) -> Optional[Any]:
        """获取全局 AskService。避免循环导入。"""
        try:
            from floodmind.agent.runtime.services.ask_service import get_ask_service
            return get_ask_service()
        except Exception:
            return None

    # --- 能力快照 / 投影 Manifest（§7.6 / §9.2 / §9.3） ---

    @staticmethod
    def _derive_model_family(model_name: str) -> str:
        """从模型名推导 capability registry 的 family key（best-effort）。

        剥离聚合网关前缀（"MiniMax/xxx"）后取首个 "-" 分段：o4-mini -> o（openai
        o-family）、deepseek-chat -> deepseek、kimi-k2 -> kimi。
        """
        name = (model_name or "").strip().lower()
        if not name:
            return ""
        base = name.split("/")[-1]
        return base.split("-")[0]

    def _resolve_capabilities(self) -> ModelCapabilities:
        """§7.6 解析模型能力快照（一次解析，跨压缩/投影复用）。

        经 default_registry 分层覆盖（provider -> family -> exact）；解析为空时
        回退为仅含 self.context_window 的能力（保证 §9.3 预算可计算）。
        """
        provider = getattr(self.model_client, "provider", "") or ""
        model = getattr(self.model_client, "model_name", "") or ""
        if not isinstance(provider, str):
            provider = ""
        if not isinstance(model, str):
            model = ""
        family = self._derive_model_family(model)
        caps, _ = default_registry().resolve_capabilities(provider, family, model)
        if not caps.context_window:
            caps = ModelCapabilities(context_window=self.context_window)
        self._capability_snapshot_id = f"cap_{provider}:{family}:{model}"
        return caps

    def _emit_projection_committed(self, state: AgentLoopState) -> None:
        """§9.2 每次模型调用前把当前消息投影 Manifest 落 journal（context.projection.committed）。

        回答「模型看到了什么」：逐消息估算 token，source_type="episode"、
        transform="identity"。确定性且廉价；失败不阻塞主循环。
        """
        if self._journal_authority is None:
            return
        try:
            import hashlib

            from floodmind.agent.native.context_compressor import ContextCompressor as _CC
            from floodmind.agent.native.projection import build_manifest
            from floodmind.agent.runtime.contracts.canonical_events import canonical_json
            from floodmind.agent.runtime.contracts.projection import ProjectionSource

            caps = getattr(self, "_capabilities", None)
            if caps is None:
                caps = self._resolve_capabilities()
                self._capabilities = caps
            budget = compute_input_budget(caps)
            model = getattr(self.model_client, "model_name", "") or ""
            codec = getattr(getattr(self.model_client, "pipeline", None), "name", "") or ""
            sources = []
            for i, msg in enumerate(state.messages):
                tokens = _CC._estimate_tokens([msg])
                sources.append(ProjectionSource(
                    source_id=f"msg_{i}",
                    source_type="episode",
                    content_sha256=hashlib.sha256(canonical_json(msg).encode("utf-8")).hexdigest(),
                    original_tokens=tokens,
                    projected_tokens=tokens,
                    transform="identity",
                    priority=0,
                    selected=True,
                ))
            manifest = build_manifest(
                model=model,
                codec_version=codec,
                capability_snapshot_id=getattr(self, "_capability_snapshot_id", "") or "",
                budget=budget,
                sources=sources,
                model_call_id=getattr(state, "attempt_id", "") or "",
            )
            self._journal_authority.emit("context.projection.committed", manifest.model_dump())
        except Exception as e:
            logger.warning("[EXEC] projection manifest emission failed: %s", e)
