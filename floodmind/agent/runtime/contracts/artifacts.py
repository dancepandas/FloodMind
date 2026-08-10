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
