"""Tests for ArtifactWatcher managed workspace exclusions."""

from floodmind.agent.native.artifact_watcher import ArtifactWatcher
from floodmind.agent.runtime.services.workspace_service import build_folder_workspace


def test_artifact_watcher_ignores_floodmind_managed_tree(tmp_path):
    watcher = ArtifactWatcher(output_dir=str(tmp_path))
    watcher.take_snapshot()

    report = tmp_path / "report.md"
    report.write_text("ok", encoding="utf-8")
    managed = tmp_path / ".floodmind" / "artifacts" / "s1" / "internal.md"
    managed.parent.mkdir(parents=True)
    managed.write_text("internal", encoding="utf-8")

    artifacts = watcher.detect_new_artifacts()

    assert [a.file_name for a in artifacts] == ["report.md"]


def test_verify_artifact_rejects_floodmind_file(tmp_path):
    managed = tmp_path / ".floodmind" / "artifacts" / "s1" / "internal.md"
    managed.parent.mkdir(parents=True)
    managed.write_text("internal", encoding="utf-8")

    watcher = ArtifactWatcher(output_dir=str(tmp_path))

    assert watcher.verify_artifact_exists(".floodmind/artifacts/s1/internal.md") is None


def test_artifact_watcher_detects_files_inside_floodmind_artifact_dir(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    watcher = ArtifactWatcher(output_dir=str(ws.artifact_dir), ignore_managed_dirs=False)
    watcher.take_snapshot()

    report = ws.artifact_dir / "report.md"
    report.write_text("done", encoding="utf-8")

    records = watcher.detect_new_artifacts()

    assert len(records) == 1
    assert records[0].file_name == "report.md"
    assert records[0].file_path == str(report)
    assert records[0].metadata["source"] == "artifact_dir_watcher"
    assert records[0].metadata["relative_path"] == "report.md"


def test_artifact_watcher_workspace_root_still_ignores_managed_dir(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    watcher = ArtifactWatcher(output_dir=str(ws.workspace_dir), ignore_managed_dirs=True)
    watcher.take_snapshot()

    report = ws.artifact_dir / "report.md"
    report.write_text("done", encoding="utf-8")
    source_file = ws.workspace_dir / "source.md"
    source_file.write_text("source", encoding="utf-8")

    records = watcher.detect_new_artifacts()
    paths = {r.file_path for r in records}

    assert str(source_file) in paths
    assert str(report) not in paths
