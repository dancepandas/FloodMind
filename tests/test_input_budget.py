from floodmind.agent.native.capabilities import ModelCapabilities
from floodmind.agent.native.projection import build_manifest, compute_input_budget
from floodmind.agent.runtime.contracts.projection import ProjectionSource


def test_input_budget_uses_capability_context_window():
    caps = ModelCapabilities(context_window=131072)
    b = compute_input_budget(caps)
    assert b.effective_input == 131072 - 16384 - 8000 - 2048 - 4096


def test_manifest_sha256_deterministic():
    m1 = build_manifest(model="gpt-x", codec_version="1",
                        capability_snapshot_id="cap_1",
                        budget=compute_input_budget(ModelCapabilities(context_window=8192)),
                        sources=[ProjectionSource(source_id="s1", source_type="episode",
                                                 content_sha256="h", original_tokens=10,
                                                 projected_tokens=10, transform="identity",
                                                 priority=1, selected=True)])
    m2 = build_manifest(model="gpt-x", codec_version="1",
                        capability_snapshot_id="cap_1",
                        budget=compute_input_budget(ModelCapabilities(context_window=8192)),
                        sources=[ProjectionSource(source_id="s1", source_type="episode",
                                                 content_sha256="h", original_tokens=10,
                                                 projected_tokens=10, transform="identity",
                                                 priority=1, selected=True)])
    assert m1.projection_sha256 == m2.projection_sha256
    assert m1.total_projected_tokens == 10
    assert m1.projection_id.startswith("ctx_")
