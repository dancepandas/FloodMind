"""Handoff 控制权移交：目标 Agent 接管同一 run 的后续 LLM 调用。

区别于 SubAgent：SubAgent 同步跑完并把结果作为工具输出返回主 Agent；Handoff
调用后不返回中间结果，目标 Agent 的 ModelClient/system_prompt/tool registry/
tool executor 接管当前 loop，直到本次 run 终止。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional


def default_handoff_history_filter(messages: List[dict]) -> List[dict]:
    """默认历史过滤：保留 system 消息，把移交前对话压为一条 assistant 摘要。

    为防上下文膨胀，仅保留各消息的可见文本；图片/工具 schema 等结构化块不复制。
    """
    system = [dict(m) for m in messages if m.get("role") == "system"]
    lines = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if content:
            lines.append(f"{role}: {content}")
    if lines:
        system.append({
            "role": "assistant",
            "content": "【移交前对话摘要】\n" + "\n".join(lines[-20:]),
        })
    return system


@dataclass(frozen=True)
class HandoffTarget:
    """模型可调用的 handoff 目标。

    ``agent`` 为另一个 ``floodmind.Agent``（或其 ``raw`` NativeFloodAgent）；
    ``history_filter`` 接收当前 messages，返回交给目标的消息列表。
    """
    agent: Any
    tool_name: str = ""
    description: str = ""
    name: str = ""
    history_filter: Optional[Callable[[List[dict]], List[dict]]] = None

    @property
    def resolved_name(self) -> str:
        return self.name or getattr(self.agent, "session_id", "") or "agent"

    @property
    def resolved_tool_name(self) -> str:
        if self.tool_name:
            return self.tool_name
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in self.resolved_name)
        return f"handoff_to_{safe}"

    @property
    def resolved_description(self) -> str:
        return self.description or f"将当前对话控制权移交给 {self.resolved_name}。移交后由该 Agent 接管本次运行。"

    def filter_history(self, messages: List[dict]) -> List[dict]:
        fn = self.history_filter or default_handoff_history_filter
        return fn(messages)
