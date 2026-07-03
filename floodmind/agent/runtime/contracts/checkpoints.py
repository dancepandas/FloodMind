"""
Runtime Contracts — Checkpoint 协议模型

Checkpoint 是 Agent 执行状态的可恢复快照，包含：
- AgentLoopState 完整序列化
- 文件系统快照（写操作前的文件状态）
- 父 checkpoint 引用，形成恢复链

所有 checkpoint 相关数据结构集中定义，不依赖业务实现。
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
    files_snapshot_dir: Optional[str] = None
    files_snapshot_base_dirs: List[str] = Field(default_factory=list)
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
