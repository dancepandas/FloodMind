"""
Agent Retry Policy — LLM 调用可恢复错误自动重试。

借鉴 OpenCode SessionRetry 模式，提供:
- 可重试错误判断（rate limit, timeout, 503 等）
- 指数退避策略
- 前端事件通知
"""

import logging
import time
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

# 错误消息关键词 → 是否可重试
_RETRYABLE_PATTERNS = [
    "rate limit", "rate exceeded", "429",
    "timeout", "timed out",
    "503", "502", "500",
    "service unavailable", "server error",
    "connection reset", "connection refused",
    "network", "temporary failure",
    "busy", "overloaded",
    "internal error",
]

_NON_RETRYABLE_PATTERNS = [
    "401", "403",
    "arrearage", "quota",
    "余额不足", "欠费",
    "not found", "invalid",
    "unsupported", "not allowed",
]


def is_retryable_error(error: Exception) -> bool:
    """判断 LLM 调用错误是否可重试。

    可重试: rate limit, timeout, 503, 网络波动
    不可重试: 401, 403, 欠费, 模型不存在
    """
    msg = str(error).lower()
    # 先检查不可重试（优先级更高）
    for pattern in _NON_RETRYABLE_PATTERNS:
        if pattern in msg:
            return False
    # 再检查可重试
    for pattern in _RETRYABLE_PATTERNS:
        if pattern in msg:
            return True
    return False


class RetryPolicy:
    """重试策略配置"""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 2.0,
        max_delay: float = 30.0,
        backoff_multiplier: float = 2.0,
        on_retry: Optional[Callable[[int, Exception], None]] = None,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_multiplier = backoff_multiplier
        self.on_retry = on_retry

    def delay_for(self, attempt: int) -> float:
        """计算第 attempt 次重试的等待时间（秒）"""
        return min(
            self.base_delay * (self.backoff_multiplier ** (attempt - 1)),
            self.max_delay,
        )


def with_retry(
    func: Callable,
    policy: RetryPolicy,
    *args,
    **kwargs,
):
    """带自动重试的函数执行包装。

    Args:
        func: 要执行的函数
        policy: 重试策略
        *args, **kwargs: 传递给 func 的参数

    Returns:
        func 的返回值

    Raises:
        最后一次尝试的异常（如果所有重试都失败）
    """
    last_error = None
    for attempt in range(policy.max_retries + 1):  # 0 表示首次尝试
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt >= policy.max_retries:
                logger.error("重试 %d 次后仍失败: %s", policy.max_retries, str(e)[:200])
                raise

            if not is_retryable_error(e):
                logger.warning("不可重试的错误，直接终止: %s", str(e)[:200])
                raise

            delay = policy.delay_for(attempt + 1)
            logger.info(
                "第 %d/%d 次重试，等待 %.1fs，错误: %s",
                attempt + 1, policy.max_retries, delay, str(e)[:100],
            )

            if policy.on_retry:
                try:
                    policy.on_retry(attempt + 1, e)
                except Exception as callback_err:
                    logger.warning("on_retry 回调异常: %s", callback_err)

            time.sleep(delay)

    # 不应到达这里
    raise last_error  # type: ignore
