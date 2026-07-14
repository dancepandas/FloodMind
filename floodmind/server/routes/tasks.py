"""定时任务路由"""
import logging

from flask import Blueprint, request, jsonify

from floodmind.agent.scheduled_task_runtime import get_scheduled_task_runtime
from floodmind.server.sanitize import sanitize_output, sanitize_deep

logger = logging.getLogger(__name__)

tasks_bp = Blueprint('tasks', __name__)


def _require_session_id(raw):
    from floodmind.memory.session_manager import validate_session_id
    return validate_session_id(raw or "default")


@tasks_bp.route('/api/scheduled-tasks', methods=['GET'])
def list_scheduled_task_api():
    try:
        session_id = request.args.get('session_id', '')
        include_all = request.args.get('include_all', '0') == '1'
        if session_id:
            session_id = _require_session_id(session_id)
        tasks = get_scheduled_task_runtime().list_tasks(session_id='' if include_all else session_id)
        return jsonify({'status': 'success', 'count': len(tasks), 'tasks': sanitize_deep(tasks)})
    except Exception as e:
        logger.error("查询定时任务失败: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


@tasks_bp.route('/api/scheduled-tasks/<task_id>', methods=['GET'])
def get_scheduled_task_api(task_id: str):
    try:
        task = get_scheduled_task_runtime().get_task(task_id)
        if not task:
            return jsonify({'status': 'error', 'message': '定时任务不存在'}), 404
        return jsonify({'status': 'success', 'task': sanitize_deep(task)})
    except Exception as e:
        logger.error("查询定时任务详情失败: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


@tasks_bp.route('/api/scheduled-tasks/<task_id>', methods=['PATCH'])
def update_scheduled_task_api(task_id: str):
    try:
        data = request.get_json() or {}
        updates = {key: data[key] for key in (
            'command', 'enabled', 'run_time', 'scheduled_at', 'repeat', 'status'
        ) if key in data}
        task = get_scheduled_task_runtime().update_task(task_id, **updates)
        return jsonify({'status': 'success', 'task': sanitize_deep(task)})
    except Exception as e:
        logger.error("修改定时任务失败: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 400


@tasks_bp.route('/api/scheduled-tasks/<task_id>', methods=['DELETE'])
def delete_scheduled_task_api(task_id: str):
    try:
        task = get_scheduled_task_runtime().delete_task(task_id)
        return jsonify({'status': 'success', 'task': sanitize_deep(task)})
    except Exception as e:
        logger.error("删除定时任务失败: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 400


@tasks_bp.route('/api/scheduled-tasks/<task_id>/artifacts', methods=['GET'])
def list_scheduled_task_artifacts_api(task_id: str):
    try:
        task = get_scheduled_task_runtime().get_task(task_id)
        if not task:
            return jsonify({'status': 'error', 'message': '定时任务不存在'}), 404
        return jsonify({
            'status': 'success',
            'task_id': task_id,
            'session_id': task.get('session_id', ''),
            'artifacts': task.get('artifacts', []) or [],
        })
    except Exception as e:
        logger.error("查询定时任务产物失败: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500
