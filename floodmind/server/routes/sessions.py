"""会话管理路由

Session CRUD, pause/resume, config, status, save, cleanup, events.
"""
import logging

from flask import Blueprint, request, jsonify

from floodmind.server.agent_factory import get_or_create_agent
from floodmind.server.sanitize import sanitize_output, sanitize_deep, sanitize_event_row, server_error_json
from floodmind.server.session_state import (
    ensure_session_state, serialize_snapshot, cleanup_session_state,
    session_abort_flags, session_abort_flags_lock,
    session_streaming_flags, session_streaming_lock,
)

logger = logging.getLogger(__name__)

sessions_bp = Blueprint('sessions', __name__)


def _sm():
    from flask import current_app
    return current_app.config['SESSION_MANAGER']


def _require_session_id(raw):
    from floodmind.memory.session_manager import validate_session_id
    return validate_session_id(raw or "default")


# ── 会话列表 ──────────────────────────────────────────

@sessions_bp.route('/api/sessions', methods=['GET'])
def list_sessions():
    try:
        sm = _sm()
        sessions = sm.get_all_sessions()
        result = []
        for s in sessions:
            session_data = s.to_dict()
            session_data['title'] = sm.get_session_title(s.session_id)
            session_data['updated_at'] = session_data.get('last_active', '')
            result.append(session_data)
        return jsonify({'status': 'success', 'sessions': result, 'stats': sm.get_stats()})
    except Exception as e:
        logger.error("获取会话列表失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 会话详情 ──────────────────────────────────────────

@sessions_bp.route('/api/sessions/<session_id>', methods=['GET'])
def get_session(session_id: str):
    try:
        session_id = _require_session_id(session_id)
        sm = _sm()
        info = sm.get_session_info(session_id)
        if not info:
            return jsonify({'status': 'error', 'message': '会话不存在'}), 404

        messages = sm.get_session_messages(session_id)
        title = sm.get_session_title(session_id)

        filtered_messages = []
        for msg in messages:
            filtered_msg = {'role': msg.get('role', ''), 'content': msg.get('content', '')}
            if msg.get('reasoning'):
                filtered_msg['reasoning'] = msg.get('reasoning', '')
            if msg.get('tool_calls'):
                filtered_msg['tool_calls'] = [
                    {
                        'tool_name': item.get('tool_name', ''),
                        'call_id': item.get('call_id') or item.get('tool_call_id', ''),
                        'tool_input': item.get('tool_input', ''),
                        'tool_output': item.get('tool_output', ''),
                    }
                    for item in msg.get('tool_calls', [])
                    if isinstance(item, dict)
                ]
            filtered_messages.append(filtered_msg)
        filtered_messages = sanitize_deep(filtered_messages)

        snapshot = ensure_session_state(session_id).get('stream_snapshot')
        in_progress = serialize_snapshot(snapshot) if (snapshot and snapshot.get('is_streaming')) else None

        from floodmind.server.file_utils import list_session_artifact_events
        return jsonify({
            'status': 'success',
            'session': {**info.to_dict(), 'title': title},
            'messages': filtered_messages,
            'artifacts': list_session_artifact_events(session_id, sm),
            'in_progress': in_progress,
        })
    except Exception as e:
        logger.error("获取会话详情失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 删除会话 ──────────────────────────────────────────

@sessions_bp.route('/api/sessions/<session_id>', methods=['DELETE'])
def delete_session_route(session_id: str):
    try:
        session_id = _require_session_id(session_id)
        sm = _sm()
        from floodmind.agent.runtime.adapters.flask_permission_api import handle_permission_cancel_session
        cancelled = handle_permission_cancel_session(session_id)
        if cancelled:
            logger.info("删除会话 %s: 已取消 %s 个 pending ASK", session_id, cancelled)
        sm.delete_session(session_id)
        from floodmind.server.file_utils import remove_session_files as _remove
        _remove(session_id, sm)
        cleanup_session_state(session_id)
        logger.info("已删除会话: %s", session_id)
        return jsonify({'status': 'success', 'message': f'会话 {session_id} 已删除'})
    except Exception as e:
        logger.error("删除会话失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 保存 ──────────────────────────────────────────────

@sessions_bp.route('/api/sessions/save', methods=['POST'])
def save_current_session():
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        sm = _sm()
        agent = sm.get_agent(session_id)
        if agent and hasattr(agent.memory, 'save_chat_history'):
            agent.memory.save_chat_history()
            return jsonify({'status': 'success', 'message': '会话已保存'})
        else:
            return jsonify({'status': 'success', 'message': '会话已保存（无Agent实例）'})
    except Exception as e:
        logger.error("保存会话失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 清理 ──────────────────────────────────────────────

@sessions_bp.route('/api/sessions/cleanup', methods=['POST'])
def trigger_cleanup():
    try:
        sm = _sm()
        sm.cleanup_expired_sessions()
        sm.cleanup_old_files()
        sm.cleanup_idle_sessions()
        return jsonify({'status': 'success', 'message': '清理完成', 'stats': sm.get_stats()})
    except Exception as e:
        logger.error("清理失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


@sessions_bp.route('/api/sessions/stats', methods=['GET'])
def get_session_stats():
    try:
        return jsonify({'status': 'success', 'stats': _sm().get_stats()})
    except Exception as e:
        logger.error("获取统计失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 暂停 / 恢复 ───────────────────────────────────────

@sessions_bp.route('/api/session/pause', methods=['POST'])
def pause_session():
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        with session_abort_flags_lock:
            session_abort_flags[session_id] = True
        try:
            from floodmind.agent.runtime.adapters.flask_permission_api import handle_permission_cancel_session
            cancelled = handle_permission_cancel_session(session_id)
            if cancelled:
                logger.info("暂停会话 %s: 已取消 %s 个 pending ASK", session_id, cancelled)
        except Exception:
            pass
        logger.info("会话已暂停（abort）: %s", session_id)
        return jsonify({'status': 'success', 'message': '会话已暂停'})
    except Exception as e:
        logger.error("暂停会话失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


@sessions_bp.route('/api/session/resume', methods=['POST'])
def resume_session():
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        with session_abort_flags_lock:
            session_abort_flags[session_id] = False
        return jsonify({'status': 'success', 'message': '会话已恢复'})
    except Exception as e:
        logger.error("恢复会话失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 状态查询 ──────────────────────────────────────────

@sessions_bp.route('/api/session/status', methods=['GET'])
def get_session_status():
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        state = ensure_session_state(session_id)
        safe_state = {k: v for k, v in state.items() if k != 'stream_snapshot'}
        snapshot = state.get('stream_snapshot')
        in_progress = serialize_snapshot(snapshot) if (snapshot and snapshot.get('is_streaming')) else None
        safe_state['stream_snapshot'] = in_progress
        return jsonify({'status': 'success', 'session_state': safe_state, 'in_progress': in_progress})
    except Exception as e:
        logger.error("获取会话状态失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 事件回放 ──────────────────────────────────────────

@sessions_bp.route('/api/sessions/<session_id>/events', methods=['GET'])
def get_session_events(session_id: str):
    try:
        from floodmind.memory.session_store import get_sync_events, get_last_event_index
        session_id = _require_session_id(session_id)
        after_index = request.args.get('after_index', 0, type=int)
        limit = request.args.get('limit', 200, type=int)
        events = get_sync_events(session_id, after_index=after_index, limit=limit)
        last_index = get_last_event_index(session_id)
        return jsonify({
            'status': 'success',
            'events': [sanitize_event_row(e) for e in events],
            'last_index': last_index,
            'has_more': len(events) >= limit,
        })
    except Exception as e:
        logger.error("获取事件失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500
