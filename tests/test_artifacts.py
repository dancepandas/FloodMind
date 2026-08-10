from floodmind.agent.runtime.contracts.artifacts import ArtifactManifest


def test_manifest_defaults():
    m = ArtifactManifest(artifact_id="art_1", content_sha256="abc",
                         storage_uri="file:///a.json")
    assert m.media_type == "application/octet-stream"
    assert m.verified is False
    assert m.supersedes is None


def test_manifest_required_fields():
    m = ArtifactManifest(artifact_id="art_2", content_sha256="def",
                         storage_uri="file:///b.json", logical_name="forecast.json")
    assert m.logical_name == "forecast.json"
