"""Checkpoint & Tracing 路由"""
import logging
import os

from flask import Blueprint, request, jsonify, send_file

from floodmind.server.sanitize import sanitize_output, server_error_json

logger = logging.getLogger(__name__)

checkpoints_bp = Blueprint('checkpoints', __name__)


def _require_session_id(raw):
    from floodmind.memory.session_manager import validate_session_id
    return validate_session_id(raw or "default")


def _checkpoints_base_dir():
    from floodmind.server.config import DATA_DIR
    return os.path.join(DATA_DIR, 'sessions')


def _debug_endpoints_enabled() -> bool:
    return os.environ.get('FLOODMIND_DEBUG', '').lower() in ('1', 'true', 'yes', 'on')


def _debug_only(func):
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _debug_endpoints_enabled():
            return jsonify({'error': '调试端点已禁用'}), 403
        return func(*args, **kwargs)
    return wrapper


# ── Checkpoints ───────────────────────────────────────

@checkpoints_bp.route('/api/sessions/<session_id>/checkpoints', methods=['GET'])
def list_checkpoints_api(session_id: str):
    from floodmind.agent.runtime.adapters.flask_checkpoint_api import handle_list_checkpoints
    result, status_code = handle_list_checkpoints(session_id, _checkpoints_base_dir())
    return jsonify(result), status_code


@checkpoints_bp.route('/api/sessions/<session_id>/checkpoints/<checkpoint_id>', methods=['GET'])
def get_checkpoint_manifest_api(session_id: str, checkpoint_id: str):
    from floodmind.agent.runtime.adapters.flask_checkpoint_api import handle_get_checkpoint_manifest
    result, status_code = handle_get_checkpoint_manifest(session_id, checkpoint_id, _checkpoints_base_dir())
    return jsonify(result), status_code


@checkpoints_bp.route('/api/sessions/<session_id>/checkpoints/<checkpoint_id>/rollback', methods=['POST'])
def rollback_checkpoint_api(session_id: str, checkpoint_id: str):
    from floodmind.agent.runtime.adapters.flask_checkpoint_api import handle_rollback_checkpoint
    result, status_code = handle_rollback_checkpoint(session_id, checkpoint_id, _checkpoints_base_dir())
    return jsonify(result), status_code


# ── Tracing (debug-only) ──────────────────────────────

@checkpoints_bp.route('/api/sessions/<session_id>/traces', methods=['GET'])
@_debug_only
def list_trace_events_api(session_id: str):
    from floodmind.agent.runtime.adapters.flask_tracing_api import handle_list_trace_events
    limit = request.args.get('limit', 200, type=int)
    result, status_code = handle_list_trace_events(session_id, _checkpoints_base_dir(), limit=limit)
    return jsonify(result), status_code


@checkpoints_bp.route('/api/sessions/<session_id>/traces/download', methods=['GET'])
@_debug_only
def download_trace_api(session_id: str):
    from floodmind.agent.runtime.adapters.flask_tracing_api import handle_get_trace_file_path
    try:
        path = handle_get_trace_file_path(session_id, _checkpoints_base_dir())
        if not path.exists():
            return jsonify({'status': 'error', 'message': 'trace 文件不存在'}), 404
        return send_file(
            str(path), mimetype='application/x-ndjson',
            as_attachment=True, download_name=f'{session_id}_trace.jsonl',
        )
    except Exception as e:
        logger.error("下载 trace 文件失败: %s", e, exc_info=True)
        return server_error_json(e)


# ── Logs download (debug-only) ────────────────────────

@checkpoints_bp.route('/api/logs')
@_debug_only
def download_logs():
    try:
        import zipfile, io
        from datetime import datetime

        from floodmind.server.config import PROJECT_ROOT
        logs_dir = os.path.join(PROJECT_ROOT, 'logs')
        if not os.path.isdir(logs_dir):
            return jsonify({'error': '日志目录不存在'}), 404

        log_files = [f for f in os.listdir(logs_dir) if f.endswith('.log') or f.endswith('.txt')]
        if not log_files:
            return jsonify({'error': '没有日志文件'}), 404

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fname in sorted(log_files):
                fpath = os.path.join(logs_dir, fname)
                zf.write(fpath, fname)
        buf.seek(0)

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        return send_file(buf, mimetype='application/zip', as_attachment=True,
                         download_name=f'logs_{ts}.zip')
    except Exception as e:
        logger.error("下载日志失败: %s", e, exc_info=True)
        return server_error_json(e)
