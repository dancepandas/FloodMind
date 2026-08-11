from floodmind.agent.runtime.contracts.context_manifest import (
    ProjectionManifest, ProjectionSource, projection_sha256,
)


def test_manifest_roundtrip_and_hash_deterministic():
    m1 = ProjectionManifest(
        projection_id="ctx_1", model_call_id="call_1",
        sources=[ProjectionSource(source_id="s1", source_type="agents",
                                  content_sha256="a", original_tokens=10,
                                  projected_tokens=10)],
    )
    m2 = ProjectionManifest.model_validate_json(m1.model_dump_json())
    assert projection_sha256(m1) == projection_sha256(m2)
    assert len(projection_sha256(m1)) == 64


def test_budget_fields():
    m = ProjectionManifest(projection_id="ctx_2", model_call_id="call_2")
    assert m.total_projected_tokens == 0
