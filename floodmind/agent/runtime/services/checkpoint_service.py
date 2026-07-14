"""
CheckpointService — Agent 执行状态持久化与恢复

职责：
1. 保存 AgentLoopState 到 checkpoint 目录
2. 对会话工作区做文件快照，支持按 checkpoint 回滚
3. 加载指定或最新 checkpoint
4. 列出、清理 checkpoint

设计原则：
- 与业务逻辑解耦，只负责序列化/反序列化和文件 I/O
- checkpoint 目录结构：data/sessions/<session_id>/checkpoints/<checkpoint_id>/
  - manifest.json: 元数据
  - state.json: AgentLoopState 序列化
  - files/: 文件快照目录（可选）
- 原子写入：先写 .tmp 目录，成功后 rename
- 自动保留最近 N 个 checkpoint，避免磁盘无限增长
"""

import json
import logging
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from floodmind.agent.runtime.contracts.checkpoints import (
    CheckpointManifest,
    CheckpointRecord,
    CheckpointSummary,
)
from floodmind.agent.runtime.services.tracing_service import TracingService

logger = logging.getLogger(__name__)

# 默认保留最近 checkpoint 数量
_DEFAULT_KEEP_COUNT = 10

# 状态文件名
_STATE_FILE = "state.json"
_MANIFEST_FILE = "manifest.json"
_FILES_DIR = "files"


