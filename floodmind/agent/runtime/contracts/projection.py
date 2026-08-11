"""Projection Manifest / Input Budget（目标 §9.2/§9.3）。纯数据层。"""

from typing import List

from pydantic import BaseModel


class ProjectionSource(BaseModel):
    source_id: str = ""
    source_type: str = ""          # soul|agents|core|episode|retrieval|skill|tool
    content_sha256: str = ""
    original_tokens: int = 0
    projected_tokens: int = 0
    transform: str = "identity"    # identity|compact|offload|summary
    priority: int = 0
    selected: bool = True


class InputBudget(BaseModel):
    context_limit: int = 0
    reserved_output: int = 0
    reserved_tools: int = 0
    provider_overhead: int = 0
    safety_margin: int = 0
    effective_input: int = 0


class ProjectionManifest(BaseModel):
    projection_id: str = ""
    model_call_id: str = ""
    projection_version: str = "1"
    model: str = ""
    codec_version: str = ""
    capability_snapshot_id: str = ""
    budget: InputBudget = InputBudget()
    sources: List[ProjectionSource] = []
    tool_registry_version: str = ""
    skill_catalog_version: str = ""
    total_projected_tokens: int = 0
    projection_sha256: str = ""
