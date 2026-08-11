from floodmind.agent.runtime.services.provenance import (
    Provenance, attach_provenance, source_sha256, to_projection_source,
)


def test_provenance_attached_and_hashed():
    wrapped = attach_provenance("project instruction", source_type="agents", source_id="ag_1", version="v3")
    assert wrapped["__provenance"].source_type == "agents"
    assert wrapped["__provenance"].content_sha256 == source_sha256("project instruction")
    assert source_sha256("a") != source_sha256("b")


def test_provenance_to_projection_source():
    prov = Provenance(source_type="soul", source_id="soul_1", version="v1",
                      content_sha256=source_sha256("x"))
    ps = to_projection_source(prov, original_tokens=10, projected_tokens=10)
    assert ps.source_type == "soul" and ps.source_id == "soul_1"
