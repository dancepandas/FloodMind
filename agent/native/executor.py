"""
Native Agent Runtime - NativeAgentExecutor

自研工具调用循环，替代 LangChain AgentExecutor。
支持流式 token/reasoning/tool_call 输出、工具执行、产物检测。
"""

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from agent.native.types import (
    AgentResult,
    ModelEvent,
    RunContext,
)
from agent.runtime.contracts.tools import ToolCall, ToolResult
from agent.native.event_bus import EventBus
from agent.native.message_builder import MessageBuilder
from agent.native.model_client import ModelClient

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
        self.system_prompt = system_prompt
        self._tools_schema = tools_schema
        self._tool_registry = tool_registry
        self._require_plan_before_delegate = require_plan_before_delegate

    def set_tools_schema(self, schema: List[dict]) -> None:
        self._tools_schema = schema

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

        effective_abort = abort_check or context.abort_check

        for iteration in range(self.max_iterations):
            if effective_abort and effective_abort():
                logger.info("NativeAgentExecutor aborted at iteration %d", iteration)
                if not final_answer:
                    final_answer = "任务已被用户中断。"
                break

            current_answer = ""
            tool_calls: List[ToolCall] = []

            tools_param = self._tools_schema if self._tools_schema else None

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
                    return AgentResult(
                        final_output=f"模型调用错误: {event.content}",
                        reasoning=reasoning,
                        tool_results=all_tool_results,
                        artifacts=all_artifacts,
                    )
                elif event.type == "done":
                    pass

            if not tool_calls:
                final_answer = current_answer
                break

            messages.append(
                self.message_builder.build_assistant_tool_calls_message(tool_calls, current_answer)
            )

            for call in tool_calls:
                if call.name == "create_plan":
                    plan_created = True

                if self._require_plan_before_delegate and call.name == "delegate_execution_specialist" and not plan_created:
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
                self.event_bus.emit_tool_status(call.name, "running", tool_input=tool_input_str, call_id=call.id)
                result = self.tool_executor.execute(call, context, registry=self._tool_registry)
                all_tool_results.append(result)

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

    def _build_initial_messages(
        self,
        context: RunContext,
        user_text: str,
        attachments: Optional[list],
        memory_messages: Optional[List[dict]],
    ) -> List[dict]:
        messages: List[dict] = []
        if self.system_prompt:
            messages.append(self.message_builder.build_system_message(self.system_prompt))
        if memory_messages:
            messages.extend(memory_messages)
        messages.append(self.message_builder.build_user_message(user_text, attachments))
        return messages