class CheckpointService:
    """Agent 执行状态 checkpoint 服务。"""

    def __init__(
        self,
        base_dir: Optional[str] = None,
        keep_count: int = _DEFAULT_KEEP_COUNT,
        tracing_service: Optional[TracingService] = None,
    ):
        """
        Args:
            base_dir: checkpoint 根目录。默认使用当前工作目录下的 data/sessions。
            keep_count: 每个 session 保留的最大 checkpoint 数量，超出时删除最旧的。
        """
        if base_dir:
            self._base_dir = Path(base_dir)
        else:
            self._base_dir = Path.cwd() / "data" / "sessions"
        self._keep_count = max(keep_count, 1)
        self._tracing_service = tracing_service

    # ── 公开 API ───────────────────────────────────────────────

    def save(
        self,
        state: Any,
        files_dirs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CheckpointRecord:
        """保存一个 checkpoint。

        Args:
            state: AgentLoopState 实例（需要可序列化为 JSON）
            files_dirs: 需要快照的目录列表（如 output_dir, upload_dir）
            metadata: 额外元数据

        Returns:
            CheckpointRecord
        """
        session_id = getattr(state, "session_id", "")
        run_id = getattr(state, "run_id", "")
        if not session_id:
            raise ValueError("CheckpointService.save: state.session_id 不能为空")

        checkpoint_id = self._make_checkpoint_id()
        parent_checkpoint_id = getattr(state, "checkpoint_id", None)
        state.checkpoint_id = checkpoint_id  # 更新状态指向新 checkpoint
        state.updated_at = datetime.now(timezone.utc)

        checkpoint_dir = self._checkpoint_dir(session_id, checkpoint_id)
        session_cp_dir = self._session_checkpoints_dir(session_id)
        session_cp_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"ckpt-{checkpoint_id}-", dir=session_cp_dir))

        try:
            # 1. 序列化 state
            state_path = tmp_dir / _STATE_FILE
            state_data = self._serialize_state(state)
            state_path.write_text(json.dumps(state_data, ensure_ascii=False, sort_keys=True, default=self._json_default), encoding="utf-8")

            # 2. 文件快照
            files_snapshot_path = None
            snapshot_files: List[str] = []
            if files_dirs:
                files_dir = tmp_dir / _FILES_DIR
                snapshot_files = self._snapshot_files(files_dirs, files_dir)
                if snapshot_files:
                    files_snapshot_path = str(files_dir.relative_to(checkpoint_dir.parent))

            # 3. manifest
            manifest = CheckpointManifest(
                checkpoint_id=checkpoint_id,
                session_id=session_id,
                run_id=run_id,
                parent_checkpoint_id=parent_checkpoint_id,
                status=getattr(state, "status", "unknown"),
                iteration=getattr(state, "iteration", 0),
                created_at=state.updated_at,
                state_file=_STATE_FILE,
                files_snapshot_dir=str(Path(_FILES_DIR)) if snapshot_files else None,
                files_snapshot_base_dirs=files_dirs if files_dirs else [],
                metadata=metadata or {},
            )
            manifest_path = tmp_dir / _MANIFEST_FILE
            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

            # 4. 原子发布
            tmp_dir.rename(checkpoint_dir)

            if self._tracing_service is not None:
                self._tracing_service.record_event(
                    session_id,
                    "checkpoint",
                    "checkpoint_save",
                    output={
                        "checkpoint_id": checkpoint_id,
                        "iteration": manifest.iteration,
                        "status": manifest.status,
                    },
                )

            # 5. 清理旧 checkpoint
            self._cleanup_old_checkpoints(session_id)

            record = CheckpointRecord(
                checkpoint_id=checkpoint_id,
                session_id=session_id,
                run_id=run_id,
                parent_checkpoint_id=parent_checkpoint_id,
                status=manifest.status,
                iteration=manifest.iteration,
                created_at=manifest.created_at,
                state_path=str(state_path),
                files_snapshot_path=files_snapshot_path,
                metadata=manifest.metadata,
            )
            logger.info(
                "CheckpointService: saved checkpoint %s for session %s (iteration=%d, status=%s)",
                checkpoint_id, session_id, manifest.iteration, manifest.status,
            )
            return record

        except Exception:
            # 清理临时目录
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
            raise

    def load(
        self,
        session_id: str,
        checkpoint_id: Optional[str] = None,
        state_class: Optional[type] = None,
    ) -> Any:
        """加载 checkpoint 中的 AgentLoopState。

        Args:
            session_id: 会话 ID
            checkpoint_id: checkpoint ID，None 表示加载最新
            state_class: 用于反序列化的状态类，默认从已保存数据中恢复

        Returns:
            AgentLoopState 实例
        """
        if not checkpoint_id:
            record = self._latest_checkpoint_record(session_id)
            if record is None:
                raise CheckpointNotFoundError(f"会话 {session_id} 没有 checkpoint")
            checkpoint_id = record.checkpoint_id

        checkpoint_dir = self._checkpoint_dir(session_id, checkpoint_id)
        if not checkpoint_dir.is_dir():
            raise CheckpointNotFoundError(f"checkpoint {checkpoint_id} 不存在")

        state_path = checkpoint_dir / _STATE_FILE
        if not state_path.exists():
            raise CheckpointNotFoundError(f"checkpoint {checkpoint_id} 缺少 state.json")

        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise CheckpointCorruptedError(f"无法解析 checkpoint {checkpoint_id}: {e}") from e

        if state_class is not None:
            try:
                return state_class.model_validate(data)
            except Exception as e:
                raise CheckpointCorruptedError(f"无法反序列化 checkpoint {checkpoint_id}: {e}") from e

        return data

    def load_manifest(self, session_id: str, checkpoint_id: str) -> CheckpointManifest:
        """加载 checkpoint manifest。"""
        checkpoint_dir = self._checkpoint_dir(session_id, checkpoint_id)
        manifest_path = checkpoint_dir / _MANIFEST_FILE
        if not manifest_path.exists():
            raise CheckpointNotFoundError(f"checkpoint {checkpoint_id} manifest 不存在")
        return CheckpointManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    def list(self, session_id: str) -> List[CheckpointSummary]:
        """列出某会话的所有 checkpoint（按时间倒序）。"""
        records = self._list_records(session_id)
        return [
            CheckpointSummary(
                checkpoint_id=r.checkpoint_id,
                status=r.status,
                iteration=r.iteration,
                created_at=r.created_at,
                has_files_snapshot=r.files_snapshot_path is not None,
            )
            for r in records
        ]

    def rollback_files(
        self,
        session_id: str,
        checkpoint_id: str,
        target_base_dirs: Optional[List[str]] = None,
    ) -> List[str]:
        """将文件快照恢复到原始位置。

        Args:
            target_base_dirs: 可选，指定恢复到哪些目录。默认使用 checkpoint 中记录的 base_dirs。

        Returns:
            被恢复的文件路径列表
        """
        checkpoint_dir = self._checkpoint_dir(session_id, checkpoint_id)
        files_dir = checkpoint_dir / _FILES_DIR
        if not files_dir.is_dir():
            return []

        manifest = self.load_manifest(session_id, checkpoint_id)
        base_dirs = target_base_dirs or manifest.files_snapshot_base_dirs or []
        if not base_dirs:
            return []

        restored: List[str] = []
        # 快照中的相对路径是相对于每个 base_dir 的
        for base_dir in base_dirs:
            base_path = Path(base_dir)
            for src_path in files_dir.rglob("*"):
                if not src_path.is_file():
                    continue
                rel = src_path.relative_to(files_dir)
                dst_path = base_path / rel
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                restored.append(str(dst_path))

        logger.info("CheckpointService: rolled back %d files for session %s to checkpoint %s", len(restored), session_id, checkpoint_id)
        return restored

    def get_session_dir(self, session_id: str) -> Path:
        """返回会话根目录。"""
        return self._session_dir(session_id)

    # ── 内部辅助 ───────────────────────────────────────────────

    def _make_checkpoint_id(self) -> str:
        return f"ckpt-{uuid.uuid4().hex[:16]}"

    def _session_dir(self, session_id: str) -> Path:
        return self._base_dir / session_id

    def _session_checkpoints_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "checkpoints"

    def _checkpoint_dir(self, session_id: str, checkpoint_id: str) -> Path:
        return self._session_checkpoints_dir(session_id) / checkpoint_id

    def _list_records(self, session_id: str) -> List[CheckpointRecord]:
        cp_dir = self._session_checkpoints_dir(session_id)
        if not cp_dir.is_dir():
            return []

        records: List[CheckpointRecord] = []
        for entry in cp_dir.iterdir():
            if not entry.is_dir():
                continue
            manifest_path = entry / _MANIFEST_FILE
            if not manifest_path.exists():
                continue
            try:
                manifest = CheckpointManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
                records.append(
                    CheckpointRecord(
                        checkpoint_id=manifest.checkpoint_id,
                        session_id=manifest.session_id,
                        run_id=manifest.run_id,
                        parent_checkpoint_id=manifest.parent_checkpoint_id,
                        status=manifest.status,
                        iteration=manifest.iteration,
                        created_at=manifest.created_at,
                        state_path=str(entry / manifest.state_file),
                        files_snapshot_path=str(entry / manifest.files_snapshot_dir) if manifest.files_snapshot_dir else None,
                        metadata=manifest.metadata,
                    )
                )
            except Exception as e:
                logger.warning("CheckpointService: 跳过损坏的 checkpoint 目录 %s: %s", entry, e)

        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def _latest_checkpoint_record(self, session_id: str) -> Optional[CheckpointRecord]:
        records = self._list_records(session_id)
        return records[0] if records else None

    def _cleanup_old_checkpoints(self, session_id: str) -> None:
        records = self._list_records(session_id)
        if len(records) <= self._keep_count:
            return
        for old in records[self._keep_count :]:
            old_dir = self._checkpoint_dir(session_id, old.checkpoint_id)
            try:
                shutil.rmtree(old_dir, ignore_errors=True)
                logger.info("CheckpointService: cleaned up old checkpoint %s", old.checkpoint_id)
            except Exception as e:
                logger.warning("CheckpointService: 清理旧 checkpoint %s 失败: %s", old.checkpoint_id, e)

    def _snapshot_files(self, dirs: List[str], dest_dir: Path) -> List[str]:
        """把 dirs 中的文件复制到 dest_dir，返回相对路径列表。"""
        copied: List[str] = []
        dest_resolved = dest_dir.resolve()
        for base_dir in dirs:
            if not base_dir or not Path(base_dir).is_dir():
                continue
            base_path = Path(base_dir)
            for src_path in base_path.rglob("*"):
                # 跳过符号链接，防止跟随到允许目录外
                if src_path.is_symlink():
                    continue
                if not src_path.is_file():
                    continue
                rel = src_path.relative_to(base_path)
                dst_path = dest_dir / rel
                # 路径遍历防护：目标必须落在 dest_dir 内
                try:
                    dst_path.resolve().relative_to(dest_resolved)
                except ValueError:
                    logger.warning("CheckpointService: skip snapshot path traversal %s", dst_path)
                    continue
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                copied.append(str(rel.as_posix()))
        return copied

    def _serialize_state(self, state: Any) -> Dict[str, Any]:
        """把状态对象序列化为 dict。优先使用 Pydantic model_dump，其次 __dict__。"""
        if hasattr(state, "model_dump"):
            return state.model_dump()
        if hasattr(state, "__dict__"):
            return dict(state.__dict__)
        raise TypeError(f"无法序列化状态对象: {type(state)}")

    @staticmethod
    def _json_default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class CheckpointError(Exception):
    """Checkpoint 相关错误的基类。"""


class CheckpointNotFoundError(CheckpointError):
    """Checkpoint 不存在。"""


class CheckpointCorruptedError(CheckpointError):
    """Checkpoint 数据损坏或无法解析。"""
