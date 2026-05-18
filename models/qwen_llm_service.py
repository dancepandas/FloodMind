"""
Qwen LLM 服务

使用 openai SDK 直接调用，支持流式输出和推理内容提取。
不依赖 LangChain。
"""

import json
import logging
import os
import re
import time
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

from openai import OpenAI
from pydantic import BaseModel

from agent.runtime.contracts.messages import Message, ai_message
from config.settings import settings

logger = logging.getLogger(__name__)


class QwenLLMService:
    """Qwen LLM 服务"""

    _instance: Optional['QwenLLMService'] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        enable_thinking: bool = False,
    ):
        if hasattr(self, '_initialized') and self._initialized:
            return

        self.model_name = model_name or settings.qwen.model_name
        self.api_key = api_key or settings.qwen.api_key
        self.base_url = base_url or os.getenv("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking

        self._client: Optional[OpenAI] = None
        self._initialized = True

    @property
    def enable_reasoning(self) -> bool:
        """兼容别名：外部代码使用 enable_reasoning 访问推理模式"""
        return self.enable_thinking

    @enable_reasoning.setter
    def enable_reasoning(self, value: bool) -> None:
        self.enable_thinking = value

        logger.info(
            f"QwenLLMService 初始化: model={self.model_name}, "
            f"base_url={self.base_url}, thinking={enable_thinking}"
        )

    @property
    def client(self) -> OpenAI:
        """获取 OpenAI 客户端（懒加载）"""
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    def get_llm(self) -> 'QwenLLMService':
        """获取 LLM 实例（兼容旧接口，返回 self）"""
        return self

    def invoke(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Message:
        """调用 LLM"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        start_time = time.time()
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens,
                **kwargs,
            )

            content = response.choices[0].message.content or ""
            reasoning_content = None

            if hasattr(response.choices[0].message, 'reasoning_content'):
                reasoning_content = response.choices[0].message.reasoning_content

            additional_kwargs = {}
            if reasoning_content:
                additional_kwargs["reasoning_content"] = reasoning_content

            usage = response.usage
            if usage:
                additional_kwargs["usage"] = {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                }

            elapsed = time.time() - start_time
            logger.info(
                f"LLM 调用完成: model={self.model_name}, "
                f"tokens={usage.total_tokens if usage else 'N/A'}, "
                f"耗时={elapsed:.2f}s"
            )

            return ai_message(content=content, **additional_kwargs)

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"LLM 调用失败: {e}, 耗时={elapsed:.2f}s")
            raise

    def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Iterator[Dict[str, Any]]:
        """流式调用 LLM"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        start_time = time.time()
        try:
            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens,
                stream=True,
                **kwargs,
            )

            for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason

                result: Dict[str, Any] = {
                    "type": "content",
                    "content": "",
                    "finish_reason": finish_reason,
                }

                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    result["type"] = "reasoning"
                    result["content"] = delta.reasoning_content
                elif delta.content:
                    result["content"] = delta.content

                yield result

                if finish_reason == "stop":
                    break

            elapsed = time.time() - start_time
            logger.info(f"LLM 流式调用完成: model={self.model_name}, 耗时={elapsed:.2f}s")

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"LLM 流式调用失败: {e}, 耗时={elapsed:.2f}s")
            yield {"type": "error", "content": str(e), "finish_reason": "error"}

    def stream_with_reasoning(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Iterator[Dict[str, Any]]:
        """流式调用 LLM（带推理内容）"""
        extra_kwargs = dict(kwargs)
        if self.enable_thinking:
            extra_kwargs["extra_body"] = {"enable_thinking": True}

        yield from self.stream(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra_kwargs,
        )

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Message:
        """多轮对话调用"""
        start_time = time.time()
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens,
                **kwargs,
            )

            content = response.choices[0].message.content or ""
            reasoning_content = None

            if hasattr(response.choices[0].message, 'reasoning_content'):
                reasoning_content = response.choices[0].message.reasoning_content

            additional_kwargs = {}
            if reasoning_content:
                additional_kwargs["reasoning_content"] = reasoning_content

            usage = response.usage
            if usage:
                additional_kwargs["usage"] = {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                }

            elapsed = time.time() - start_time
            logger.info(
                f"LLM 多轮对话完成: model={self.model_name}, "
                f"tokens={usage.total_tokens if usage else 'N/A'}, "
                f"耗时={elapsed:.2f}s"
            )

            return ai_message(content=content, **additional_kwargs)

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"LLM 多轮对话失败: {e}, 耗时={elapsed:.2f}s")
            raise

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Iterator[Dict[str, Any]]:
        """多轮对话流式调用"""
        start_time = time.time()
        try:
            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens,
                stream=True,
                **kwargs,
            )

            for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason

                result: Dict[str, Any] = {
                    "type": "content",
                    "content": "",
                    "finish_reason": finish_reason,
                }

                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    result["type"] = "reasoning"
                    result["content"] = delta.reasoning_content
                elif delta.content:
                    result["content"] = delta.content

                yield result

                if finish_reason == "stop":
                    break

            elapsed = time.time() - start_time
            logger.info(f"LLM 多轮流式调用完成: model={self.model_name}, 耗时={elapsed:.2f}s")

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"LLM 多轮流式调用失败: {e}, 耗时={elapsed:.2f}s")
            yield {"type": "error", "content": str(e), "finish_reason": "error"}

    @classmethod
    def reset(cls):
        """重置单例"""
        cls._instance = None


def get_qwen_llm_service(
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    enable_reasoning: bool = False,
) -> QwenLLMService:
    """获取单例 QwenLLMService 实例"""
    QwenLLMService.reset()
    return QwenLLMService(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature if temperature is not None else 0.7,
        max_tokens=max_tokens or 8192,
        enable_thinking=enable_reasoning,
    )


def create_llm_service_from_preset(
    model_key: str,
    enable_reasoning: bool = False,
) -> QwenLLMService:
    """根据预设创建 LLM 服务"""
    from config.model_presets import get_preset, resolve_api_key, resolve_base_url

    preset = get_preset(model_key)
    if not preset:
        raise ValueError(f"未找到模型预设: {model_key}")

    api_key = resolve_api_key(preset)
    base_url = resolve_base_url(preset)
    model_name = preset["model_name"]

    if enable_reasoning:
        temperature = preset.get("thinking_temperature", 0.2)
        max_tokens = preset.get("thinking_max_tokens", 4096)
    else:
        temperature = preset.get("default_temperature", 0.3)
        max_tokens = preset.get("default_max_tokens", 4096)

    QwenLLMService.reset()
    return QwenLLMService(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        enable_thinking=enable_reasoning,
    )