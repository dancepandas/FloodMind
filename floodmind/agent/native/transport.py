"""ModelTransport（目标 §7.2/§7.7）：纯 Wire I/O + Retry Advice。"""

from typing import Any, Dict, Iterator, List, Protocol

import httpx
import openai
from pydantic import BaseModel

from floodmind.agent.native.retry import is_retryable_error


class TransportRetryAdvice(BaseModel):
    retry_suggested: bool = False
    retry_after: float = 0.0
    response_started: bool = False
    replay_safe: bool = False
    normalized_error: str = ""


class RawResponse:
    def __init__(self, chunks: Iterator[Any]):
        self._chunks = chunks

    def chunks(self) -> Iterator[Any]:
        return self._chunks


class ModelTransport(Protocol):
    def send(self, request: dict) -> RawResponse: ...
    def classify_error(self, exc: Exception) -> TransportRetryAdvice: ...


class OpenAIChatTransport:
    def __init__(self, api_key: str, base_url: str, timeout: int = 90):
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def send(self, request: dict) -> RawResponse:
        return RawResponse(self._client.chat.completions.create(**request))

    def classify_error(self, exc: Exception) -> TransportRetryAdvice:
        if is_retryable_error(exc):
            return TransportRetryAdvice(retry_suggested=True, response_started=False,
                                        normalized_error=str(exc))
        return TransportRetryAdvice(retry_suggested=False, normalized_error=str(exc))
