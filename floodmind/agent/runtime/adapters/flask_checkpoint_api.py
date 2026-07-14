"""
Flask Checkpoint API — 适配层

将 HTTP 请求转换为 CheckpointService 调用，不包含业务逻辑。
"""

import logging
from pathlib import Path
from typing import Any, List, Optional

from floodmind.agent.runtime.contracts.checkpoints import CheckpointManifest, CheckpointSummary
from floodmind.agent.runtime.services.checkpoint_service import CheckpointService

logger = logging.getLogger(__name__)


def _serialize_summary(summary: CheckpointSummary) -> dict[str, Any]:
    # 公网白名单：剥离 iteration 等内部状态机字段，仅保留用户可理解信息
    return {
        "checkpoint_id": summary.checkpoint_id,
        "status": summary.status,
        "created_at": summary.created_at.isoformat() if summary.created_at else None,
        "has_files_snapshot": summary.has_files_snapshot,
    }


def _serialize_manifest(manifest: CheckpointManifest) -> dict[str, Any]:
    # 公网白名单：剥离 state_file / files_snapshot_* / run_id / parent_checkpoint_id / iteration
    # —— 这些字段含服务器绝对路径或内部状态机细节，不应暴露给前端
    return {
        "checkpoint_id": manifest.checkpoint_id,
        "session_id": manifest.session_id,
        "status": manifest.status,
        "created_at": manifest.created_at.isoformat() if manifest.created_at else None,
        "has_files_snapshot": bool(manifest.files_snapshot_dir),
        "metadata": manifest.metadata,
    }


def _get_service(base_dir: str) -> CheckpointService:
    return CheckpointService(base_dir=base_dir)


def handle_list_checkpoints(session_id: str, base_dir: str) -> tuple[dict, int]:
    try:
        service = _get_service(base_dir)
        summaries = service.list(session_id)
        return {
            "status": "success",
            "checkpoints": [_serialize_summary(s) for s in summaries],
        }, 200
    except Exception as e:
        logger.error(f"列出 checkpoint 失败: {e}", exc_info=True)
        return {"status": "error", "message": "服务器内部错误"}, 500


def handle_get_checkpoint_manifest(session_id: str, checkpoint_id: str, base_dir: str) -> tuple[dict, int]:
    try:
        service = _get_service(base_dir)
        manifest = service.load_manifest(session_id, checkpoint_id)
        return {
            "status": "success",
            "manifest": _serialize_manifest(manifest),
        }, 200
    except Exception as e:
        logger.error(f"获取 checkpoint manifest 失败: {e}", exc_info=True)
        return {"status": "error", "message": "服务器内部错误"}, 500


def handle_rollback_checkpoint(session_id: str, checkpoint_id: str, base_dir: str) -> tuple[dict, int]:
    try:
        service = _get_service(base_dir)
        restored = service.rollback_files(session_id, checkpoint_id)
        return {
            "status": "success",
            "checkpoint_id": checkpoint_id,
            "restored_files": restored,
        }, 200
    except Exception as e:
        logger.error(f"回滚 checkpoint 失败: {e}", exc_info=True)
        return {"status": "error", "message": "服务器内部错误"}, 500
