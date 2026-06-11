"""
Provider 注册和管理。

通过 protocol 字段或 base_url 自动推断使用哪个 Provider，也支持显式指定。
"""

from typing import Optional

from .base import Provider
from .openai_compatible import OpenAICompatibleProvider
from .usage import TokenUsage

__all__ = ["Provider", "TokenUsage", "get_provider"]


# Provider 注册表：protocol 名 → Provider 类
# 新增 provider 只需在此注册即可
_PROVIDER_REGISTRY = {
    "openai-compatible": OpenAICompatibleProvider,
    # 未来扩展：
    # "anthropic-messages": AnthropicProvider,
    # "google-gemini": GoogleProvider,
    # "bedrock-converse": BedrockProvider,
}


def get_provider(
    base_url: Optional[str] = None,
    provider_name: Optional[str] = None,
    protocol: Optional[str] = None,
) -> Provider:
    """
    获取对应的 Provider 实例。

    优先级：protocol 显式指定 > provider_name 推断 > base_url 推断 > 默认 openai-compatible

    Args:
        base_url: API base URL，用于自动推断
        provider_name: provider 配置名（如 "dashscope", "deepseek"）
        protocol: 显式指定协议名（如 "openai-compatible", "anthropic-messages"）

    Returns:
        Provider 实例
    """
    # 1. protocol 显式指定（最高优先级）
    if protocol:
        provider_cls = _PROVIDER_REGISTRY.get(protocol)
        if provider_cls:
            return provider_cls()
        raise ValueError(f"未知的 provider protocol: {protocol}，已知: {list(_PROVIDER_REGISTRY.keys())}")

    # 2. provider_name 推断（未来可扩展）
    if provider_name:
        # 未来可根据 provider_name 推断 protocol
        # 如 provider_name == "anthropic" 返回 AnthropicProvider()
        pass

    # 3. base_url 推断（未来可扩展）
    if base_url:
        # 未来可根据 base_url 关键词匹配特定 provider
        # 如 "anthropic.com" 返回 AnthropicProvider()
        pass

    # 4. 默认使用 OpenAI 兼容 provider（覆盖绝大多数场景）
    return OpenAICompatibleProvider()
