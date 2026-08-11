"""Provenance（目标 §8.3）：所有注入模型的非用户原文携带来源。纯函数。"""

import hashlib

from pydantic import BaseModel

from floodmind.agent.runtime.contracts.canonical_events import canonical_json
from floodmind.agent.runtime.contracts.projection import ProjectionSource


class Provenance(BaseModel):
    source_type: str = ""   # soul|agents|core|episode|retrieval|skill|tool
    source_id: str = ""
    content_sha256: str = ""
    version: str = ""
    transform: str = "identity"


def source_sha256(content: str) -> str:
    return hashlib.sha256(canonical_json({"content": content}).encode("utf-8")).hexdigest()


def attach_provenance(content: str, *, source_type: str, source_id: str, version: str = "") -> dict:
    return {"__provenance": Provenance(
        source_type=source_type, source_id=source_id,
        content_sha256=source_sha256(content), version=version), "content": content}


def to_projection_source(prov: Provenance, *, original_tokens: int, projected_tokens: int) -> ProjectionSource:
    return ProjectionSource(source_id=prov.source_id, source_type=prov.source_type,
                            content_sha256=prov.content_sha256,
                            original_tokens=original_tokens, projected_tokens=projected_tokens,
                            transform=prov.transform, priority=1, selected=True)
