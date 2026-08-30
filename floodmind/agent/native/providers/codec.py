"""ProviderCodec（目标 §7.3）：Canonical Request↔Provider、Provider Raw Event→Canonical Parts。

由原 ProviderPipeline 收敛重命名而来：职责不变，接口按 §7.3 对齐。
"""

import json
from typing import Any, Dict, Iterator, List, Optional, Tuple

from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


class ProviderCodec:
    name: str = "openai"

    def __init__(self, name: str = "openai"):
        self.name = name

    @classmethod
    def match(cls, provider_id: str, model_id: str, base_url: str) -> int:
        return 0

    def encode_request(self, params: Dict[str, Any], *, enable_thinking: bool = False, stream: bool = False) -> Dict[str, Any]:
        """Canonical Request → Provider Request（厂商方言参数由子类 setdefault 注入）。"""
        return dict(params)

    def decode_chunk(self, raw_chunk: Any) -> Iterator[CanonicalPart]:
        """Provider Raw Event → Canonical Parts（保留原生块 raw）。

        usage-only 末帧（choices=[] + 顶层 usage，标准 usage 位置）也必须产出
        usage part——先于 no-choices 守卫检查 usage，避免 §25.2 usage-only final
        chunk 被吞成 error。
        """
        if raw_chunk is None:
            yield CanonicalPart(event="error", text="no chunk")
            return
        usage = getattr(raw_chunk, "usage", None)
        if usage is not None:
            yield CanonicalPart(event="usage", kind="text",
                                text=json.dumps(_usage_dict(usage), ensure_ascii=False),
                                raw=_asdict(raw_chunk))
        if not getattr(raw_chunk, "choices", None):
            if usage is None:
                yield CanonicalPart(event="error", text="no choices")
            return
        choice = raw_chunk.choices[0]
        delta = getattr(choice, "delta", None) or type("D", (), {})()
        if getattr(delta, "content", None):
            yield CanonicalPart(event="text_delta", kind="text", text=str(delta.content),
                                raw=_asdict(raw_chunk))
        reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if reasoning:
            yield CanonicalPart(event="reasoning_delta", kind="provider_reasoning",
                                text=str(reasoning), raw=_asdict(raw_chunk))
        tool_calls = getattr(delta, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                func = getattr(tc, "function", None) or type("F", (), {})()
                try:
                    index = int(getattr(tc, "index", 0))
                except (TypeError, ValueError):
                    # 部分 provider 的 index 为 None/非 int：回退 0，避免整轮流中断
                    index = 0
                yield CanonicalPart(
                    event="tool_call_delta", kind="tool_call", index=index,
                    id=getattr(tc, "id", "") or "", name=getattr(func, "name", "") or "",
                    arguments=getattr(func, "arguments", "") or "",
                    raw=_asdict(raw_chunk))
        finish = getattr(choice, "finish_reason", None)
        if finish is not None:
            yield CanonicalPart(event="response_end", kind="text", text=str(finish),
                                raw=_asdict(raw_chunk))

    def decode_message(self, message: Any) -> Tuple[str, str, dict]:
        content = str(getattr(getattr(message, "content", None), "content", "") or getattr(message, "content", "") or "")
        reasoning = ""
        usage = {}
        return content, reasoning, usage

    def extract_usage(self, raw: Any) -> Optional[Dict[str, int]]:
        usage = getattr(raw, "usage", None)
        return _usage_dict(usage) if usage is not None else None


def _asdict(raw: Any) -> dict:
    try:
        return getattr(raw, "model_dump", lambda: {})() if hasattr(raw, "model_dump") else vars(raw)
    except Exception:
        return {}


def _usage_dict(usage: Any) -> dict:
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if isinstance(usage, dict):
        return {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
