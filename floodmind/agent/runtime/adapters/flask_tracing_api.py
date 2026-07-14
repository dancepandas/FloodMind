"""
Flask Tracing API — 适配层

将 HTTP 请求转换为本地 trace.jsonl 读取，不包含业务逻辑。
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _trace_path(base_dir: str, session_id: str) -> Path:
    return Path(base_dir) / session_id / "trace.jsonl"


def _parse_trace_line(line: str) -> Optional[Dict[str, Any]]:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def handle_list_trace_events(session_id: str, base_dir: str, limit: int = 200) -> tuple[dict, int]:
    """读取会话的 trace.jsonl 事件。"""
    try:
        path = _trace_path(base_dir, session_id)
        events: List[Dict[str, Any]] = []
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    event = _parse_trace_line(line)
                    if event is not None:
                        events.append(event)
        # 按时间顺序返回，限制数量（取最后 N 条）
        if limit > 0 and len(events) > limit:
            events = events[-limit:]
        return {
            "status": "success",
            "session_id": session_id,
            "events": events,
        }, 200
    except Exception as e:
        logger.error(f"读取 trace 事件失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}, 500


def handle_get_trace_file_path(session_id: str, base_dir: str) -> Path:
    return _trace_path(base_dir, session_id)
