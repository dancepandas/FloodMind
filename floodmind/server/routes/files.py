"""文件上传 / 下载 / 预览路由"""
import logging
import mimetypes
import os
import uuid
from datetime import datetime

from flask import Blueprint, request, jsonify, send_file

from floodmind.server.file_utils import (
    allowed_file, safe_filename, dedup_filename,
    get_session_upload_dir, get_session_output_dir,
    get_session_files_map, public_file_info,
    build_uploaded_file_preview, remove_session_files,
    session_files_lock, session_files, _save_session_files,
)
from floodmind.server.sanitize import sanitize_output, server_error_json

logger = logging.getLogger(__name__)

files_bp = Blueprint('files', __name__)


def _sm():
    from flask import current_app
    return current_app.config['SESSION_MANAGER']


def _require_session_id(raw):
    from floodmind.memory.session_manager import validate_session_id
    return validate_session_id(raw or "default")


def _is_within_dir(path: str, base_dir: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(base_dir)]) == os.path.realpath(base_dir)
    except ValueError:
        return False


# ── 上传 ──────────────────────────────────────────────

@files_bp.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': '没有选择文件'}), 400

        file = request.files['file']
        session_id = _require_session_id(request.form.get('session_id', 'default'))
        sm = _sm()
        sm.get_or_create_session(session_id, agent_factory=None)

        filename = file.filename or ''
        if filename == '':
            return jsonify({'status': 'error', 'message': '没有选择文件'}), 400

        if not allowed_file(filename):
            from floodmind.server.config import ALLOWED_EXTENSIONS
            return jsonify({
                'status': 'error',
                'message': f'不支持的文件类型，允许的类型: {", ".join(ALLOWED_EXTENSIONS)}'
            }), 400

        file_id = str(uuid.uuid4())
        filename = safe_filename(filename)

        session_dir = get_session_upload_dir(session_id, sm)
        filename = dedup_filename(session_dir, filename)
        file_path = os.path.join(session_dir, filename)
        file.save(file_path)

        from floodmind.server.config import IMAGE_EXTENSIONS
        ext = os.path.splitext(filename)[1].lower()
        kind = 'image' if ext in IMAGE_EXTENSIONS else 'document'
        mime_type = mimetypes.guess_type(filename)[0] or (
            'image/png' if ext in IMAGE_EXTENSIONS else 'application/octet-stream')

        session_file_map = get_session_files_map(session_id, sm)
        session_file_map[file_id] = {
            'id': file_id, 'name': filename, 'path': file_path,
            'size': os.path.getsize(file_path),
            'upload_time': datetime.now().isoformat(),
            'kind': kind, 'mime_type': mime_type,
        }
        with session_files_lock:
            session_files[session_id] = session_file_map
        from floodmind.server.file_utils import _save_session_files
        _save_session_files(session_id, sm)

        logger.info("文件上传成功: %s -> %s", filename, file_path)
        return jsonify({
            'status': 'success',
            'file_id': file_id,
            'file_name': filename,
            'size': os.path.getsize(file_path),
        })

    except Exception as e:
        logger.error("文件上传失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 列表 ──────────────────────────────────────────────

@files_bp.route('/api/files', methods=['GET'])
def list_files():
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        sm = _sm()
        files = [public_file_info(item) for item in get_session_files_map(session_id, sm).values()]
        return jsonify({'status': 'success', 'files': files})
    except Exception as e:
        logger.error("获取文件列表失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 删除 ──────────────────────────────────────────────

@files_bp.route('/api/files/<file_id>', methods=['DELETE'])
def delete_file(file_id: str):
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        sm = _sm()
        session_file_map = get_session_files_map(session_id, sm)
        if file_id in session_file_map:
            file_info = session_file_map[file_id]
            file_path = file_info.get('path')
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            del session_file_map[file_id]
            with session_files_lock:
                session_files[session_id] = session_file_map
            from floodmind.server.file_utils import _save_session_files
            _save_session_files(session_id, sm)
            logger.info("文件已删除: %s", file_id)
        return jsonify({'status': 'success', 'message': '文件已删除'})
    except Exception as e:
        logger.error("文件删除失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 预览 ──────────────────────────────────────────────

@files_bp.route('/api/files/<file_id>/preview', methods=['GET'])
def preview_uploaded_file(file_id: str):
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        sm = _sm()
        session_file_map = get_session_files_map(session_id, sm)
        if file_id not in session_file_map:
            return jsonify({'status': 'error', 'message': '文件不存在'}), 404
        file_info = session_file_map[file_id]
        preview = build_uploaded_file_preview(file_info)
        preview['download_url'] = f"/api/files/{file_id}/download?session_id={session_id}"
        return jsonify({'status': 'success', 'preview': preview})
    except Exception as e:
        logger.error("预览上传文件失败: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 下载 ──────────────────────────────────────────────

@files_bp.route('/api/files/<file_id>/download', methods=['GET'])
def download_uploaded_file(file_id: str):
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        sm = _sm()
        session_file_map = get_session_files_map(session_id, sm)
        if file_id not in session_file_map:
            return jsonify({'status': 'error', 'message': '文件不存在'}), 404
        file_info = session_file_map[file_id]
        file_path = file_info.get('path', '')
        file_name = file_info.get('name', '')
        if not os.path.exists(file_path):
            return jsonify({'error': '文件不存在'}), 404
        uploads_dir = os.path.realpath(get_session_upload_dir(session_id, sm))
        real_path = os.path.realpath(file_path)
        if not _is_within_dir(real_path, uploads_dir):
            return jsonify({'error': '非法路径'}), 403
        inline = request.args.get('inline') == 'true'
        ext = os.path.splitext(file_name)[1].lower()
        mimetype_map = {
            '.pdf': 'application/pdf',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.xls': 'application/vnd.ms-excel',
            '.txt': 'text/plain', '.json': 'application/json',
            '.csv': 'text/csv', '.md': 'text/markdown',
        }
        mimetype = mimetype_map.get(ext, 'application/octet-stream')
        return send_file(
            real_path, mimetype=mimetype,
            as_attachment=not inline,
            download_name=file_name if not inline else None,
        )
    except Exception as e:
        logger.error("下载上传文件失败: %s", e, exc_info=True)
        return server_error_json(e)


# ── 输出文件服务 ───────────────────────────────────────

@files_bp.route('/api/sessions/<session_id>/outputs/<path:filename>')
def get_session_output_file(session_id: str, filename: str):
    try:
        session_id = _require_session_id(session_id)
        sm = _sm()
        output_dir = get_session_output_dir(session_id, sm)
        file_path = os.path.join(output_dir, filename)
        logger.info("[OUTPUT_FILE] 请求: session=%s, filename=%s", session_id, filename)
        real_path = os.path.realpath(file_path)
        real_output_dir = os.path.realpath(output_dir)
        if not _is_within_dir(real_path, real_output_dir):
            logger.warning("[OUTPUT_FILE] 非法路径: real=%s, output_dir=%s", real_path, real_output_dir)
            return jsonify({'error': '非法路径'}), 403
        if not os.path.exists(real_path):
            logger.warning("[OUTPUT_FILE] 文件不存在: %s", real_path)
            return jsonify({'error': '文件不存在'}), 404
        file_size = os.path.getsize(real_path)
        ext = os.path.splitext(filename)[1].lower()
        logger.info("[OUTPUT_FILE] 命中: ext=%s, size=%s", ext, file_size)
        if ext == '.png':
            return send_file(real_path, mimetype='image/png')
        if ext in {'.jpg', '.jpeg'}:
            return send_file(real_path, mimetype='image/jpeg')
        from floodmind.server.config import DOWNLOADABLE_EXTENSIONS
        if ext in DOWNLOADABLE_EXTENSIONS:
            if request.args.get('inline') == 'true':
                return send_file(real_path, mimetype=DOWNLOADABLE_EXTENSIONS[ext], as_attachment=False)
            return send_file(real_path, mimetype=DOWNLOADABLE_EXTENSIONS[ext],
                             as_attachment=True, download_name=filename)
        return send_file(real_path)
    except Exception as e:
        logger.error("[OUTPUT_FILE] 获取输出文件失败: %s", e, exc_info=True)
        return server_error_json(e)


@files_bp.route('/api/sessions/<session_id>/outputs/download')
def download_session_outputs(session_id: str):
    try:
        session_id = _require_session_id(session_id)
        import zipfile, io
        from werkzeug.utils import secure_filename
        sm = _sm()
        output_dir = get_session_output_dir(session_id, sm)
        if not os.path.isdir(output_dir):
            return jsonify({'error': '输出目录不存在'}), 404
        all_files = []
        for root, _dirs, files in os.walk(output_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, output_dir)
                all_files.append((fpath, arcname))
        if not all_files:
            return jsonify({'error': '没有输出文件'}), 404
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fpath, arcname in all_files:
                zf.write(fpath, arcname)
        buf.seek(0)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_id = secure_filename(session_id)
        return send_file(buf, mimetype='application/zip', as_attachment=True,
                         download_name=f'{safe_id}_outputs_{ts}.zip')
    except Exception as e:
        logger.error("下载会话输出失败: %s", e, exc_info=True)
        return server_error_json(e)
