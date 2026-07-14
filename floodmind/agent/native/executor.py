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
    TokenUsage,
)
from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult
from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.retry import is_retryable_error

from floodmind.agent.runtime.services.execution_journal_service import ExecutionJournalService
from floodmind.agent.runtime.services.tracing_service import TracingService

# 上下文压缩（可选）
from floodmind.agent.native.context_compressor import ContextCompressor

logger = logging.getLogger(__name__)


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
        checkpoint_service: Optional[Any] = None,
        execution_journal_service: Optional[ExecutionJournalService] = None,
        tracing_service: Optional[TracingService] = None,
        context_compressor: Optional[ContextCompressor] = None,
        context_window: int = 128000,
        memory: Optional[Any] = None,
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
        self._checkpoint_service = checkpoint_service
        self._journal_service = execution_journal_service
        self._tracing_service = tracing_service
        self._context_compressor = context_compressor
        self.context_window = context_window
        # 唯一历史源：每轮原子完成后写入 memory（add_assistant_round）；中断时不写。
        self._memory = memory
        self._compressor_session_id: Optional[str] = None
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
    ) -> AgentResult:
        """从给定状态开始运行状态机。

        memory 是唯一历史源：每次 stream 从 memory 起步，无需 checkpoint resume。
        用户暂停 = abort（终态 failed，未完成轮丢弃不落 history）；无单独的 paused 软状态。
        """
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

        try:
            result = self._context_compressor.compress(state.messages, max_context_tokens=self.context_window)
            if result.saved_tokens > 0:
                state.messages = result.compressed_messages
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
        except Exception as e:
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

        # 主动上下文压缩：达到阈值时先进入 context_compress 状态
        if (
            self._context_compressor is not None
            and self._context_compressor.should_compress(state.messages, self.context_window)
        ):
            logger.info(
                "[EXEC] context ratio over threshold, entering context_compress (messages=%d, window=%d)",
                len(state.messages),
                self.context_window,
            )
            state.status = "context_compress"
            return state

        tools_param = self._tools_schema if self._tools_schema else None
        state.current_answer = ""
        tool_calls: List[ToolCall] = []
        step_tokens = TokenUsage()
        # 记录本轮开始前 reasoning 长度，重试时截断本轮残片，避免两段拼接
        reasoning_before = len(state.reasoning)

        self.event_bus.emit_llm_step_start(
            model_name=getattr(self.model_client, 'model_name', ''),
            iteration=state.iteration,
        )

        # LLM 流消费（带自动重试）
        retry_remaining = 3
        while True:
            try:
                for event in self.model_client.stream_chat(
                    messages=state.messages,
                    tools=tools_param,
                    extra_body=self.extra_body or None,
                    abort_check=context.abort_check,
                ):
                    self._consume_llm_event(event, state, tool_calls, step_tokens)
                break  # stream completed successfully

            except Exception as e:
                if retry_remaining <= 0 or not is_retryable_error(e):
                    self.event_bus.emit_error(str(e)[:500])
                    self.event_bus.emit_llm_step_end(reason="error")
                    state.final_output = f"模型调用失败: {str(e)[:300]}"
                    state.status = "failed"
                    return state
                retry_remaining -= 1
                delay = min(2.0 * (2 ** (2 - retry_remaining)), 30.0)
                logger.warning(
                    "[EXEC] LLM stream error, retrying in %.1fs (%d left): %s",
                    delay, retry_remaining, str(e)[:200],
                )
                self.event_bus.emit({
                    "type": "retry_attempt",
                    "attempt": 3 - retry_remaining,
                    "error": str(e)[:200],
                })
                # 清空本轮已收集的内容，重试重新生成
                state.current_answer = ""
                state.reasoning = state.reasoning[:reasoning_before]
                tool_calls = []
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

        self.event_bus.emit_llm_step_end(
            reason="tool_calls" if tool_calls else "stop",
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

        if not tool_calls:
            # 终态轮（只有 LLM 回答，没有工具调用）：立即落 memory（history 只含完整轮）
            self._write_round_to_memory(state, tool_calls_records=[], is_final=True)
            state.final_output = state.current_answer
            # 记录最终一轮（只有 LLM 回答，没有工具调用）
            if self._journal_service is not None:
                self._journal_service.record_turn(
                    session_id=context.session_id,
                    turn_index=state.iteration,
                    checkpoint_id=state.checkpoint_id,
                    current_answer=state.current_answer,
                    tool_calls=[],
                    tool_result_entries=[],
                    token_usage=state.token_usage.model_dump(),
                )
            # 终态后再检查一次排队指令：运行中若有追加的新指令，继续处理而非结束
            if self._inject_queued_user_messages(state) > 0:
                logger.info("[EXEC] terminal round deferred: %d queued message(s) pending", state.iteration)
                state.final_output = ""
                state.status = "awaiting_llm"
                return state
            state.status = "completed"
            return state

        # 记录本轮模型给出的答案片段，并追加 assistant message
        state.messages.append(
            self.message_builder.build_assistant_tool_calls_message(tool_calls, state.current_answer)
        )
        state.pending_tool_calls = tool_calls
        state.status = "awaiting_tool"
        return state

    def _on_awaiting_tool(self, state: AgentLoopState, context: RunContext) -> AgentLoopState:
        """顺序执行 pending_tool_calls，检测 DOOM LOOP / 连续失败。"""
        tool_calls = state.pending_tool_calls

        # 文件快照：在执行写操作前保存当前工作区状态
        self._snapshot_files_if_needed(state, context)

        # 记录本轮开始时的 artifact 集合，用于乐观自动推进
        state._round_artifacts_before = list(state.artifacts)

        # 本轮工具结果 journal 条目
        tool_result_entries = []
        # 本轮工具记录（写 memory 用：tool_name/input/output/status）
        round_tool_records: List[Dict[str, Any]] = []

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

            self.event_bus.emit_tool_status(call.name, "running", tool_input=tool_input_str, call_id=call.id)
            logger.info("[EXEC] executing tool: name=%s, call_id=%s, input_len=%d", call.name, call.id, len(tool_input_str))

            # 执行工具
            # 阶段E：将 state.mode 注入 context（_resolve_mode 优先读 context.mode）
            context.mode = getattr(state, "mode", "execution")
            result = self.tool_executor.execute(call, context, registry=self._tool_registry)
            logger.info("[EXEC] tool done: name=%s, status=%s, result_len=%d", call.name, result.status, len(result.content) if result.content else 0)

            state.tool_results.append(result)
            state.doom_loop_tracker.append((call.name, input_sig))
            # 防止长时间运行导致 tracker 无限增长
            tracker_limit = max(self.DOOM_LOOP_THRESHOLD * 10, 100)
            state.doom_loop_tracker = state.doom_loop_tracker[-tracker_limit:]

            # 处理 awaiting_permission：保存当前未完成的工具调用，暂停执行
            if result.status == "awaiting_permission":
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
                failure_msg = (
                    f"工具 {call.name} 已连续失败 {self.MAX_CONSECUTIVE_TOOL_FAILURES} 次，"
                    f"强制终止执行循环。请检查参数是否正确。"
                )
                logger.warning("[EXEC] %s", failure_msg)
                self._emit_tool_error(call, failure_msg, state)
                state.final_output = failure_msg
                state.status = "failed"
                return state

            # 使用 journal service 决定 inline 还是归档
            if self._journal_service is not None:
                inline_content, journal_entry = self._journal_service.process_tool_result(
                    session_id=context.session_id,
                    tool_call=call,
                    tool_result=result,
                )
                tool_result_entries.append(journal_entry)
            else:
                inline_content = result.content

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

        # 本轮工具全部执行完毕：整轮（assistant + tool_results）原子落 memory（history 只含完整轮）
        self._write_round_to_memory(state, tool_calls_records=round_tool_records, is_final=False)

        # 本轮工具全部执行完毕，记录 journal
        if self._journal_service is not None:
            self._journal_service.record_turn(
                session_id=context.session_id,
                turn_index=state.iteration,
                checkpoint_id=state.checkpoint_id,
                current_answer=state.current_answer,
                tool_calls=tool_calls,
                tool_result_entries=tool_result_entries,
                token_usage=state.token_usage.model_dump(),
            )

        # 本轮工具全部执行完毕，进入下一轮 LLM
        state.pending_tool_calls = []
        self._auto_advance_plan(state)
        state.iteration += 1
        state.status = "awaiting_llm"
        return state

    # --- memory 单一历史源：整轮原子写入 + 排队指令注入 ---

    def _write_round_to_memory(
        self,
        state: AgentLoopState,
        tool_calls_records: List[Dict[str, Any]],
        is_final: bool,
    ) -> None:
        """把一个完整 LLM 调用轮（assistant 产物 + 本轮工具结果）原子写入 memory。

        仅在轮原子完成（LLM 出完 + 全部工具执行完 / 终态无工具）后调用。
        中断路径不会走到这里，故 memory 永远只含完整轮。子代理 memory=None 时跳过。
        """
        if self._memory is None or not hasattr(self._memory, "add_assistant_round"):
            return
        try:
            self._memory.add_assistant_round(
                content=state.current_answer or "",
                reasoning=getattr(state, "round_reasoning", "") or "",
                tool_calls=tool_calls_records,
                is_final=is_final,
            )
        except Exception as e:
            logger.warning("[EXEC] write round to memory failed: %s", e)

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
                state.pending_tool_calls = []
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
                result = self.tool_executor.execute(pending_call, context, registry=self._tool_registry)
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
        result = self.tool_executor.execute(
            first_call,
            context,
            registry=self._tool_registry,
            authorized_ask_id=authorized_ask_id,
        )
        state.tool_results.append(result)

        self.event_bus.emit_tool_result(
            tool_name=first_call.name,
            status=result.status,
            content=result.content,
            tool_input=json.dumps(first_call.arguments, ensure_ascii=False) if first_call.arguments else "",
            call_id=first_call.id,
        )

        if result.artifacts:
            state.artifacts.extend(result.artifacts)

        state.messages.append(
            self.message_builder.build_tool_result_message(first_call.id, result.content)
        )

        # 移除已执行的第一个 call
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
    ) -> None:
        """消费单个 LLM 事件。"""
        if event.type == "reasoning":
            state.reasoning += event.content
            self.event_bus.emit_reasoning(event.content)
        elif event.type == "token":
            state.current_answer += event.content
            self.event_bus.emit_token(event.content)
        elif event.type == "tool_call_done":
            if event.tool_call is not None:
                tool_calls_ref.append(event.tool_call)
        elif event.type == "error":
            self.event_bus.emit_error(event.content)
            self.event_bus.emit_llm_step_end(reason="error")
            raise RuntimeError(event.content)
        elif event.type == "timeout":
            self.event_bus.emit_error(event.content)
            self.event_bus.emit_llm_step_end(reason="timeout")
            raise TimeoutError(event.content)
        elif event.type == "done":
            pass
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

    def _snapshot_files_if_needed(self, state: AgentLoopState, context: RunContext) -> None:
        """在工具执行前对会话工作区做文件快照。"""
        if self._checkpoint_service is None:
            return
        files_dirs = []
        if context.output_dir:
            files_dirs.append(context.output_dir)
        if context.upload_dir:
            files_dirs.append(context.upload_dir)
        if not files_dirs:
            return
        # 快照会在 save checkpoint 时一并写入；这里只是触发一次保存
        self._save_checkpoint(state, context, files_dirs=files_dirs)

    def _save_checkpoint(
        self,
        state: AgentLoopState,
        context: RunContext,
        files_dirs: Optional[List[str]] = None,
    ) -> None:
        """保存 checkpoint，失败不阻塞执行。"""
        if self._checkpoint_service is None:
            return
        try:
            files_dirs = files_dirs or []
            # 只有在 awaiting_tool 进入时才需要文件快照
            if state.status == "awaiting_tool" and not files_dirs:
                if context.output_dir:
                    files_dirs.append(context.output_dir)
                if context.upload_dir:
                    files_dirs.append(context.upload_dir)
            self._checkpoint_service.save(
                state,
                files_dirs=files_dirs if files_dirs else None,
                metadata={
                    "model_name": getattr(self.model_client, 'model_name', ''),
                    "status": state.status,
                },
            )
        except Exception as e:
            logger.error("NativeAgentExecutor: checkpoint save failed: %s", e)

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
            run_id=f"run-{int(time.time())}",
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
