"""
Runtime Contracts — Execution Journal 协议模型

定义 Agent 执行日志的结构化格式。Journal 用于：
1. 把每轮执行事件（LLM 决策、工具调用、工具结果）结构化归档
2. 长工具结果不进入 prompt，只保留摘要 + 引用
3. 完整历史可查询、可恢复
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class LLMCallJournalEntry(BaseModel):
    """一轮 LLM 调用的摘要。"""

    answer_fragment: str = ""
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)


class ToolResultJournalEntry(BaseModel):
    """一个工具结果的 journal 条目。"""

    tool_call_id: str
    tool_name: str
    status: str
    summary: str
    full_ref: Optional[str] = None
    artifacts: List[str] = Field(default_factory=list)
    inline: bool = True


class TurnJournalEntry(BaseModel):
    """一轮（一次 LLM 调用 + 其触发的工具执行）的 journal 条目。"""

    model_config = ConfigDict(extra="allow")

    turn_index: int
    checkpoint_id: Optional[str] = None
    timestamp: datetime
    llm: LLMCallJournalEntry = Field(default_factory=LLMCallJournalEntry)
    tool_results: List[ToolResultJournalEntry] = Field(default_factory=list)
    token_usage: Dict[str, int] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ArchivedToolResult(BaseModel):
    """归档的完整工具结果。"""

    model_config = ConfigDict(extra="allow")

    ref_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    status: str
    content: str
    artifacts: List[str] = Field(default_factory=list)
    archived_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)
