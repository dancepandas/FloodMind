"""
会话级状态管理

管理 per-session 状态字典、流快照、abort/streaming 标志。
"""
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from floodmind.server.config import DATA_DIR
from floodmind.server.sanitize import sanitize_payload

# ── 会话级状态存储 ────────────────────────────────────
_session_token_usage: Dict[str, Dict[str, int]] = {}

session_states: Dict[str, Dict[str, Any]] = {}
session_states_lock = threading.RLock()

session_abort_flags: Dict[str, bool] = {}
session_abort_flags_lock = threading.RLock()

session_streaming_flags: Dict[str, bool] = {}
session_streaming_lock = threading.RLock()


def ensure_session_state(session_id: str) -> Dict[str, Any]:
    """获取或初始化会话状态。"""
    from floodmind.config.model_presets import get_default_model_key
    with session_states_lock:
        state = session_states.setdefault(session_id, {})
        state.setdefault('model_key', get_default_model_key())
        state.setdefault('enable_search', True)
        state.setdefault('enable_rag', True)
        state.setdefault('enable_reasoning', True)
        state.setdefault('is_paused', False)
        state.setdefault('is_streaming', False)
        state.setdefault('stream_snapshot', None)
        return state


def init_stream_snapshot(session_id: str, message_id: str) -> Dict[str, Any]:
    """初始化流快照。"""
    state = ensure_session_state(session_id)
    snapshot = {
        'message_id': message_id,
        'content': '',
        'reasoning': '',
        'raw_reasoning': '',
        'tool_results': [],
        'artifacts': [],
        'workflow': None,
        'is_streaming': True,
        'updated_at': datetime.now().isoformat(),
        'event_buffer': [],
        'resume_event': threading.Event(),
        'buffer_lock': threading.Lock(),
    }
    state['is_streaming'] = True
    state['stream_snapshot'] = snapshot
    return snapshot


def touch_stream_snapshot(session_id: str) -> Optional[Dict[str, Any]]:
    """更新时间戳。"""
    snapshot = ensure_session_state(session_id).get('stream_snapshot')
    if snapshot:
        snapshot['updated_at'] = datetime.now().isoformat()
    return snapshot


def finish_stream_snapshot(session_id: str) -> None:
    """结束流快照。"""
    state = ensure_session_state(session_id)
    state['is_streaming'] = False
    snapshot = state.get('stream_snapshot')
    if snapshot:
        snapshot['is_streaming'] = False
        snapshot['updated_at'] = datetime.now().isoformat()
        resume_event = snapshot.get('resume_event')
        if resume_event:
            resume_event.set()


def serialize_snapshot(snapshot: Optional[dict]) -> Optional[dict]:
    """序列化快照（剥离内部运行时对象与原始推理）。"""
    if not snapshot:
        return None
    data = {k: v for k, v in snapshot.items()
            if k not in ('event_buffer', 'resume_event', 'buffer_lock', 'raw_reasoning')}
    return sanitize_payload(data)


def get_token_usage(session_id: str) -> Dict[str, int]:
    """获取会话 Token 用量。"""
    return _session_token_usage.get(session_id, {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    })


def accumulate_token_usage(session_id: str, chunk: dict) -> None:
    """累加 Token 用量。"""
    prev = _session_token_usage.get(session_id, {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    })
    _session_token_usage[session_id] = {
        "prompt_tokens": prev["prompt_tokens"] + chunk.get("prompt_tokens", 0),
        "completion_tokens": prev["completion_tokens"] + chunk.get("completion_tokens", 0),
        "total_tokens": prev["total_tokens"] + chunk.get("total_tokens", 0),
    }


def clear_session_token_usage(session_id: str) -> None:
    """清除 Token 用量（Agent 重建时调用）。"""
    _session_token_usage.pop(session_id, None)


def cleanup_session_state(session_id: str) -> None:
    """清理会话所有运行时状态。"""
    with session_states_lock:
        session_states.pop(session_id, None)
    with session_abort_flags_lock:
        session_abort_flags.pop(session_id, None)
    with session_streaming_lock:
        session_streaming_flags.pop(session_id, None)
    _session_token_usage.pop(session_id, None)
