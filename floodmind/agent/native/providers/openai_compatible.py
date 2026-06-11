"""
OpenAI Compatible Provider。

覆盖所有 OpenAI API 兼容的 provider：
- OpenAI 官方
- DeepSeek
- DashScope (阿里云)
- SiliconFlow
- Groq
- TogetherAI
- 等等

核心假设：这些 provider 的 usage 格式与 OpenAI 一致（prompt_tokens, completion_tokens 等），
只是有的支持 stream_options，有的不支持。不支持时 extract 返回 None，由上层 fallback。
"""

import logging
from typing import Any, Dict, Optional

from .base import Provider
from .usage import TokenUsage

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(Provider):
    """OpenAI 兼容 provider，覆盖绝大多数国内/国际 LLM 服务。"""

    name = "openai-compatible"

    # ── 请求参数 ──────────────────────────────────────────────────

    def prepare_request_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        流式请求添加 stream_options 以获取 usage。
        如果 provider 不支持，会在 API 调用时返回错误，由 ModelClient 捕获处理。
        """
        if params.get("stream"):
            params["stream_options"] = {"include_usage": True}
        return params

    # ── Usage 提取 ─────────────────────────────────────────────────

    def extract_usage_from_response(self, response: Any) -> Optional[Dict[str, Any]]:
        """从非流式响应提取 usage。"""
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        # 支持 Pydantic 对象和 dict
        if hasattr(usage, "model_dump"):
            return usage.model_dump()
        if isinstance(usage, dict):
            return dict(usage)
        return None

    def extract_usage_from_stream_chunk(self, chunk: Any) -> Optional[Dict[str, Any]]:
        """
        从流式 chunk 提取 usage。

        OpenAI 流式协议的约定：最后一个 chunk 的 choices 为空数组，
        此时 usage 字段包含本次请求的完整 token 统计。
        """
        choices = getattr(chunk, "choices", None)
        # choices 为 None 或空列表时，这个 chunk 可能是 usage chunk
        if choices:
            return None
        usage = getattr(chunk, "usage", None)
        if usage is None:
            return None
        if hasattr(usage, "model_dump"):
            return usage.model_dump()
        if isinstance(usage, dict):
            return dict(usage)
        return None

    # ── Usage 归一化 ──────────────────────────────────────────────

    def normalize_usage(self, raw: Dict[str, Any]) -> TokenUsage:
        """
        把 OpenAI 格式 usage 转成统一 TokenUsage。

        OpenAI 格式：
        {
            "prompt_tokens": 123,
            "completion_tokens": 456,
            "total_tokens": 579,
            "completion_tokens_details": {"reasoning_tokens": 100},
            "prompt_tokens_details": {"cached_tokens": 50}
        }

        注意：OpenAI 的 prompt_tokens 是**包含 cache 的总量**。
        我们将其作为 prompt_tokens，cache 信息放在 breakdown 字段。
        """
        prompt_tokens = raw.get("prompt_tokens") or raw.get("input_tokens") or 0
        completion_tokens = raw.get("completion_tokens") or raw.get("output_tokens") or 0
        total_tokens = raw.get("total_tokens") or (prompt_tokens + completion_tokens)

        # reasoning_tokens: DeepSeek / OpenAI o1 / Claude thinking
        reasoning_tokens: Optional[int] = None
        _completion_details = raw.get("completion_tokens_details")
        if _completion_details:
            reasoning_tokens = _completion_details.get("reasoning_tokens")

        # cache_read_tokens: Anthropic cache_read_input_tokens / OpenAI cached_tokens
        cache_read_tokens: Optional[int] = None
        _prompt_details = raw.get("prompt_tokens_details")
        if _prompt_details:
            cache_read_tokens = _prompt_details.get("cached_tokens")
        if cache_read_tokens is None:
            cache_read_tokens = raw.get("cache_read_input_tokens")
        if cache_read_tokens is None:
            cache_read_tokens = raw.get("cached_tokens")

        # cache_write_tokens: Anthropic cache_creation_input_tokens
        cache_write_tokens: Optional[int] = None
        if _prompt_details:
            cache_write_tokens = _prompt_details.get("cache_creation_input_tokens")
        if cache_write_tokens is None:
            cache_write_tokens = raw.get("cache_creation_input_tokens")
        if cache_write_tokens is None:
            cache_write_tokens = raw.get("cache_write_input_tokens")

        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            provider_metadata=dict(raw),
        )
