"""
Native Agent Runtime - ModelClient

直接对接 OpenAI 兼容 Chat Completions API，不依赖 LangChain ChatOpenAI。
支持流式输出、reasoning_content、tool_calls delta 拼接、多模态 content parts。
"""

import json
import logging
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

import httpx
import openai

from agent.native.types import ModelEvent, ToolCall

logger = logging.getLogger(__name__)


class ModelClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_name: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: int = 90,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    def stream_chat(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        tool_choice: Any = "auto",
        extra_body: Optional[dict] = None,
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> Iterator[ModelEvent]:
        request_params: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if "openai.com" in (self.base_url or ""):
            request_params["stream_options"] = {"include_usage": True}
        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = tool_choice
        if extra_body:
            request_params["extra_body"] = extra_body

        tool_call_accumulators: Dict[int, Dict[str, str]] = {}
        reasoning_buffer = ""
        is_in_thinking_phase = False

        try:
            stream = self._client.chat.completions.create(**request_params)
        except openai.APIError as e:
            logger.error("ModelClient API error: %s", e)
            yield ModelEvent(type="error", content=str(e))
            return

        try:
            for chunk in stream:
                if abort_check and abort_check():
                    logger.info("ModelClient stream aborted by external signal")
                    stream.close()
                    yield ModelEvent(type="done", content="")
                    return

                if not chunk.choices:
                    usage = getattr(chunk, "usage", None)
                    if usage:
                        yield ModelEvent(
                            type="usage",
                            content=json.dumps({
                                "prompt_tokens": usage.prompt_tokens or 0,
                                "completion_tokens": usage.completion_tokens or 0,
                                "total_tokens": usage.total_tokens or 0,
                            }),
                        )
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    reasoning_text = str(delta.reasoning_content)
                    if reasoning_buffer and reasoning_text.startswith(reasoning_buffer):
                        new_reasoning = reasoning_text[len(reasoning_buffer):]
                        reasoning_buffer = reasoning_text
                    else:
                        new_reasoning = reasoning_text
                        reasoning_buffer += reasoning_text
                    is_in_thinking_phase = True
                    if new_reasoning:
                        yield ModelEvent(type="reasoning", content=new_reasoning)
                    continue

                if hasattr(delta, "reasoning") and delta.reasoning:
                    is_in_thinking_phase = True
                    yield ModelEvent(type="reasoning", content=str(delta.reasoning))
                    continue

                if is_in_thinking_phase and delta.content:
                    is_in_thinking_phase = False
                    reasoning_buffer = ""

                if delta.content:
                    yield ModelEvent(type="token", content=delta.content)

                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_call_accumulators:
                            tool_call_accumulators[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        acc = tool_call_accumulators[idx]
                        if tc_delta.id:
                            acc["id"] = tc_delta.id
                        if tc_delta.function and tc_delta.function.name:
                            acc["name"] = tc_delta.function.name
                        if tc_delta.function and tc_delta.function.arguments:
                            acc["arguments"] += tc_delta.function.arguments

                finish_reason = choice.finish_reason
                if finish_reason == "tool_calls":
                    for idx, acc in sorted(tool_call_accumulators.items()):
                        arguments_str = acc["arguments"]
                        parsed_args: dict = {}
                        if arguments_str:
                            try:
                                parsed_args = json.loads(arguments_str)
                            except json.JSONDecodeError:
                                try:
                                    repaired = arguments_str + "}"
                                    parsed_args = json.loads(repaired)
                                except json.JSONDecodeError:
                                    parsed_args = {}
                                    logger.warning(
                                        "tool_call arguments JSON parse failed for %s, raw: %s. Passing empty args.",
                                        acc["name"],
                                        arguments_str[:200],
                                    )

                        tool_call = ToolCall(
                            id=acc["id"] or f"call_{idx}_{time.time_ns()}",
                            name=acc["name"],
                            arguments=parsed_args,
                        )
                        yield ModelEvent(type="tool_call_done", content="", tool_call=tool_call)
                    tool_call_accumulators.clear()

                if finish_reason == "stop":
                    pass

            if tool_call_accumulators:
                for idx, acc in tool_call_accumulators.items():
                    arguments_str = acc["arguments"]
                    parsed_args: dict = {}
                    if arguments_str:
                        try:
                            parsed_args = json.loads(arguments_str)
                        except json.JSONDecodeError:
                            parsed_args = {}
                            logger.warning(
                                "tool_call arguments JSON parse failed for %s (stream end), raw: %s",
                                acc["name"],
                                arguments_str[:200],
                            )
                    tool_call = ToolCall(
                        id=acc["id"] or f"call_{idx}_{time.time_ns()}",
                        name=acc["name"],
                        arguments=parsed_args,
                    )
                    yield ModelEvent(type="tool_call_done", content="", tool_call=tool_call)

            yield ModelEvent(type="done", content="")

        except openai.APIError as e:
            logger.error("ModelClient stream error: %s", e)
            yield ModelEvent(type="error", content=str(e))
        except httpx.ReadTimeout as e:
            logger.error("ModelClient stream timeout: %s", e)
            yield ModelEvent(type="timeout", content="模型请求超时，请稍后重试")
        except Exception as e:
            logger.error("ModelClient unexpected stream error: %s", e, exc_info=True)
            yield ModelEvent(type="error", content=f"流式输出异常: {str(e)}")