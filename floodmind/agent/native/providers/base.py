"""
Provider 抽象基类。

每个 LLM Provider 只需实现：
1. prepare_request_params() — 添加 provider 特定参数（如 stream_options）
2. extract_usage_from_response() — 从非流式响应提取 usage
3. extract_usage_from_stream_chunk() — 从流式 chunk 提取 usage
4. normalize_usage() — 把 provider-native 格式转成统一 TokenUsage

新增 provider 只需继承 Provider 并实现这 4 个方法。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from .usage import TokenUsage


class Provider(ABC):
    """LLM Provider 抽象基类。"""

    name: str = "base"

    # ── 请求参数 ──────────────────────────────────────────────────

    def prepare_request_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        在发送请求前修改参数。子类可覆盖以添加 provider 特定参数。

        例如 OpenAI-compatible provider 可在此添加 stream_options。
        """
        return params

    # ── Usage 提取 ─────────────────────────────────────────────────

    @abstractmethod
    def extract_usage_from_response(self, response: Any) -> Optional[Dict[str, Any]]:
        """
        从非流式响应对象中提取 provider-native usage 数据。

        Args:
            response: openai 返回的 ChatCompletion 对象

        Returns:
            provider-native usage dict，或 None（provider 没返回 usage）
        """
        ...

    @abstractmethod
    def extract_usage_from_stream_chunk(self, chunk: Any) -> Optional[Dict[str, Any]]:
        """
        从流式 chunk 中提取 provider-native usage 数据。

        Args:
            chunk: 流式响应中的一个 chunk 对象

        Returns:
            provider-native usage dict，或 None（这个 chunk 不含 usage）
        """
        ...

    # ── Usage 归一化 ──────────────────────────────────────────────

    @abstractmethod
    def normalize_usage(self, raw: Dict[str, Any]) -> TokenUsage:
        """
        把 provider-native usage 格式转成统一的 TokenUsage。

        这是唯一需要处理 provider 差异的地方：
        - OpenAI: prompt_tokens / completion_tokens / prompt_tokens_details.cached_tokens
        - Anthropic: input_tokens / cache_read_input_tokens / cache_creation_input_tokens
        - 等等

        Args:
            raw: extract_usage_* 返回的 provider-native dict

        Returns:
            统一的 TokenUsage 对象
        """
        ...

    # ── 便捷方法 ───────────────────────────────────────────────────

    def process_response_usage(self, response: Any) -> Optional[TokenUsage]:
        """从非流式响应提取并归一化 usage。"""
        raw = self.extract_usage_from_response(response)
        if raw is None:
            return None
        return self.normalize_usage(raw)

    def process_stream_chunk_usage(self, chunk: Any) -> Optional[TokenUsage]:
        """从流式 chunk 提取并归一化 usage。"""
        raw = self.extract_usage_from_stream_chunk(chunk)
        if raw is None:
            return None
        return self.normalize_usage(raw)
