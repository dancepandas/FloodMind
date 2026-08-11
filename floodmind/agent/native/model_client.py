"""
Native Agent Runtime - ModelClient

统一的 LLM 服务接口，直接对接 OpenAI 兼容 Chat Completions API。
支持流式输出、reasoning_content、tool_calls delta 拼接、多模态 content parts。

所有 LLM 调用（agent 主对话 + 记忆压缩 + 标题生成等）均通过此模块完成，
配置统一从 settings.json 读取。
"""

import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

import httpx
import openai

from floodmind.agent.native.types import InvalidToolCall, ModelEvent, TerminalReason, ToolCall
from floodmind.agent.runtime.contracts.messages import ai_message, Message
from floodmind.agent.native.retry import is_retryable_error

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
        enable_thinking: bool = False,
        provider: str = "",
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.enable_thinking = enable_thinking
        self.provider = provider
        from floodmind.config.provider_registry import validate_openai_compatible_transport
        validate_openai_compatible_transport(provider, base_url)
        from floodmind.agent.native.providers import route_codec
        self.pipeline = route_codec(provider, model_name, base_url)
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    # ── 工厂方法：从 settings.json 构造 ───────────────────────────
    @classmethod
    def from_settings(
        cls,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        enable_thinking: bool = False,
    ) -> "ModelClient":
        """从 settings.json 构造 ModelClient（默认激活模型），参数未提供时用解析值。

        解析统一走 resolve_model()——SDK/桌面端的稳定契约。
        """
        from floodmind.config.model_resolver import resolve_model

        rm = resolve_model()
        return cls(
            api_key=api_key or rm.api_key,
            base_url=base_url or rm.base_url,
            model_name=model_name or rm.id,
            temperature=temperature if temperature is not None else rm.temperature,
            max_tokens=max_tokens if max_tokens is not None else rm.max_tokens,
            enable_thinking=enable_thinking,
            provider=rm.provider,
        )

    @classmethod
    def from_settings_with_preset(
        cls,
        model_key: str,
        enable_reasoning: bool = False,
    ) -> "ModelClient":
        """根据 settings.json 中的指定模型构造 ModelClient。"""
        from floodmind.config.model_resolver import resolve_model

        rm = resolve_model(model_key=model_key)
        if enable_reasoning:
            # 推理模式：取模型 thinking_* 参数（缺省回退 default_*）
            from floodmind.config.model_presets import get_preset
            preset = get_preset(model_key) or {}
            temperature = preset.get("thinking_temperature", 0.2)
            max_tokens = preset.get("thinking_max_tokens", rm.max_tokens)
        else:
            temperature = rm.temperature
            max_tokens = rm.max_tokens

        return cls(
            api_key=rm.api_key,
            base_url=rm.base_url,
            model_name=rm.id,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_thinking=enable_reasoning,
            provider=rm.provider,
        )

    # ── 非流式调用（兼容旧 QwenLLMService.invoke / .chat）───────
    def invoke(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Message:
        """单轮非流式调用，返回 ai_message"""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, temperature=temperature, max_tokens=max_tokens)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Message:
        """多轮非流式调用，返回 ai_message"""
        try:
            messages = self.pipeline.prepare_messages(messages)
        except ValueError as e:
            logger.error("ModelClient message validation failed: %s", e)
            raise

        request_params: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        if kwargs.get("extra_body"):
            request_params["extra_body"] = dict(kwargs["extra_body"])
        request_params = self.pipeline.prepare_request(
            request_params, enable_thinking=self.enable_thinking, stream=False
        )

        try:
            response = self._client.chat.completions.create(**request_params)
        except openai.APIError as e:
            logger.error("ModelClient invoke error: %s", e)
            raise

        choice = response.choices[0]
        content = choice.message.content or ""

        additional_kwargs: Dict[str, Any] = {}
        reasoning = self.pipeline.extract_message_reasoning(choice.message)
        if reasoning:
            additional_kwargs["reasoning_content"] = reasoning

        usage = self.pipeline.extract_response_usage(response)
        if usage:
            additional_kwargs["usage"] = usage

        return ai_message(content=content, **additional_kwargs)

    # ── 流式调用（agent 主循环使用）──────────────────────────────
    def stream_chat(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        tool_choice: Any = "auto",
        extra_body: Optional[dict] = None,
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> Iterator[ModelEvent]:
        try:
            messages = self.pipeline.prepare_messages(messages)
        except ValueError as e:
            logger.error("ModelClient message validation failed: %s", e)
            yield ModelEvent(type="error", content=str(e))
            return

        request_params: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = tool_choice
        # 显式 extra_body 进入 params，pipeline 用 setdefault 注入厂商参数（显式优先）
        if extra_body:
            request_params["extra_body"] = dict(extra_body)
        request_params = self.pipeline.prepare_request(
            request_params, enable_thinking=self.enable_thinking, stream=True
        )

        tool_call_accumulators: Dict[int, Dict[str, str]] = {}
        completed_tool_call_accumulators: List[Dict[str, str]] = []
        assistant_accumulator: Dict[str, Any] = {"role": "assistant", "content": ""}
        state = self.pipeline.new_stream_state()
        latest_usage: Optional[Dict[str, int]] = None
        terminal_reason = TerminalReason.from_raw(None)

        def finalize_tool_call(idx: int, acc: Dict[str, str]) -> tuple[Optional[ToolCall], Optional[InvalidToolCall]]:
            """Parse one complete call without converting malformed JSON to executable defaults."""
            if not acc.get("id"):
                acc["id"] = f"call_{idx}_{time.time_ns()}"
            arguments_str = acc.get("arguments", "")
            if not arguments_str:
                return ToolCall(id=acc["id"], name=acc["name"], arguments={}), None
            try:
                parsed_args = json.loads(arguments_str)
            except (json.JSONDecodeError, TypeError) as exc:
                error = f"工具参数不是有效 JSON: {exc}"
                logger.warning(
                    "tool_call arguments JSON parse failed for %s. length=%d, preview=%s",
                    acc["name"], len(arguments_str), arguments_str[:300],
                )
                return None, InvalidToolCall(
                    id=acc["id"], name=acc["name"], raw_arguments=arguments_str, error=error,
                )
            if not isinstance(parsed_args, dict):
                return None, InvalidToolCall(
                    id=acc["id"], name=acc["name"], raw_arguments=arguments_str,
                    error="工具参数 JSON 必须是对象。",
                )
            return ToolCall(id=acc["id"], name=acc["name"], arguments=parsed_args), None

        try:
            stream = self._client.chat.completions.create(**request_params)
        except openai.APIError as e:
            # 可重试错误（如连接阶段的网络抖动）直接抛给调用方（executor 重试循环），
            # 保留异常链（APIConnectionError 的 str() 恒为 "Connection error."，
            # 真实原因在 __cause__，is_retryable_error 会递归检查）。
            if is_retryable_error(e):
                raise
            logger.error("ModelClient API error: %s", e)
            yield ModelEvent(type="error", content=str(e))
            return

        try:
            for chunk in stream:
                if abort_check and abort_check():
                    logger.info("ModelClient stream aborted by external signal")
                    stream.close()
                    yield ModelEvent(
                        type="done",
                        content="",
                        terminal_reason=TerminalReason.from_raw("aborted"),
                    )
                    return

                usage = self.pipeline.extract_usage(chunk)
                if usage:
                    # Providers may emit several cumulative usage snapshots.  Keep
                    # replacing the candidate so the terminal event is authoritative.
                    latest_usage = usage

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                reasoning_inc = self.pipeline.extract_reasoning(delta, state)
                self.pipeline.capture_assistant_delta(delta, state, assistant_accumulator)
                if reasoning_inc:
                    yield ModelEvent(type="reasoning", content=reasoning_inc)

                if delta.content:
                    answer_inc, tag_reasoning = self.pipeline.filter_content(
                        str(delta.content), state
                    )
                    if tag_reasoning:
                        yield ModelEvent(type="reasoning", content=tag_reasoning)
                    if answer_inc:
                        yield ModelEvent(type="token", content=answer_inc)

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
                if finish_reason is not None:
                    terminal_reason = TerminalReason.from_raw(finish_reason)
                if finish_reason == "tool_calls":
                    for idx, acc in sorted(tool_call_accumulators.items()):
                        tool_call, invalid_call = finalize_tool_call(idx, acc)
                        if tool_call is not None:
                            yield ModelEvent(type="tool_call_done", content="", tool_call=tool_call)
                        elif invalid_call is not None:
                            yield ModelEvent(
                                type="invalid_tool_call",
                                content=invalid_call.error,
                                invalid_tool_call=invalid_call,
                            )
                        completed_tool_call_accumulators.append(dict(acc))
                    tool_call_accumulators.clear()

                if finish_reason in ("stop", "length", "content_filter"):
                    pass

            if tool_call_accumulators:
                for idx, acc in sorted(tool_call_accumulators.items()):
                    tool_call, invalid_call = finalize_tool_call(idx, acc)
                    if tool_call is not None:
                        yield ModelEvent(type="tool_call_done", content="", tool_call=tool_call)
                    elif invalid_call is not None:
                        yield ModelEvent(
                            type="invalid_tool_call",
                            content=invalid_call.error,
                            invalid_tool_call=invalid_call,
                        )
                    completed_tool_call_accumulators.append(dict(acc))

            if latest_usage:
                yield ModelEvent(type="usage", content=json.dumps(latest_usage))

            assistant_message = self.pipeline.build_assistant_message(
                assistant_accumulator,
                completed_tool_call_accumulators,
            )
            yield ModelEvent(
                type="assistant_message_done",
                raw={
                    "message": assistant_message,
                    "provider": self.pipeline.name,
                },
            )
            yield ModelEvent(type="done", content="", terminal_reason=terminal_reason)

        except openai.APIError as e:
            # 可重试错误抛给调用方（executor 重试循环），保留异常链
            if is_retryable_error(e):
                raise
            logger.error("ModelClient stream error: %s", e)
            yield ModelEvent(type="error", content=str(e))
        except httpx.ReadTimeout as e:
            # 超时属可重试（"timed out"），抛给调用方重试
            if is_retryable_error(e):
                raise
            logger.error("ModelClient stream timeout: %s", e)
            yield ModelEvent(type="timeout", content="调用超时，请切换模型或重试")
        except Exception as e:
            # 可重试错误抛给调用方（executor 重试循环），保留异常链
            if is_retryable_error(e):
                raise
            logger.error("ModelClient unexpected stream error: %s", e, exc_info=True)
            yield ModelEvent(type="error", content=f"流式输出异常: {str(e)}")
