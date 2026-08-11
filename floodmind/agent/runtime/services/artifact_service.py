"""ArtifactService（target §15）——content-addressed 原子发布。

布局（§15 / runtime_layout.artifact_dirs）：
  <base>/objects/<sha[:2]>/<sha>                 # 内容寻址对象
  <base>/manifests/<artifact_id>.json            # 不可变 manifest

base 为 ArtifactService(base_dir) 的 base_dir（artifact store 根）。

发布管线（§15.2）：validate root containment → reject symlink escape
→ classify MIME/sensitivity → compute size/hash → copy to temp → fsync
→ atomic rename → append ArtifactCommitted → publish host event。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from floodmind.agent.runtime.contracts.artifacts import ArtifactDeclaration, ArtifactManifest
from floodmind.agent.runtime.services.runtime_layout import artifact_dirs

logger = logging.getLogger(__name__)

_MIME_BY_EXT = {
    ".json": "application/json", ".csv": "text/csv", ".txt": "text/plain",
    ".md": "text/markdown", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".pdf": "application/pdf", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html", ".py": "text/x-python",
}

_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _mime_for(path: Path) -> str:
    return _MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def _sensitivity_for(path: Path, declared: str) -> str:
    if declared:
        return declared
    if path.suffix.lower() in _IMG_EXT:
        return "internal"
    return "internal"


def _hash_file(path: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(block)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class ArtifactService:
    """Artifact 原子发布与解析服务。"""

    def __init__(
        self,
        base_dir: Union[str, Path],
        authority: Any = None,
        allowed_roots: Optional[List[str]] = None,
    ):
        self._base_dir = Path(base_dir)
        self._authority = authority
        # allowed_roots：发布源文件必须落在其内（resolve 后 containment 校验）
        self._allowed_roots = [Path(r).resolve() for r in (allowed_roots or [])]
        self._dirs = artifact_dirs(self._base_dir)
        self._dirs["objects_dir"].mkdir(parents=True, exist_ok=True)
        self._dirs["manifests_dir"].mkdir(parents=True, exist_ok=True)

    # ── 发布 ─────────────────────────────────────────────────────

    def publish(self, declaration: ArtifactDeclaration) -> ArtifactManifest:
        source = Path(declaration.source_path)
        if not source.is_file():
            raise ValueError(f"发布源不是文件: {source}")

        src_real = source.resolve()
        self._validate_containment(src_real)
        if source.is_symlink():
            # resolve 已展开；若目标逃逸 allowed_roots，上面已拒绝。
            pass

        media_type = declaration.media_type or _mime_for(source)
        sensitivity = _sensitivity_for(source, declaration.sensitivity)
        size = src_real.stat().st_size
        content_sha256 = _hash_file(src_real)

        object_dir = self._dirs["objects_dir"] / content_sha256[:2]
        object_path = object_dir / content_sha256
        object_dir.mkdir(parents=True, exist_ok=True)

        # 原子落盘：临时文件同目录 → fsync → os.replace
        if not object_path.exists():
            tmp = object_dir / f".tmp-{uuid.uuid4().hex[:12]}"
            shutil.copy2(str(src_real), str(tmp))
            # 以 r+b 打开（而非只读 rb）：Windows 上对只读句柄 fsync 会抛 EBADF。
            with tmp.open("r+b") as f:
                os.fsync(f.fileno())
            os.replace(tmp, object_path)

        artifact_id = f"art_{content_sha256[:16]}{uuid.uuid4().hex[:12]}"
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            content_sha256=content_sha256,
            media_type=media_type,
            size=size,
            storage_uri=str(object_path),
            logical_name=declaration.logical_name,
            producer_call_id=declaration.producer_call_id,
            producer_thread_id=declaration.producer_thread_id,
            sensitivity=sensitivity,
            verified=False,
            supersedes=declaration.supersedes,
            retention=declaration.retention,
            metadata=declaration.metadata,
        )

        if self._authority is not None:
            self._authority.emit("artifact.declared", {
                "artifact_id": artifact_id,
                "logical_name": manifest.logical_name,
                "media_type": media_type,
                "sensitivity": sensitivity,
                "producer_call_id": manifest.producer_call_id,
                "producer_thread_id": manifest.producer_thread_id,
                "supersedes": manifest.supersedes,
            })
        self._write_manifest(manifest)
        if self._authority is not None:
            self._authority.emit("artifact.committed", {
                "artifact_id": artifact_id,
                "content_sha256": content_sha256,
                "size": size,
                "storage_uri": manifest.storage_uri,
            })
        if manifest.supersedes:
            self._authority and self._authority.emit("artifact.superseded", {
                "artifact_id": artifact_id,
                "superseded": manifest.supersedes,
            })
        logger.info("Artifact published: %s (%s, %d bytes)", artifact_id, logical_name := manifest.logical_name, size)
        return manifest

    # ── 读取 / 验证 / 删除 ───────────────────────────────────────

    def resolve(self, artifact_id: str) -> ArtifactManifest:
        return self._read_manifest(artifact_id)

    def read_path(self, artifact_id: str) -> Path:
        """返回对象路径；下载方必须先 verify 再外发（§25.8 防路径穿越）。"""
        m = self._read_manifest(artifact_id)
        return Path(m.storage_uri)

    def verify(self, artifact_id: str) -> bool:
        m = self._read_manifest(artifact_id)
        obj = Path(m.storage_uri)
        ok = obj.is_file() and _hash_file(obj) == m.content_sha256
        if self._authority is not None:
            self._authority.emit("artifact.verified", {
                "artifact_id": artifact_id,
                "ok": ok,
                "detail": "hash_match" if ok else "hash_mismatch",
            })
        return ok

    def delete(self, artifact_id: str) -> bool:
        """Retention 删除（§25.8）：移除 manifest + 未被其他 manifest 引用的对象。"""
        m = self._read_manifest(artifact_id)
        obj = Path(m.storage_uri)
        self._manifest_path(artifact_id).unlink(missing_ok=True)
        # 仅当没有其他 manifest 引用同一对象时才删对象
        if not self._object_referenced(obj):
            obj.unlink(missing_ok=True)
            try:
                obj.parent.rmdir()
            except OSError:
                pass
        logger.info("Artifact deleted: %s", artifact_id)
        return True

    def supersede(self, old_id: str, declaration: ArtifactDeclaration) -> ArtifactManifest:
        declaration.supersedes = old_id
        return self.publish(declaration)

    def load_manifests(self) -> List[ArtifactManifest]:
        out = []
        for p in self._dirs["manifests_dir"].glob("*.json"):
            try:
                out.append(ArtifactManifest.model_validate_json(p.read_text(encoding="utf-8")))
            except Exception as e:
                logger.warning("Artifact manifest %s unreadable: %s", p, e)
        return out

    # ── 内部 ─────────────────────────────────────────────────────

    def _validate_containment(self, src_real: Path) -> None:
        if not self._allowed_roots:
            return  # 无 allowed_roots 时不强制（测试/离线场景）
        for root in self._allowed_roots:
            if src_real == root or root in src_real.parents:
                return
        raise ValueError(f"发布源 {src_real} 逃逸 allowed_roots {self._allowed_roots}")

    def _manifest_path(self, artifact_id: str) -> Path:
        return self._dirs["manifests_dir"] / f"{artifact_id}.json"

    def _write_manifest(self, manifest: ArtifactManifest) -> None:
        tmp = self._dirs["manifests_dir"] / f".tmp-{manifest.artifact_id}"
        tmp.write_text(manifest.model_dump_json(), encoding="utf-8")
        os.replace(tmp, self._manifest_path(manifest.artifact_id))

    def _read_manifest(self, artifact_id: str) -> ArtifactManifest:
        p = self._manifest_path(artifact_id)
        if not p.exists():
            raise KeyError(f"artifact {artifact_id} 不存在")
        return ArtifactManifest.model_validate_json(p.read_text(encoding="utf-8"))

    def _object_referenced(self, obj: Path) -> bool:
        for p in self._dirs["manifests_dir"].glob("*.json"):
            try:
                m = ArtifactManifest.model_validate_json(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if Path(m.storage_uri) == obj:
                return True
        return False
