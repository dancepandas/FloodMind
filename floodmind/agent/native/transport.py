"""ModelTransport（目标 §7.2/§7.7）：纯 Wire I/O + Retry Advice。"""

import ipaddress
from typing import Any, Dict, Iterator, List, Optional, Protocol
from urllib.parse import urlparse

import httpx
import openai
from pydantic import BaseModel

from floodmind.agent.native.retry import is_retryable_error


def should_trust_env(base_url: str) -> bool:
    """判断是否让 httpx 读取系统代理设置。

    私有网段与回环地址默认绕过系统代理：Windows 的 Internet 选项代理会
    被 httpx 读取，内网模型端点经代理转发会返回 502；公网地址保持原行为。
    需要代理访问内网时，显式传 ``trust_env=True``。
    """
    try:
        host = (urlparse(base_url).hostname or "").strip().lower()
    except ValueError:
        return True
    if not host:
        return True
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (addr.is_private or addr.is_loopback or addr.is_link_local)


class TransportRetryAdvice(BaseModel):
    retry_suggested: bool = False
    retry_after: float = 0.0
    response_started: bool = False
    replay_safe: bool = False
    normalized_error: str = ""


class RawResponse:
    def __init__(self, chunks: Iterator[Any], response: Any = None):
        self._chunks = chunks
        # 底层 HTTP 响应（openai Stream.response）：保留给上层做协议健全性检查。
        self.response = response

    def chunks(self) -> Iterator[Any]:
        return self._chunks

    def content_type(self) -> str:
        headers = getattr(self.response, "headers", None)
        if headers is None:
            return ""
        return str(headers.get("content-type", "")).lower()


class ModelTransport(Protocol):
    def send(self, request: dict) -> RawResponse: ...
    def classify_error(self, exc: Exception) -> TransportRetryAdvice: ...


class OpenAIChatTransport:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: int = 90,
        trust_env: Optional[bool] = None,
    ):
        if trust_env is None:
            trust_env = should_trust_env(base_url)
        self._http_client = httpx.Client(trust_env=trust_env, timeout=timeout)
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            http_client=self._http_client,
        )

    def send(self, request: dict) -> RawResponse:
        result = self._client.chat.completions.create(**request)
        # 流式调用返回 openai.Stream，其 .response 是底层 httpx.Response。
        return RawResponse(result, response=getattr(result, "response", None))

    def classify_error(self, exc: Exception) -> TransportRetryAdvice:
        if is_retryable_error(exc):
            return TransportRetryAdvice(retry_suggested=True, response_started=False,
                                        normalized_error=str(exc))
        return TransportRetryAdvice(retry_suggested=False, normalized_error=str(exc))
