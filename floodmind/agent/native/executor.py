"""
Native Agent Runtime - NativeAgentExecutor

自研工具调用循环，替代 LangChain AgentExecutor。
支持流式 token/reasoning/tool_call 输出、工具执行、产物检测。
"""

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from floodmind.agent.native.types import (
    AgentResult,
    ModelEvent,
    RunContext,
)
from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult
from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.retry import is_retryable_error

logger = logging.getLogger(__name__)


class NativeAgentExecutor:
    def __init__(
        self,
        model_client: ModelClient,
        tool_executor: Any,
        event_bus: EventBus,
        message_builder: Optional[MessageBuilder] = None,
        max_iterations: int = 50,
        extra_body: Optional[dict] = None,
        system_prompt: str = "",
        system_prompts: Optional[List[str]] = None,
        tools_schema: Optional[List[dict]] = None,
        tool_registry: Optional[Any] = None,
        require_plan_before_delegate: bool = False,
    ):
        self.model_client = model_client
        self.tool_executor = tool_executor
        self.event_bus = event_bus
        self.message_builder = message_builder or MessageBuilder()
        self.max_iterations = max_iterations
        self.extra_body = extra_body or {}
        # 兼容单 prompt 和多 prompt 模式：
        # - system_prompts 优先；
        # - 回退到 [system_prompt]（保留旧调用方）
        if system_prompts is not None:
            self._system_prompts: List[str] = [p for p in system_prompts if p]
        elif system_prompt:
            self._system_prompts = [system_prompt]
        else:
            self._system_prompts = []
        self._tools_schema = tools_schema
        self._tool_registry = tool_registry
        self._require_plan_before_delegate = require_plan_before_delegate

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

    MAX_CONSECUTIVE_TOOL_FAILURES = 5
    DOOM_LOOP_THRESHOLD = 3  # 连续相同工具+相同参数次数阈值

    def run(
        self,
        context: RunContext,
        user_text: str,
        attachments: Optional[list] = None,
        memory_messages: Optional[List[dict]] = None,
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> AgentResult:
        messages = self._build_initial_messages(context, user_text, attachments, memory_messages)
        final_answer = ""
        reasoning = ""
        all_tool_results: List[ToolResult] = []
        all_artifacts: List[str] = []
        plan_created = False
        _consecutive_failures: Dict[str, int] = {}
        _force_break = False
        # DOOM LOOP 检测：追踪最近 N 次工具调用的 (tool_name, input_signature)
        _recent_calls: List[tuple] = []

        effective_abort = abort_check or context.abort_check

        for iteration in range(self.max_iterations):
            if effective_abort and effective_abort():
                logger.info("NativeAgentExecutor aborted at iteration %d", iteration)
                if not final_answer:
                    final_answer = "任务已被用户中断。"
                break

            logger.info("[EXEC] === iteration %d === messages=%d, tool_results=%d", iteration, len(messages), len(all_tool_results))

            current_answer = ""
            tool_calls: List[ToolCall] = []
            _step_tokens = {"prompt_tokens": 0, "completion_tokens": 0}

            tools_param = self._tools_schema if self._tools_schema else None

            logger.info("[EXEC] calling LLM stream, iteration=%d, messages=%d, tools=%d", iteration, len(messages), len(tools_param) if tools_param else 0)

            # Step 开始事件（前端可用显示当前迭代和模型）
            self.event_bus.emit_llm_step_start(
                model_name=getattr(self.model_client, 'model_name', ''),
                iteration=iteration,
            )

            # ── LLM 流消费（带自动重试）──
            _retry_remaining = 3
            while True:
                try:
                    for event in self.model_client.stream_chat(
                        messages=messages,
                        tools=tools_param,
                        extra_body=self.extra_body or None,
                        abort_check=effective_abort,
                    ):
                        if event.type == "reasoning":
                            reasoning += event.content
                            self.event_bus.emit_reasoning(event.content)
                        elif event.type == "token":
                            current_answer += event.content
                            self.event_bus.emit_token(event.content)
                        elif event.type == "tool_call_done":
                            tool_calls.append(event.tool_call)
                        elif event.type == "error":
                            self.event_bus.emit_error(event.content)
                            self.event_bus.emit_llm_step_end(reason="error")
                            return AgentResult(
                                final_output=f"模型调用错误: {event.content}",
                                reasoning=reasoning,
                                tool_results=all_tool_results,
                                artifacts=all_artifacts,
                            )
                        elif event.type == "timeout":
                            self.event_bus.emit_error(event.content)
                            self.event_bus.emit_llm_step_end(reason="timeout")
                            return AgentResult(
                                final_output=event.content,
                                reasoning=reasoning,
                                tool_results=all_tool_results,
                                artifacts=all_artifacts,
                                is_timeout=True,
                            )
                        elif event.type == "done":
                            pass
                        elif event.type == "usage":
                            try:
                                payload = json.loads(event.content) if event.content else {}
                            except (json.JSONDecodeError, TypeError):
                                payload = {}
                            _step_tokens["prompt_tokens"] = payload.get("prompt_tokens", 0)
                            _step_tokens["completion_tokens"] = payload.get("completion_tokens", 0)
                            logger.info("[EXEC] usage event: prompt=%s, completion=%s, total=%s",
                                        payload.get("prompt_tokens"), payload.get("completion_tokens"), payload.get("total_tokens"))
                            self.event_bus.emit_token_usage(
                                prompt_tokens=payload.get("prompt_tokens", 0),
                                completion_tokens=payload.get("completion_tokens", 0),
                                total_tokens=payload.get("total_tokens", 0),
                            )
                    break  # stream completed successfully

                except Exception as e:
                    if _retry_remaining <= 0 or not is_retryable_error(e):
                        self.event_bus.emit_error(str(e)[:500])
                        self.event_bus.emit_llm_step_end(reason="error")
                        return AgentResult(
                            final_output=f"模型调用失败: {str(e)[:300]}",
                            reasoning=reasoning,
                            tool_results=all_tool_results,
                            artifacts=all_artifacts,
                        )
                    _retry_remaining -= 1
                    delay = min(2.0 * (2 ** (2 - _retry_remaining)), 30.0)
                    logger.warning(
                        "[EXEC] LLM stream error, retrying in %.1fs (%d left): %s",
                        delay, _retry_remaining + 1, str(e)[:200],
                    )
                    self.event_bus.emit({
                        "type": "retry_attempt",
                        "attempt": 3 - _retry_remaining,
                        "error": str(e)[:200],
                    })
                    # Reset partial state on retry
                    current_answer = ""
                    tool_calls = []
                    time.sleep(delay)

            logger.info("[EXEC] LLM stream done, iteration=%d, answer_len=%d, tool_calls=%d", iteration, len(current_answer), len(tool_calls))

            # Step 结束事件
            self.event_bus.emit_llm_step_end(
                reason="tool_calls" if tool_calls else "stop",
                tokens=_step_tokens,
            )

            if tool_calls:
                for tc in tool_calls:
                    raw_note = ""
                    if hasattr(tc, "_raw_arguments") and tc._raw_arguments:
                        ends_with_brace = tc._raw_arguments.endswith("}")
                        raw_note = " [RAW_PARSE_FAILED length=%d, ends_with_}=%s]" % (len(tc._raw_arguments), ends_with_brace)
                    logger.info("[EXEC] tool_call: name=%s, id=%s, args=%s%s", tc.name, tc.id, json.dumps(tc.arguments, ensure_ascii=False)[:1000] if tc.arguments else "NONE", raw_note)

            if not tool_calls:
                final_answer = current_answer
                break

            messages.append(
                self.message_builder.build_assistant_tool_calls_message(tool_calls, current_answer)
            )

            for idx, call in enumerate(tool_calls):
                if call.name == "create_plan":
                    plan_created = True

                if self._require_plan_before_delegate and call.name == "SubAgent" and not plan_created:
                    rejection_msg = "未先调用 create_plan 创建执行计划。请先调用 create_plan 工具创建执行计划，然后再委派执行单元。"
                    self.event_bus.emit_tool_status(call.name, "running", tool_input="", call_id=call.id)
                    result = ToolResult(
                        tool_call_id=call.id,
                        name=call.name,
                        content=rejection_msg,
                        status="error",
                        artifacts=[],
                    )
                    all_tool_results.append(result)
                    self.event_bus.emit_tool_result(
                        tool_name=call.name,
                        status="error",
                        content=rejection_msg,
                        tool_input="",
                        call_id=call.id,
                    )
                    messages.append(self.message_builder.build_tool_result_message(call.id, rejection_msg))
                    continue

                tool_input_str = json.dumps(call.arguments, ensure_ascii=False) if call.arguments else ""

                # ── DOOM LOOP 检测 ──
                # 检查最近 DOOM_LOOP_THRESHOLD 次同工具非 pending 调用
                # 是否使用了完全相同的参数。与连续失败计数互补：
                # - 连续失败检测：同一工具连续报错（不管参数是否相同）
                # - DOOM LOOP 检测：同一工具连续用相同参数调用（不管成败）
                input_sig = self._build_input_signature(call)
                recent_same_tool = [
                    (n, s) for n, s in _recent_calls
                    if n == call.name
                ]
                if len(recent_same_tool) >= self.DOOM_LOOP_THRESHOLD:
                    last_n = recent_same_tool[-self.DOOM_LOOP_THRESHOLD:]
                    if all(s == input_sig for _, s in last_n):
                        doom_msg = (
                            f"工具 {call.name} 已连续 {self.DOOM_LOOP_THRESHOLD} 次"
                            f"使用相同参数调用，疑似死循环，强制终止。"
                        )
                        logger.warning("[EXEC] DOOM LOOP: %s, sig=%s", doom_msg, input_sig[:200])
                        self.event_bus.emit_tool_result(
                            tool_name=call.name,
                            status="error",
                            content=doom_msg,
                            tool_input=tool_input_str,
                            call_id=call.id,
                        )
                        messages.append(self.message_builder.build_tool_result_message(
                            call.id, doom_msg
                        ))
                        if not final_answer:
                            final_answer = doom_msg
                        _force_break = True
                        for leftover in tool_calls[idx + 1:]:
                            messages.append(self.message_builder.build_tool_result_message(
                                leftover.id, f"跳过: 已因 {call.name} 死循环而中断"
                            ))
                        break

                self.event_bus.emit_tool_status(call.name, "running", tool_input=tool_input_str, call_id=call.id)
                logger.info("[EXEC] executing tool: name=%s, call_id=%s, input_len=%d", call.name, call.id, len(tool_input_str))
                result = self.tool_executor.execute(call, context, registry=self._tool_registry)
                logger.info("[EXEC] tool done: name=%s, status=%s, result_len=%d", call.name, result.status, len(result.content) if result.content else 0)
                all_tool_results.append(result)
                # 记录到 DOOM LOOP 追踪（不管成败都记录）
                _recent_calls.append((call.name, input_sig))

                # 连续失败检测
                if result.status == "error" or (result.content and "错误" in result.content[:50]):
                    _consecutive_failures[call.name] = _consecutive_failures.get(call.name, 0) + 1
                else:
                    _consecutive_failures[call.name] = 0

                if _consecutive_failures.get(call.name, 0) >= self.MAX_CONSECUTIVE_TOOL_FAILURES:
                    failure_msg = (
                        f"工具 {call.name} 已连续失败 {self.MAX_CONSECUTIVE_TOOL_FAILURES} 次，"
                        f"强制终止执行循环。请检查参数是否正确。"
                    )
                    logger.warning("[EXEC] %s", failure_msg)
                    self.event_bus.emit_tool_result(
                        tool_name=call.name,
                        status="error",
                        content=failure_msg,
                        tool_input=tool_input_str,
                        call_id=call.id,
                    )
                    messages.append(self.message_builder.build_tool_result_message(call.id, failure_msg))
                    if not final_answer:
                        final_answer = failure_msg
                    _force_break = True
                    for leftover in tool_calls[idx + 1:]:
                        messages.append(self.message_builder.build_tool_result_message(
                            leftover.id, f"跳过: 已因 {call.name} 连续失败而中断"
                        ))
                    break

                self.event_bus.emit_tool_result(
                    tool_name=call.name,
                    status=result.status,
                    content=result.content,
                    tool_input=tool_input_str,
                    call_id=call.id,
                )

                if result.artifacts:
                    all_artifacts.extend(result.artifacts)

                messages.append(
                    self.message_builder.build_tool_result_message(call.id, result.content)
                )

            if _force_break:
                break

        else:
            if not final_answer:
                last_tool_output = ""
                for tr in reversed(all_tool_results):
                    if tr.status == "completed" and tr.content:
                        last_tool_output = tr.content
                        break
                if last_tool_output:
                    final_answer = last_tool_output
                else:
                    final_answer = "Agent 达到最大迭代次数，请检查任务是否过于复杂或参数是否缺失。"

        return AgentResult(
            final_output=final_answer,
            reasoning=reasoning,
            tool_results=all_tool_results,
            artifacts=all_artifacts,
        )

    @staticmethod
    def _build_input_signature(call: ToolCall) -> str:
        """构建工具调用参数签名（用于 DOOM LOOP 检测）。

        参数 JSON 按 key 排序以保证相同语义的输入产生相同签名。
        """
        if call.arguments:
            return json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
        return "{}"

    def _build_initial_messages(
        self,
        context: RunContext,
        user_text: str,
        attachments: Optional[list],
        memory_messages: Optional[List[dict]],
    ) -> List[dict]:
        messages: List[dict] = []
        # 多条 system prompt（STATIC_GLOBAL → PROJECT → SESSION 顺序），
        # 这样 DashScope/OpenAI 的前缀缓存能命中 STATIC_GLOBAL 部分
        for sp in self._system_prompts:
            messages.append(self.message_builder.build_system_message(sp))
        if memory_messages:
            messages.extend(memory_messages)
        messages.append(self.message_builder.build_user_message(user_text, attachments))
        return messages
