"""
Runtime Contracts — SubAgent 协议模型

定义子代理（Specialist）完成子任务后返回给父代理（Orchestrator）的结构化报告。
目标：父代理只接收摘要，不接收子代理完整执行过程。
"""

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class SubAgentReport(BaseModel):
    """子代理执行报告。"""

    model_config = ConfigDict(extra="allow")

    summary: str
    completed: bool = False
    outputs: Dict[str, Any] = Field(default_factory=dict)
    artifacts: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    needs_human: bool = False
    sub_session_id: str = ""
    tool_result_summaries: List[Dict[str, Any]] = Field(default_factory=list)

    def to_payload(self) -> Dict[str, Any]:
        """返回给父代理 LLM 的精简 payload。"""
        return {
            "summary": self.summary,
            "completed": self.completed,
            "artifacts": self.artifacts,
            "next_steps": self.next_steps,
            "needs_human": self.needs_human,
        }
