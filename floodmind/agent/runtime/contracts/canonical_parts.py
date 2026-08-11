"""Canonical Part 事件与类型（目标 §7.5）。纯数据层。"""

from typing import Dict, List

from pydantic import BaseModel

PART_EVENT_TYPES: List[str] = ["response_start", "part_start", "text_delta", "reasoning_delta",
                               "tool_call_delta", "part_end", "usage", "response_end", "error"]
PART_TYPES: List[str] = ["text", "reasoning_summary", "provider_reasoning", "tool_call",
                         "refusal", "compaction", "provider_extension"]


class CanonicalPart(BaseModel):
    event: str                       # PART_EVENT_TYPES 之一
    kind: str = ""                   # PART_TYPES 之一
    index: int = 0
    id: str = ""                     # tool_call 的 provider id（跨 chunk 首个非空帧）
    text: str = ""
    name: str = ""
    arguments: str = ""
    arguments_sha256: str = ""
    raw: Dict = {}                   # provider 原生块（replay/审计保留）
