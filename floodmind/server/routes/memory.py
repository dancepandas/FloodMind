"""记忆系统路由"""
import logging

from flask import Blueprint, request, jsonify

from floodmind.server.sanitize import sanitize_output, sanitize_deep, server_error_json
from floodmind.server.session_state import get_token_usage

logger = logging.getLogger(__name__)

memory_bp = Blueprint('memory', __name__)


def _sm():
    from flask import current_app
    return current_app.config['SESSION_MANAGER']


def _require_session_id(raw):
    from floodmind.memory.session_manager import validate_session_id
    return validate_session_id(raw or "default")


@memory_bp.route('/api/memory/stats', methods=['GET'])
def get_memory_stats():
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        agent = _sm().get_agent(session_id)
        if agent:
            stats = agent.get_memory_summary()
            return jsonify({
                'status': 'success',
                'stats': sanitize_deep(stats) if isinstance(stats, dict) else stats,
            })
        else:
            return jsonify({'status': 'success', 'stats': {'message': '会话尚未初始化'}})
    except Exception as e:
        logger.error("获取记忆统计失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


@memory_bp.route('/api/token-usage', methods=['GET'])
def get_token_usage_api():
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        return jsonify({'status': 'success', 'usage': get_token_usage(session_id)})
    except Exception as e:
        logger.error("获取 Token 用量失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


@memory_bp.route('/api/memory/heartbeat', methods=['POST'])
def trigger_heartbeat():
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        agent = _sm().get_agent(session_id)
        if agent:
            if hasattr(agent.memory, 'force_heartbeat'):
                result = agent.memory.force_heartbeat()
                return jsonify({
                    'status': 'success',
                    'message': sanitize_output(result) if isinstance(result, str) else result,
                })
            else:
                return jsonify({'status': 'error', 'message': '当前记忆系统不支持心跳归纳'}), 400
        else:
            return jsonify({'status': 'error', 'message': '会话不存在'}), 404
    except Exception as e:
        logger.error("触发心跳失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


@memory_bp.route('/api/memory/search', methods=['POST'])
def search_long_term_memory():
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        query = data.get('query', '')
        top_k = data.get('top_k', 5)
        if not query:
            return jsonify({'status': 'error', 'message': '查询内容不能为空'}), 400
        agent = _sm().get_agent(session_id)
        if agent:
            if hasattr(agent.memory, 'search_long_term'):
                results = agent.memory.search_long_term(query, top_k)
                return jsonify({'status': 'success', 'results': results})
            else:
                return jsonify({'status': 'error', 'message': '当前记忆系统不支持长期记忆搜索'}), 400
        else:
            return jsonify({'status': 'error', 'message': '会话不存在'}), 404
    except Exception as e:
        logger.error("搜索长期记忆失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


@memory_bp.route('/api/memory/add', methods=['POST'])
def add_long_term_memory():
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        content = data.get('content', '')
        entry_type = data.get('type', 'note')
        if not content:
            return jsonify({'status': 'error', 'message': '记忆内容不能为空'}), 400
        agent = _sm().get_agent(session_id)
        if agent:
            if hasattr(agent.memory, 'add_long_term_memory'):
                success = agent.memory.add_long_term_memory(content, entry_type)
                return jsonify({
                    'status': 'success' if success else 'error',
                    'message': '已添加到长期记忆' if success else '添加失败（可能重复）',
                })
            else:
                return jsonify({'status': 'error', 'message': '当前记忆系统不支持手动添加长期记忆'}), 400
        else:
            return jsonify({'status': 'error', 'message': '会话不存在'}), 404
    except Exception as e:
        logger.error("添加长期记忆失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500
