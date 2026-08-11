"""ArtifactService traversal and manifest trust-boundary tests."""
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.artifacts import ArtifactManifest
from floodmind.agent.runtime.services.artifact_service import ArtifactService


def test_artifact_id_traversal_rejected(tmp_path):
    svc = ArtifactService(tmp_path / "artifacts")
    for operation, artifact_id in (
        (svc.resolve, "../../etc/passwd"),
        (svc.read_path, "../x"),
        (svc.verify, "../x"),
        (svc.delete, ".."),
    ):
        with pytest.raises(ValueError):
            operation(artifact_id)


def test_manifest_storage_uri_escape_rejected(tmp_path):
    store = tmp_path / "artifacts"
    svc = ArtifactService(store)
    artifact_id = "art_" + "a" * 28
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    manifest = ArtifactManifest(
        artifact_id=artifact_id,
        content_sha256="0" * 64,
        media_type="text/plain",
        size=6,
        storage_uri=str(outside),
        logical_name="outside.txt",
    )
    (store / "manifests" / f"{artifact_id}.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )

    with pytest.raises(ValueError):
        svc.resolve(artifact_id)
    with pytest.raises(ValueError):
        svc.read_path(artifact_id)
    with pytest.raises(ValueError):
        svc.verify(artifact_id)
    with pytest.raises(ValueError):
        svc.delete(artifact_id)
    assert outside.exists()
