"""Canonical model part events (target §7.5)."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel


class PartType(str, Enum):
    text = "text"
    reasoning_summary = "reasoning_summary"
    provider_reasoning = "provider_reasoning"
    tool_call = "tool_call"
    refusal = "refusal"
    compaction = "compaction"
    provider_extension = "provider_extension"


PART_EVENT_TYPES = (
    "response_start", "part_start", "text_delta", "reasoning_delta",
    "tool_call_delta", "part_end", "usage", "response_end", "error",
)


class PartStartEvent(BaseModel):
    type: Literal["part_start"] = "part_start"
    part_id: str
    part_type: PartType


class PartDeltaEvent(BaseModel):
    type: Literal["part_delta"] = "part_delta"
    part_id: str
    text: str = ""


class PartEndEvent(BaseModel):
    type: Literal["part_end"] = "part_end"
    part_id: str


class UsageRecorded(BaseModel):
    type: Literal["usage"] = "usage"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ResponseEndEvent(BaseModel):
    type: Literal["response_end"] = "response_end"
    terminal_reason_code: str
    terminal_reason_raw: str = ""
