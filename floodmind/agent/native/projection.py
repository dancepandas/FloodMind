"""输入预算（§9.3）+ Projection Manifest 构建（§9.2）。纯函数，无 I/O/时间/随机（除投影 id）。"""

import hashlib
import uuid

from floodmind.agent.native.capabilities import ModelCapabilities
from floodmind.agent.runtime.contracts.canonical_events import canonical_json
from floodmind.agent.runtime.contracts.projection import (
    InputBudget, ProjectionManifest, ProjectionSource,
)

# §9.3 预算项默认值（不得对所有模型固定减 16K；context_limit 取 capabilities）
_DEFAULT_RESERVED_OUTPUT = 16384
_DEFAULT_RESERVED_TOOLS = 8000
_DEFAULT_PROVIDER_OVERHEAD = 2048
_DEFAULT_SAFETY_MARGIN = 4096


def compute_input_budget(
    capabilities: ModelCapabilities,
    *,
    reserved_output: int = _DEFAULT_RESERVED_OUTPUT,
    reserved_tools: int = _DEFAULT_RESERVED_TOOLS,
    provider_overhead: int = _DEFAULT_PROVIDER_OVERHEAD,
    safety_margin: int = _DEFAULT_SAFETY_MARGIN,
) -> InputBudget:
    context_limit = capabilities.context_window or 0
    effective = max(0, context_limit - reserved_output - reserved_tools
                    - provider_overhead - safety_margin)
    return InputBudget(context_limit=context_limit, reserved_output=reserved_output,
                       reserved_tools=reserved_tools, provider_overhead=provider_overhead,
                       safety_margin=safety_margin, effective_input=effective)


def build_manifest(
    *,
    model: str,
    codec_version: str,
    capability_snapshot_id: str,
    budget: InputBudget,
    sources: list,
    tool_registry_version: str = "",
    skill_catalog_version: str = "",
    model_call_id: str = "",
) -> ProjectionManifest:
    manifest = ProjectionManifest(
        projection_id=f"ctx_{uuid.uuid4().hex}",
        model_call_id=model_call_id,
        model=model,
        codec_version=codec_version,
        capability_snapshot_id=capability_snapshot_id,
        budget=budget,
        sources=[s if isinstance(s, ProjectionSource) else ProjectionSource(**s) for s in sources],
        tool_registry_version=tool_registry_version,
        skill_catalog_version=skill_catalog_version,
        total_projected_tokens=sum(
            (s.projected_tokens if isinstance(s, ProjectionSource) else s.get("projected_tokens", 0))
            for s in sources),
    )
    # §9.2 sha256 只依赖输入：排除非确定的 projection_id 与自指的 projection_sha256。
    manifest.projection_sha256 = hashlib.sha256(
        canonical_json(manifest.model_dump(exclude={"projection_id", "projection_sha256"})).encode("utf-8")
    ).hexdigest()
    return manifest
