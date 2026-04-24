"""
Qwen模型服务模块

基于LangChain集成Qwen API，提供统一的大模型调用接口。
优化版本：提升响应速度
"""

import logging
import os
from typing import Any, Mapping, Optional

import openai
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.messages.base import BaseMessageChunk
from langchain_openai.chat_models.base import (
    _convert_delta_to_message_chunk,
    _convert_dict_to_message,
    _create_usage_metadata,
)

logger = logging.getLogger(__name__)


class QwenReasoningChatOpenAI(ChatOpenAI):
    """保留 DashScope reasoning_content 的 ChatOpenAI 兼容层。"""

    @staticmethod
    def _extract_reasoning(payload: Mapping[str, Any]) -> str:
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None

        choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
        if not choices:
            return generation_chunk

        delta = choices[0].get("delta") or {}
        reasoning = self._extract_reasoning(delta)
        if reasoning and isinstance(generation_chunk.message, AIMessageChunk):
            generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        return generation_chunk

    def _create_chat_result(
        self,
        response: dict | openai.BaseModel,
        generation_info: dict | None = None,
    ) -> ChatResult:
        chat_result = super()._create_chat_result(response, generation_info)

        response_dict = response if isinstance(response, dict) else response.model_dump()
        choices = response_dict.get("choices", []) or []
        if not choices or not chat_result.generations:
            return chat_result

        message_payload = choices[0].get("message") or {}
        reasoning = self._extract_reasoning(message_payload)
        if reasoning and isinstance(chat_result.generations[0].message, AIMessage):
            chat_result.generations[0].message.additional_kwargs["reasoning_content"] = reasoning
        return chat_result


class QwenLLMService:
    """Qwen大模型服务类"""

    def __init__(self, api_key: str, model_name: str = "qwen-flash",
                 temperature: float = 0.3, max_tokens: int = 2048,
                 enable_search: bool = True, enable_reasoning: bool = False,
                 reasoning_model: str = "qwen-plus"):
        """
        初始化Qwen大模型服务

        Args:
            api_key: 阿里云API密钥
            model_name: 模型名称（推荐 qwen2.5-flash 速度更快）
            temperature: 温度参数（0-1），控制输出随机性
            max_tokens: 最大生成token数
            enable_search: 是否启用模型自带搜索能力
            enable_reasoning: 是否启用推理模式
            reasoning_model: 推理模式使用的模型名称
        """
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.enable_search = enable_search
        self.enable_reasoning = enable_reasoning
        self.reasoning_model = reasoning_model
        self.base_url = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

        # 如果启用推理模式，使用推理模型和优化的参数
        if enable_reasoning:
            actual_model = reasoning_model
            actual_temperature = min(temperature, 0.2)  # 推理模式使用更低温度
            reasoning_max_tokens = min(4096, max(1024, max_tokens))  # 推理需要更多token空间
            extra_body = {"enable_search": enable_search}
            if enable_reasoning:
                extra_body["enable_thinking"] = True
            logger.info(f"推理模式已启用 - 使用模型: {actual_model}, 温度: {actual_temperature}")
        else:
            actual_model = model_name
            actual_temperature = temperature
            reasoning_max_tokens = max_tokens
            extra_body = {"enable_search": enable_search}

        self.llm = QwenReasoningChatOpenAI(
            model=actual_model,
            api_key=api_key,
            base_url=self.base_url,
            temperature=actual_temperature,
            max_tokens=reasoning_max_tokens,
            timeout=90,
            streaming=True,
            extra_body=extra_body,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )

        mode_str = "推理模式" if enable_reasoning else "标准模式"
        logger.info(f"Qwen大模型服务初始化成功 - {mode_str} - 模型: {actual_model}, 搜索能力: {enable_search}, base_url: {self.base_url}")
    
    def get_llm(self) -> ChatOpenAI:
        """
        获取LangChain兼容的LLM对象
        
        Returns:
            ChatOpenAI实例
        """
        return self.llm
    
    def invoke(self, prompt: str, include_reasoning: bool = True) -> str:
        """
        直接调用模型生成文本

        Args:
            prompt: 输入提示
            include_reasoning: 是否包含推理过程（默认为True）

        Returns:
            生成的文本（可能包含推理过程和最终内容）
        """
        try:
            response = self.llm.invoke(prompt)

            # 如果启用推理模式且有推理内容
            if include_reasoning and hasattr(response, 'reasoning_content') and response.reasoning_content:
                return f"[推理过程]: {response.reasoning_content}\n\n[最终回答]: {response.content}"
            else:
                return response.content
        except Exception as e:
            logger.error(f"模型调用失败: {str(e)}")
            raise
    
    def stream(self, prompt: str, include_reasoning: bool = True):
        """
        流式调用模型生成文本

        Args:
            prompt: 输入提示
            include_reasoning: 是否包含推理过程（默认为True）

        Yields:
            生成的文本片段（包含推理过程和最终内容）
        """
        try:
            for chunk in self.llm.stream(prompt):
                # 检查是否有推理内容
                if hasattr(chunk, 'reasoning_content') and chunk.reasoning_content and include_reasoning:
                    yield f"[推理过程]: {chunk.reasoning_content}\n"

                # 检查是否有普通内容
                if hasattr(chunk, 'content'):
                    yield chunk.content
        except Exception as e:
            logger.error(f"模型流式调用失败: {str(e)}")
            raise


_llm_service: Optional[QwenLLMService] = None


def get_qwen_llm_service(api_key: str, model_name: str = "qwen-flash",
                        temperature: float = 0.3, max_tokens: int = 2048,
                        enable_search: bool = False,
                        enable_reasoning: bool = False,
                        reasoning_model: str = "qwen-plus") -> QwenLLMService:
    """
    获取全局Qwen大模型服务实例

    Args:
        api_key: API密钥
        model_name: 模型名称
        temperature: 温度参数
        max_tokens: 最大token数
        enable_search: 是否启用模型自带搜索能力
        enable_reasoning: 是否启用推理模式
        reasoning_model: 推理模式使用的模型名称

    Returns:
        QwenLLMService实例
    """
    global _llm_service
    current_signature = {
        "api_key": api_key,
        "model_name": model_name,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "enable_search": enable_search,
        "enable_reasoning": enable_reasoning,
        "reasoning_model": reasoning_model,
        "base_url": os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    }

    previous_signature = getattr(_llm_service, "_config_signature", None) if _llm_service else None
    need_recreate = _llm_service is None or previous_signature != current_signature

    if need_recreate and previous_signature is not None:
        logger.info("Qwen 配置变更，重新创建实例")
    
    if need_recreate:
        try:
            from langchain_core.globals import set_llm_cache
            from langchain_community.cache import SQLiteCache
            set_llm_cache(SQLiteCache(database_path=".langchain_cache.db"))
            logger.info("LLM 缓存已启用 (SQLiteCache -> .langchain_cache.db)")
        except Exception as _cache_err:
            logger.warning(f"LLM 缓存启用失败，将不使用缓存: {_cache_err}")
        _llm_service = QwenLLMService(
            api_key=api_key,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_search=enable_search,
            enable_reasoning=enable_reasoning,
            reasoning_model=reasoning_model
        )
        _llm_service._config_signature = current_signature
    return _llm_service
