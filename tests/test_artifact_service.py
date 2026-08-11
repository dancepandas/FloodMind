"""ArtifactService 发布管线测试（§15）。"""
import hashlib
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.artifacts import ArtifactDeclaration, ArtifactManifest
from floodmind.agent.runtime.services.artifact_service import ArtifactService
from floodmind.agent.runtime.services.sandbox_service import SandboxService


def _write(root: Path, rel: str, content: bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_publish_computes_hash_and_manifest(tmp_path):
    src = _write(tmp_path, "in/forecast.json", b'{"v":1}')
    store = tmp_path / "artifacts"
    svc = ArtifactService(store)
    m = svc.publish(ArtifactDeclaration(
        logical_name="forecast.json", source_path=str(src),
        producer_thread_id="th_1", producer_call_id="call_1",
    ))
    assert m.artifact_id.startswith("art_")
    assert m.content_sha256 == _sha(b'{"v":1}')
    assert m.size == 7  # len(b'{"v":1}') == 7
    assert m.media_type == "application/json"
    assert m.storage_uri.endswith(m.content_sha256)
    assert m.verified is False
    # manifest 落盘
    assert (store / "manifests" / f"{m.artifact_id}.json").exists()
    # 对象 content-addressed 落盘
    obj = store / "objects" / m.content_sha256[:2] / m.content_sha256
    assert obj.read_bytes() == b'{"v":1}'


def test_publish_rejects_symlink_escape(tmp_path):
    victim = _write(tmp_path, "victim/secret.txt", b"TOP SECRET")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, allowed_roots=[str(tmp_path / "in")])
    src = _write(tmp_path, "in/evil", b"")
    src.unlink()  # 占位文件先删除，否则 symlink_to 抛 FileExistsError
    try:
        src.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用")
    with pytest.raises(Exception):
        svc.publish(ArtifactDeclaration(logical_name="evil", source_path=str(src)))


def test_publish_content_containment_enforced(tmp_path):
    outside = _write(tmp_path, "outside/secret.txt", b"x")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, allowed_roots=[str(tmp_path / "in")])
    with pytest.raises(Exception):
        svc.publish(ArtifactDeclaration(logical_name="s", source_path=str(outside)))


def test_verify_detects_corruption(tmp_path):
    src = _write(tmp_path, "in/a.csv", b"a,b\n1,2\n")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store)
    m = svc.publish(ArtifactDeclaration(logical_name="a.csv", source_path=str(src)))
    assert svc.verify(m.artifact_id) is True
    obj = store / "objects" / m.content_sha256[:2] / m.content_sha256
    obj.write_bytes(b"corrupted")
    assert svc.verify(m.artifact_id) is False


def test_resolve_and_read_path(tmp_path):
    src = _write(tmp_path, "in/r.txt", b"hello")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store)
    m = svc.publish(ArtifactDeclaration(logical_name="r.txt", source_path=str(src)))
    loaded = svc.resolve(m.artifact_id)
    assert loaded.artifact_id == m.artifact_id
    assert svc.read_path(m.artifact_id).read_bytes() == b"hello"


def test_supersede_links_previous(tmp_path):
    v1 = _write(tmp_path, "in/v1.txt", b"v1")
    v2 = _write(tmp_path, "in/v2.txt", b"v2")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store)
    m1 = svc.publish(ArtifactDeclaration(logical_name="v.txt", source_path=str(v1)))
    m2 = svc.publish(ArtifactDeclaration(logical_name="v.txt", source_path=str(v2), supersedes=m1.artifact_id))
    assert m2.supersedes == m1.artifact_id


def test_publish_emits_journal_events(tmp_path):
    from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
    from floodmind.agent.runtime.contracts.run_state import RunStatus
    from floodmind.agent.runtime.reducer import initial_run_state, reduce
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority

    src = _write(tmp_path, "in/e.json", b'{"e":1}')
    auth = open_journal_authority(tmp_path / "journal", conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, authority=auth)
    m = svc.publish(ArtifactDeclaration(
        logical_name="e.json", source_path=str(src),
        producer_thread_id="th", producer_call_id="call_1",
    ))
    events = auth.read_after(0)  # replay() 返回 RunState；读事件流用 read_after
    types = [e.event_type for e in events]
    assert "artifact.declared" in types
    assert "artifact.committed" in types
    # reducer：committed 事件把 artifact_id 计入 RunState.artifacts
    s = initial_run_state("run_1", thread_id="th")
    for e in events:
        s = reduce(s, e)
    assert m.artifact_id in s.artifacts


def test_artifact_survives_sandbox_destroy(tmp_path):
    """§25.7：Artifact 不因 Sandbox 销毁丢失。"""
    base = tmp_path / "sessions"
    sb = SandboxService(base_dir=base)
    ctx = sb.create("sub_1")
    src = ctx.workspace_dir / "out.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n0000")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, allowed_roots=[str(ctx.workspace_dir)])
    m = svc.publish(ArtifactDeclaration(
        logical_name="out.png", source_path=str(src),
        producer_thread_id="th", producer_call_id="call_1",
    ))
    sb.destroy(ctx)  # 沙盒工作区被删除
    assert not src.exists()
    assert svc.resolve(m.artifact_id) is not None
    assert svc.read_path(m.artifact_id).read_bytes().startswith(b"\x89PNG")
