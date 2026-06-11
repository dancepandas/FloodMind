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

from floodmind.agent.native.types import ModelEvent, ToolCall
from floodmind.agent.runtime.contracts.messages import ai_message, Message

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
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.enable_thinking = enable_thinking
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
        """从 settings.json 构造 ModelClient，参数未提供时用 settings 中的默认值"""
        from floodmind.config.settings import settings as _s

        mc = _s.model
        return cls(
            api_key=api_key or mc.api_key,
            base_url=base_url or _resolve_base_url(mc.provider_name),
            model_name=model_name or mc.model_name,
            temperature=temperature if temperature is not None else float(mc.temperature),
            max_tokens=max_tokens if max_tokens is not None else int(mc.max_tokens),
            enable_thinking=enable_thinking,
        )

    @classmethod
    def from_settings_with_preset(
        cls,
        model_key: str,
        enable_reasoning: bool = False,
    ) -> "ModelClient":
        """根据 settings.json 中的 model preset 构造 ModelClient"""
        from floodmind.config.model_presets import (
            get_preset,
            resolve_api_key,
            resolve_base_url,
        )

        preset = get_preset(model_key)
        if not preset:
            raise ValueError(f"未找到模型预设: {model_key}")

        p_api_key = resolve_api_key(preset)
        p_base_url = resolve_base_url(preset)
        p_model_name = preset["model_name"]

        if enable_reasoning:
            temperature = preset.get("thinking_temperature", 0.2)
            max_tokens = preset.get("thinking_max_tokens", 4096)
        else:
            temperature = preset.get("default_temperature", 0.3)
            max_tokens = preset.get("default_max_tokens", 4096)

        return cls(
            api_key=p_api_key,
            base_url=p_base_url,
            model_name=p_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_thinking=enable_reasoning,
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
        request_params: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        request_params["stream_options"] = {"include_usage": True}

        # 支持 extra_body（如 enable_thinking）
        extra_body = kwargs.get("extra_body")
        if extra_body is None and self.enable_thinking:
            extra_body = {"enable_thinking": True}
        if extra_body:
            request_params["extra_body"] = extra_body

        try:
            response = self._client.chat.completions.create(**request_params)
        except openai.APIError as e:
            logger.error("ModelClient invoke error: %s", e)
            raise

        choice = response.choices[0]
        content = choice.message.content or ""

        additional_kwargs: Dict[str, Any] = {}
        reasoning = getattr(choice.message, "reasoning_content", None)
        if not reasoning:
            reasoning = getattr(choice.message, "reasoning", None)
        if reasoning:
            additional_kwargs["reasoning_content"] = reasoning

        usage = getattr(response, "usage", None)
        if usage:
            additional_kwargs["usage"] = {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            }

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
        request_params: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        request_params["stream_options"] = {"include_usage": True}
        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = tool_choice
        # 合成 extra_body：显式参数优先，否则用 enable_thinking
        effective_extra: Optional[dict] = None
        if extra_body:
            effective_extra = dict(extra_body)
        elif self.enable_thinking:
            effective_extra = {"enable_thinking": True}
        if effective_extra:
            request_params["extra_body"] = effective_extra

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
                        json_ok = False
                        if arguments_str:
                            try:
                                parsed_args = json.loads(arguments_str)
                                json_ok = True
                            except json.JSONDecodeError:
                                try:
                                    repaired = arguments_str + "}"
                                    parsed_args = json.loads(repaired)
                                    json_ok = True
                                    logger.info(
                                        "tool_call arguments JSON repaired for %s with +'}' (length=%d)",
                                        acc["name"], len(arguments_str),
                                    )
                                except json.JSONDecodeError:
                                    parsed_args = {}
                                    logger.warning(
                                        "tool_call arguments JSON parse failed for %s. "
                                        "length=%d, ends_with_}=%s, preview=%s",
                                        acc["name"],
                                        len(arguments_str),
                                        arguments_str.endswith("}"),
                                        arguments_str[:300],
                                    )

                        tool_call = ToolCall(
                            id=acc["id"] or f"call_{idx}_{time.time_ns()}",
                            name=acc["name"],
                            arguments=parsed_args,
                        )
                        if not json_ok and arguments_str:
                            tool_call._raw_arguments = arguments_str
                        yield ModelEvent(type="tool_call_done", content="", tool_call=tool_call)
                    tool_call_accumulators.clear()

                if finish_reason in ("stop", "length", "content_filter"):
                    pass

            if tool_call_accumulators:
                for idx, acc in tool_call_accumulators.items():
                    arguments_str = acc["arguments"]
                    parsed_args: dict = {}
                    json_ok = False
                    if arguments_str:
                        try:
                            parsed_args = json.loads(arguments_str)
                            json_ok = True
                        except json.JSONDecodeError:
                            parsed_args = {}
                            logger.warning(
                                "tool_call arguments JSON parse failed for %s (stream end). "
                                "length=%d, ends_with_}=%s, preview=%s",
                                acc["name"],
                                len(arguments_str),
                                arguments_str.endswith("}"),
                                arguments_str[:300],
                            )
                    tool_call = ToolCall(
                        id=acc["id"] or f"call_{idx}_{time.time_ns()}",
                        name=acc["name"],
                        arguments=parsed_args,
                    )
                    if not json_ok and arguments_str:
                        tool_call._raw_arguments = arguments_str
                    yield ModelEvent(type="tool_call_done", content="", tool_call=tool_call)

            yield ModelEvent(type="done", content="")

        except openai.APIError as e:
            logger.error("ModelClient stream error: %s", e)
            yield ModelEvent(type="error", content=str(e))
        except httpx.ReadTimeout as e:
            logger.error("ModelClient stream timeout: %s", e)
            yield ModelEvent(type="timeout", content="调用超时，请切换模型或重试")
        except Exception as e:
            logger.error("ModelClient unexpected stream error: %s", e, exc_info=True)
            yield ModelEvent(type="error", content=f"流式输出异常: {str(e)}")


# ── 工具函数 ─────────────────────────────────────────────────────
def _resolve_base_url(provider_name: str) -> str:
    """根据 provider_name 从配置中找到对应的 base URL"""
    from floodmind.config.settings import get_config
    cfg = get_config()
    provider_cfg = cfg.get("provider", {})
    if isinstance(provider_cfg, dict):
        p = provider_cfg.get(provider_name)
        if isinstance(p, dict):
            opts = p.get("options", {}) or {}
            if isinstance(opts, dict):
                url = opts.get("baseURL") or opts.get("base_url")
                if url:
                    return url
    # 兜底
    return os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
