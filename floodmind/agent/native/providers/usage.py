"""
统一的 Token Usage Schema。

所有 Provider 的 usage 数据归一化到此格式，便于上层统一处理。
设计原则（借鉴 OpenCode）：
- 使用 inclusive totals（总量包含 breakdown）
- breakdown 字段 optional，缺失时为 None 而非 0
- 保留 provider_metadata 用于审计和调试
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TokenUsage:
    """LLM 调用的 token 用量统计。

    字段说明：
    - prompt_tokens: 输入 token 总量（包含 cache read/write，如果 provider 报告的是总量）
    - completion_tokens: 输出 token 总量（包含 reasoning）
    - total_tokens: provider 报告的总 token（如果缺失则自动计算为 prompt + completion）
    - reasoning_tokens: 推理/思维链 token 数（subset of completion_tokens）
    - cache_read_tokens: 从缓存读取的输入 token 数
    - cache_write_tokens: 写入缓存的输入 token 数
    - provider_metadata: provider 原始 usage payload（用于审计）
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    provider_metadata: Optional[Dict[str, Any]] = field(default=None, repr=False)

    @property
    def visible_completion_tokens(self) -> int:
        """用户可见的输出 token 数（排除 reasoning）。"""
        if self.reasoning_tokens is None:
            return self.completion_tokens
        return max(0, self.completion_tokens - self.reasoning_tokens)

    @property
    def non_cached_prompt_tokens(self) -> int:
        """非缓存的输入 token 数。"""
        cached = (self.cache_read_tokens or 0) + (self.cache_write_tokens or 0)
        return max(0, self.prompt_tokens - cached)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict，用于 JSON 传输。"""
        result: Dict[str, Any] = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }
        if self.reasoning_tokens is not None:
            result["reasoning_tokens"] = self.reasoning_tokens
        if self.cache_read_tokens is not None:
            result["cache_read_tokens"] = self.cache_read_tokens
        if self.cache_write_tokens is not None:
            result["cache_write_tokens"] = self.cache_write_tokens
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenUsage":
        """从 dict 反序列化。"""
        return cls(
            prompt_tokens=data.get("prompt_tokens", 0) or 0,
            completion_tokens=data.get("completion_tokens", 0) or 0,
            total_tokens=data.get("total_tokens", 0) or 0,
            reasoning_tokens=data.get("reasoning_tokens"),
            cache_read_tokens=data.get("cache_read_tokens"),
            cache_write_tokens=data.get("cache_write_tokens"),
            provider_metadata=data.get("_provider_metadata"),
        )
