"""权限系统路由"""
import logging

from flask import Blueprint, request, jsonify

from floodmind.server.sanitize import sanitize_output

logger = logging.getLogger(__name__)

permission_bp = Blueprint('permission', __name__)


def _require_session_id(raw):
    from floodmind.memory.session_manager import validate_session_id
    return validate_session_id(raw or "default")


@permission_bp.route('/api/permission/respond', methods=['POST'])
def permission_respond():
    try:
        from floodmind.agent.runtime.adapters.flask_permission_api import handle_permission_respond
        data = request.get_json() or {}
        result, status_code = handle_permission_respond(data)
        return jsonify(result), status_code
    except Exception as e:
        logger.error("权限确认响应失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


@permission_bp.route('/api/permission/pending', methods=['GET'])
def permission_pending():
    try:
        from floodmind.agent.runtime.adapters.flask_permission_api import handle_permission_pending
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        result, status_code = handle_permission_pending(session_id)
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500
