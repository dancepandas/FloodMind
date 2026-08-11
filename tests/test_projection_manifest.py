from floodmind.agent.native.capabilities import ModelCapabilities
from floodmind.agent.native.projection import build_manifest, compute_input_budget
from floodmind.agent.runtime.contracts.projection import (
    InputBudget, ProjectionManifest, ProjectionSource,
)


def test_projection_manifest_defaults():
    m = ProjectionManifest()
    assert m.projection_id == ""
    assert m.model_call_id == ""
    assert m.projection_version == "1"
    assert m.model == ""
    assert m.codec_version == ""
    assert m.capability_snapshot_id == ""
    assert isinstance(m.budget, InputBudget)
    assert m.budget.effective_input == 0
    assert m.sources == []
    assert m.tool_registry_version == ""
    assert m.skill_catalog_version == ""
    assert m.total_projected_tokens == 0
    assert m.projection_sha256 == ""


def test_build_manifest_propagates_metadata_and_coerces_dict_sources():
    budget = compute_input_budget(ModelCapabilities(context_window=32768))
    m = build_manifest(
        model="m", codec_version="2", capability_snapshot_id="cap_9",
        budget=budget,
        sources=[
            {"source_id": "s1", "source_type": "episode", "content_sha256": "h",
             "original_tokens": 5, "projected_tokens": 3, "transform": "identity",
             "priority": 1, "selected": True},
        ],
        tool_registry_version="tr_1", skill_catalog_version="sc_2", model_call_id="call_3")
    assert m.model == "m"
    assert m.codec_version == "2"
    assert m.capability_snapshot_id == "cap_9"
    assert m.budget is budget
    assert m.tool_registry_version == "tr_1"
    assert m.skill_catalog_version == "sc_2"
    assert m.model_call_id == "call_3"
    assert m.total_projected_tokens == 3
    assert len(m.sources) == 1
    assert isinstance(m.sources[0], ProjectionSource)
    assert m.sources[0].source_id == "s1"
    assert m.sources[0].source_type == "episode"
    assert m.sources[0].projected_tokens == 3
    assert m.projection_id.startswith("ctx_")
    assert len(m.projection_sha256) == 64


def test_projection_id_unique_but_sha256_input_derived():
    m1 = build_manifest(model="gpt-x", codec_version="1", capability_snapshot_id="cap_1",
                        budget=compute_input_budget(ModelCapabilities(context_window=8192)),
                        sources=[ProjectionSource(source_id="s1", source_type="episode",
                                                 content_sha256="h", original_tokens=10,
                                                 projected_tokens=10, transform="identity",
                                                 priority=1, selected=True)])
    m2 = build_manifest(model="gpt-x", codec_version="1", capability_snapshot_id="cap_1",
                        budget=compute_input_budget(ModelCapabilities(context_window=8192)),
                        sources=[ProjectionSource(source_id="s1", source_type="episode",
                                                 content_sha256="h", original_tokens=10,
                                                 projected_tokens=10, transform="identity",
                                                 priority=1, selected=True)])
    assert m1.projection_id != m2.projection_id  # ids are per-call unique
    assert m1.projection_sha256 == m2.projection_sha256  # but hash derives only from inputs
