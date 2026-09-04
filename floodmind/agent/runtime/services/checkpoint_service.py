"""
CheckpointService — Agent 执行状态持久化与恢复

职责：
1. 保存 AgentLoopState 到 checkpoint 目录
2. 加载指定或最新 checkpoint
3. 列出、清理 checkpoint

设计原则：
- checkpoint 只保存 Agent runtime state，不复制 workspace 文件
- 与业务逻辑解耦，只负责状态序列化/反序列化和 checkpoint 文件 I/O
- checkpoint 目录结构：data/sessions/<session_id>/checkpoints/<checkpoint_id>/
  - manifest.json: 元数据
  - state.json: AgentLoopState 序列化
- 原子写入：先写 .tmp 目录，成功后 rename
- 自动保留最近 N 个 checkpoint，避免磁盘无限增长
"""

import json
import logging
import re
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
from floodmind.agent.runtime.contracts.run_state import RunState
from floodmind.agent.runtime.services.journal_authority import JournalAuthority
from floodmind.agent.runtime.services.tracing_service import TracingService

logger = logging.getLogger(__name__)

# 默认保留最近 checkpoint 数量
_DEFAULT_KEEP_COUNT = 10

# 状态文件名
_STATE_FILE = "state.json"
_RUN_STATE_FILE = "run_state.json"
_MANIFEST_FILE = "manifest.json"
_FILES_DIR = "files"

