"""
Runtime Contracts — Checkpoint 协议模型

Checkpoint 是 Agent 执行状态的可恢复快照，包含：
- AgentLoopState 完整序列化
- 父 checkpoint 引用，形成恢复链
- 可选 metadata（如模型名、状态摘要、artifact 引用）

Checkpoint 不复制 workspace 文件。文件产物应通过 artifact/journal 记录引用；
文件回滚/备份如需支持，应由独立的 change journal / artifact versioning 能力承担。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CheckpointRecord(BaseModel):
    """Checkpoint 元数据记录，用于 list 和索引。"""

    checkpoint_id: str
    session_id: str
    run_id: str
    parent_checkpoint_id: Optional[str] = None
    status: str
    iteration: int
    created_at: datetime
    state_path: str
    files_snapshot_path: Optional[str] = None
    journal_cursor: int = 0
    reducer_version: str = "1"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CheckpointSummary(BaseModel):
    """Checkpoint 简要信息，用于 UI 展示。"""

    checkpoint_id: str
    status: str
    iteration: int
    created_at: datetime
    has_files_snapshot: bool = False


class CheckpointManifest(BaseModel):
    """单次 checkpoint 的清单文件，与 state.json 分离存储。"""

    model_config = ConfigDict(extra="allow")

    checkpoint_id: str
    session_id: str
    run_id: str
    parent_checkpoint_id: Optional[str] = None
    status: str
    iteration: int
    created_at: datetime
    state_file: str = "state.json"
    # Legacy fields retained for old manifests/routes. New SDK-first checkpoints leave these empty.
    files_snapshot_dir: Optional[str] = None
    files_snapshot_base_dirs: List[str] = Field(default_factory=list)
    journal_cursor: int = 0
    reducer_version: str = "1"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FilesSnapshotInfo(BaseModel):
    """文件快照描述信息。"""

    snapshot_id: str
    session_id: str
    checkpoint_id: str
    base_dir: str
    created_at: datetime
    file_count: int
    files: List[str] = Field(default_factory=list)
