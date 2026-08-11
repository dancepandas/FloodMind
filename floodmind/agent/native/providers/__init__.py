"""Provider codec 注册与自动路由。

``route_codec()`` 按打分选最优 codec：
base_url 精确(100) > provider id(60) > 模型名前缀(40) > OpenAI 兜底。

- **连线方言看 base_url/provider**（请求怎么发）
- **模型个性看模型名**（codec 内部按 model 分支）
- 仅模型名前缀命中（如聚合网关托管 ``MiniMax/xxx``）→ ``conservative=True``：
  解析适配全部启用，请求适配退化为标准 OpenAI 行为，避免网关不认厂商方言参数。

新增厂商：实现一个 ProviderCodec 子类并加入 ``_CODECS`` 即可。
"""

from typing import Optional

from .base import ProviderCodec, StreamState
from .dashscope import DashScopeCodec
from .deepseek import DeepSeekCodec
from .kimi import KimiCodec
from .minimax import MiniMaxCodec
from .openai_compatible import OpenAICompatibleCodec
from .usage import TokenUsage

__all__ = [
    "ProviderCodec",
    "StreamState",
    "TokenUsage",
    "OpenAICompatibleCodec",
    "DashScopeCodec",
    "DeepSeekCodec",
    "KimiCodec",
    "MiniMaxCodec",
    "route_codec",
]

# 专属 codec 注册表（打分制，顺序无关）
_CODECS = [
    DashScopeCodec,
    DeepSeekCodec,
    KimiCodec,
    MiniMaxCodec,
]

# 模型名前缀命中的分数阈值：≤ 此分即视为「连线方言未知」，请求适配保守化
_CONSERVATIVE_THRESHOLD = 40


def route_codec(
    provider_id: Optional[str] = None,
    model_id: Optional[str] = None,
    base_url: Optional[str] = None,
) -> ProviderCodec:
    """按 provider id / 模型名 / base_url 自动路由到最佳 codec。"""
    provider_id = provider_id or ""
    model_id = model_id or ""
    base_url = base_url or ""

    best_cls = None
    best_score = 0
    for cls in _CODECS:
        score = cls.match(provider_id, model_id, base_url)
        if score > best_score:
            best_cls, best_score = cls, score

    if best_cls is None:
        codec = OpenAICompatibleCodec()
    else:
        codec = best_cls()
        if best_score <= _CONSERVATIVE_THRESHOLD:
            codec.conservative = True
    codec.provider_id = provider_id
    codec.model_id = model_id
    codec.base_url = base_url
    return codec