# ── 路径标识符校验（D01：session_id/checkpoint_id 直接拼路径，必须 fail-closed） ──
# 与 SessionManager.validate_session_id 同字母表；checkpoint_id 额外强制 ckpt- 前缀。
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-_.]{0,127}$")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_path_identifier(value: str, *, kind: str = "session") -> str:
    """校验用于拼路径的标识符（session_id / checkpoint_id），防路径穿越与 Windows 保留名。

    Raises:
        ValueError: 标识符为空、含非法字符、为 Windows 保留名或路径穿越形态。
    """
    if not value or not isinstance(value, str):
        raise ValueError(f"{kind} id 不能为空")
    v = value.strip()
    if not _IDENTIFIER_RE.match(v) or v in (".", "..") or v.endswith("."):
        raise ValueError(f"非法 {kind} id: {value!r}")
    if v.upper() in _WINDOWS_RESERVED or v.split(".")[0].upper() in _WINDOWS_RESERVED:
        raise ValueError(f"{kind} id 使用了 Windows 保留名: {value!r}")
    return v


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
            # 默认根收敛到运行时项目根（D19）：cwd 在桌面端/服务端不可靠
            from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT
            self._base_dir = PROJECT_ROOT / "data" / "sessions"
        self._keep_count = max(keep_count, 1)
        self._tracing_service = tracing_service

    # ── 公开 API ───────────────────────────────────────────────

    def save(
        self,
        state: Any,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        journal_cursor: int = 0,
        reducer_version: str = "1",
        tool_registry_version: str = "",
        run_state: Optional[RunState] = None,
    ) -> CheckpointRecord:
        """保存一个 checkpoint。

        Args:
            state: AgentLoopState 实例（需要可序列化为 JSON）
            metadata: 额外元数据

        Returns:
            CheckpointRecord
        """
        session_id = getattr(state, "session_id", "")
        run_id = getattr(state, "run_id", "")
        if not session_id:
            raise ValueError("CheckpointService.save: state.session_id 不能为空")
        if run_state is None:
            raise ValueError("CheckpointService.save: run_state snapshot 不能为空")
        if run_state.last_committed_sequence != journal_cursor:
            raise CheckpointConsistencyError(
                "RunState snapshot cursor 与 checkpoint journal_cursor 不一致"
            )
        if run_state.run_id != run_id:
            raise CheckpointConsistencyError("RunState snapshot run_id 与 loop state 不一致")

        # P2-4：先完成全部路径校验（可能抛 ValueError），再变更 state——
        # 否则校验失败时 state.checkpoint_id 等字段残留脏值，D08 回滚不完整
        checkpoint_id = self._make_checkpoint_id()
        checkpoint_dir = self._checkpoint_dir(session_id, checkpoint_id)
        session_cp_dir = self._session_checkpoints_dir(session_id)

        parent_checkpoint_id = getattr(state, "checkpoint_id", None)
        original_checkpoint_id = parent_checkpoint_id
        state.checkpoint_id = checkpoint_id  # 更新状态指向新 checkpoint
        if hasattr(state, "journal_cursor"):
            state.journal_cursor = journal_cursor
        state.updated_at = datetime.now(timezone.utc)
        manifest_metadata = dict(metadata or {})
        manifest_metadata.update({"run_id": run_id, "journal_cursor": journal_cursor})

        session_cp_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"ckpt-{checkpoint_id}-", dir=session_cp_dir))

        try:
            # 1. 序列化 state
            state_path = tmp_dir / _STATE_FILE
            state_data = self._serialize_state(state)
            state_path.write_text(json.dumps(state_data, ensure_ascii=False, sort_keys=True, default=self._json_default), encoding="utf-8")
            run_state_path = tmp_dir / _RUN_STATE_FILE
            run_state_path.write_text(
                run_state.model_dump_json(), encoding="utf-8"
            )

            # 2. manifest（checkpoint 只保存 runtime state，不复制文件系统）
            manifest = CheckpointManifest(
                checkpoint_id=checkpoint_id,
                session_id=session_id,
                run_id=run_id,
                parent_checkpoint_id=parent_checkpoint_id,
                status=getattr(state, "status", "unknown"),
                iteration=getattr(state, "iteration", 0),
                created_at=state.updated_at,
                state_file=_STATE_FILE,
                files_snapshot_dir=None,
                files_snapshot_base_dirs=[],
                journal_cursor=journal_cursor,
                reducer_version=reducer_version,
                tool_registry_version=tool_registry_version,
                run_state_file=_RUN_STATE_FILE,
                metadata=manifest_metadata,
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
                files_snapshot_path=None,
                journal_cursor=manifest.journal_cursor,
                reducer_version=manifest.reducer_version,
                metadata=manifest.metadata,
            )
            logger.info(
                "CheckpointService: saved checkpoint %s for session %s (iteration=%d, status=%s)",
                checkpoint_id, session_id, manifest.iteration, manifest.status,
            )
            return record

        except Exception:
            # 清理临时目录；回滚 state.checkpoint_id，避免内存状态指向不存在的
            # checkpoint 造成父子链断链（D08）
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
            try:
                state.checkpoint_id = original_checkpoint_id
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
            manifest = self.load_manifest(session_id, checkpoint_id)
            data["journal_cursor"] = manifest.journal_cursor
        except Exception as e:
            raise CheckpointCorruptedError(f"无法解析 checkpoint {checkpoint_id}: {e}") from e

        if state_class is not None:
            try:
                return state_class.model_validate(data)
            except Exception as e:
                raise CheckpointCorruptedError(f"无法反序列化 checkpoint {checkpoint_id}: {e}") from e

        return data

    def load_run_state(self, session_id: str, checkpoint_id: str) -> RunState:
        """Load the reducer snapshot bound to a checkpoint cursor."""
        manifest = self.load_manifest(session_id, checkpoint_id)
        snapshot_path = self._checkpoint_dir(session_id, checkpoint_id) / manifest.run_state_file
        if not snapshot_path.exists():
            raise CheckpointCorruptedError(
                f"checkpoint {checkpoint_id} 缺少 {manifest.run_state_file}"
            )
        try:
            return RunState.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise CheckpointCorruptedError(
                f"无法解析 checkpoint {checkpoint_id} reducer snapshot: {e}"
            ) from e

    def replay_from_checkpoint(
        self,
        authority: JournalAuthority,
        session_id: str,
        checkpoint_id: str,
        *,
        reducer_version: str = "1",
        expected_tool_registry_version: str = "",
    ) -> RunState:
        """Validate a checkpoint projection, then replay only its journal suffix."""
        manifest = self.load_manifest(session_id, checkpoint_id)
        snapshot = self.load_run_state(session_id, checkpoint_id)
        if manifest.reducer_version != reducer_version:
            raise CheckpointConsistencyError(
                f"reducer version 不匹配: {manifest.reducer_version} != {reducer_version}"
            )
        if expected_tool_registry_version and (
            manifest.tool_registry_version != expected_tool_registry_version
        ):
            raise CheckpointConsistencyError(
                "tool registry version 不匹配: "
                f"{manifest.tool_registry_version} != {expected_tool_registry_version}"
            )
        identity = manifest.metadata
        expected = {
            "conversation_id": authority.conversation_id,
            "task_id": authority.task_id,
            "run_id": authority.run_id,
            "thread_id": authority.thread_id,
            "turn_id": authority.turn_id,
        }
        for key, value in expected.items():
            if key not in identity or not identity[key]:
                raise CheckpointConsistencyError(
                    f"checkpoint journal identity 缺失: {key}"
                )
            if str(identity[key]) != value:
                raise CheckpointConsistencyError(
                    f"checkpoint journal identity 不匹配: {key}"
                )
        snapshot_expected = {
            "run_id": snapshot.run_id,
            "conversation_id": snapshot.conversation_id,
            "task_id": snapshot.task_id,
            "thread_id": snapshot.current_thread_id,
        }
        for key, value in snapshot_expected.items():
            if str(identity[key]) != value:
                raise CheckpointConsistencyError(
                    f"checkpoint RunState identity 不匹配: {key}"
                )
        if snapshot.run_id != manifest.run_id:
            raise CheckpointConsistencyError("checkpoint RunState run_id 不匹配")
        if snapshot.last_committed_sequence != manifest.journal_cursor:
            raise CheckpointConsistencyError("checkpoint RunState cursor 不匹配")
        rebuilt = authority.replay()  # 全量重放一次；cursor 校验与快照比对共用（去重复 replay）
        if rebuilt.last_committed_sequence < manifest.journal_cursor:
            raise CheckpointConsistencyError("checkpoint cursor 超出 canonical journal tail")
        prefix = rebuilt
        if manifest.journal_cursor != authority.cursor():
            from floodmind.agent.runtime.reducer import initial_run_state, reduce

            prefix = initial_run_state(
                authority.run_id,
                conversation_id=authority.conversation_id,
                task_id=authority.task_id,
                thread_id=authority.thread_id,
            )
            for event in authority.read_after(0):
                if event.sequence > manifest.journal_cursor:
                    break
                prefix = reduce(prefix, event)
        if prefix != snapshot:
            raise CheckpointConsistencyError("checkpoint projection 与 canonical journal 不一致")
        return authority.replay(after_sequence=manifest.journal_cursor, state=snapshot)

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
        """Restore a legacy file snapshot without altering checkpoint history.

        New SDK-first checkpoints contain runtime state only and therefore cannot
        be used for file rollback.  Callers must receive an explicit error rather
        than treating a missing snapshot as a successful no-op.
        """
        manifest = self.load_manifest(session_id, checkpoint_id)
        checkpoint_dir = self._checkpoint_dir(session_id, checkpoint_id)
        files_dir = checkpoint_dir / _FILES_DIR
        if not manifest.files_snapshot_dir or not files_dir.is_dir():
            raise CheckpointRollbackUnsupportedError(
                f"checkpoint {checkpoint_id} 不包含文件快照，无法回滚文件"
            )

        base_dirs = target_base_dirs or manifest.files_snapshot_base_dirs or []
        if not base_dirs:
            raise CheckpointRollbackUnsupportedError(
                f"checkpoint {checkpoint_id} 缺少文件快照目标目录，无法回滚文件"
            )

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
        validated = validate_path_identifier(session_id, kind="session")
        resolved = (self._base_dir / validated).resolve()
        base_resolved = self._base_dir.resolve()
        try:
            resolved.relative_to(base_resolved)
        except ValueError:
            raise ValueError(f"session id {session_id!r} 越出 checkpoint 根目录（fail-closed）")
        return self._base_dir / validated

    def _session_checkpoints_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "checkpoints"

    def _checkpoint_dir(self, session_id: str, checkpoint_id: str) -> Path:
        if not checkpoint_id.startswith("ckpt-"):
            raise ValueError(f"非法 checkpoint id（必须以 ckpt- 开头）: {checkpoint_id!r}")
        validated = validate_path_identifier(checkpoint_id, kind="checkpoint")
        path = self._session_checkpoints_dir(session_id) / validated
        resolved = path.resolve()
        try:
            resolved.relative_to(self._base_dir.resolve())
        except ValueError:
            raise ValueError(f"checkpoint id {checkpoint_id!r} 越出 checkpoint 根目录（fail-closed）")
        return path

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
                        journal_cursor=manifest.journal_cursor,
                        reducer_version=manifest.reducer_version,
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

    @staticmethod
    def _normalize_dynamic(value: Any, depth: int = 0) -> Any:
        """动态挂载字段的深度归一：Pydantic 模型 → dict，容器递归，超深截断。

        只处理运行时挂载字段（数量小），不触碰 messages 等已由 model_dump
        处理的声明字段。
        """
        if depth > 8:
            return "<unserializable:max_depth>"
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if isinstance(value, dict):
            return {k: CheckpointService._normalize_dynamic(v, depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [CheckpointService._normalize_dynamic(v, depth + 1) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return value  # 其余交给 _json_default 降级

    def _serialize_state(self, state: Any) -> Dict[str, Any]:
        """把状态对象序列化为 dict。优先使用 Pydantic model_dump，其次 __dict__。

        运行时动态挂载的属性（round_assistant_message / _pending_* 等，
        Pydantic extra=allow 允许 setattr 但不进 model_dump 的实例属性）显式
        合并进结果——MiniMax 等厂商要求 assistant 快照原样回传，checkpoint
        丢失该字段会让恢复后的下一轮请求 malformed。
        """
        if hasattr(state, "model_dump"):
            data = state.model_dump()
            declared = set(type(state).model_fields.keys())
            for key, value in vars(state).items():
                if key in declared or key.startswith("_") and key.startswith("__"):
                    continue
                if key.startswith("__") or key in ("_pydantic_extra__", "_pydantic_fields_set__", "_pydantic_private__"):
                    continue
                if key not in data:
                    data[key] = self._normalize_dynamic(value)
            return data
        if hasattr(state, "__dict__"):
            return dict(state.__dict__)
        raise TypeError(f"无法序列化状态对象: {type(state)}")

    @staticmethod
    def _json_default(obj: Any) -> Any:
        """JSON 序列化兜底。未知类型降级为标记字符串而非抛错——整次 checkpoint
        保存不应因单个宿主注入的不可序列化值失败（可序列化部分照常保留）。"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, (set, frozenset)):
            return sorted(obj, key=repr)
        marker = f"<unserializable:{type(obj).__name__}>"
        logger.warning("CheckpointService: 不可序列化值已降级为 %s", marker)
        return marker


class CheckpointError(Exception):
    """Checkpoint 相关错误的基类。"""


class CheckpointNotFoundError(CheckpointError):
    """Checkpoint 不存在。"""


class CheckpointRollbackUnsupportedError(CheckpointError):
    """Checkpoint has no usable legacy file snapshot."""


class CheckpointConsistencyError(CheckpointError):
    """Checkpoint projection does not match its canonical journal binding."""


class CheckpointCorruptedError(CheckpointError):
    """Checkpoint 数据损坏或无法解析。"""
