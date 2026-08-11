"""Artifact Manifest (target §15)."""

from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from floodmind.agent.runtime.contracts.canonical_events import utcnow


class ArtifactManifest(BaseModel):
    artifact_id: str
    content_sha256: str
    media_type: str = "application/octet-stream"
    size: int = 0
    storage_uri: str
    logical_name: str = ""
    producer_call_id: str = ""
    producer_thread_id: str = ""
    workspace_generation: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    sensitivity: str = "internal"
    verified: bool = False
    supersedes: Optional[str] = None
    retention: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ArtifactDeclaration(BaseModel):
    """一次待发布的 Artifact 声明（§15.2 管线输入）。"""

    logical_name: str
    source_path: str
    media_type: str = ""            # 空则按扩展名分类
    sensitivity: str = "internal"
    producer_call_id: str = ""
    producer_thread_id: str = ""
    supersedes: Optional[str] = None
    retention: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
