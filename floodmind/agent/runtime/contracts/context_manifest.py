"""Context Projection Manifest (target §9.2)."""

import hashlib
from typing import List
from pydantic import BaseModel, Field

from floodmind.agent.runtime.contracts.canonical_events import canonical_json


class ProjectionSource(BaseModel):
    source_id: str
    source_type: str = "episode"
    content_sha256: str = ""
    original_tokens: int = 0
    projected_tokens: int = 0
    transform: str = "identity"
    priority: int = 0
    selected: bool = True


class ProjectionManifest(BaseModel):
    projection_id: str
    model_call_id: str
    projection_version: str = "1"
    model: str = ""
    codec_version: str = ""
    capability_snapshot_id: str = ""
    sources: List[ProjectionSource] = Field(default_factory=list)
    tool_registry_version: str = ""
    skill_catalog_version: str = ""
    total_projected_tokens: int = 0
    projection_sha256: str = ""


def projection_sha256(manifest: ProjectionManifest) -> str:
    return hashlib.sha256(canonical_json(manifest.model_dump()).encode("utf-8")).hexdigest()
