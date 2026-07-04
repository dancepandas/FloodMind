"""静态文件 & 健康检查路由"""
from flask import Blueprint, send_from_directory, jsonify

static_bp = Blueprint('static', __name__)


@static_bp.route('/')
def index():
    from flask import current_app
    return send_from_directory(current_app.static_folder, 'index.html')


@static_bp.route('/favicon.ico')
def favicon():
    return ('', 204)


@static_bp.route('/api/health', methods=['GET'])
def health_check():
    from datetime import datetime
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0.0',
    })


@static_bp.route('/<path:path>')
def static_files(path):
    """静态文件 + SPA fallback"""
    from flask import current_app
    import os
    if path.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    file_path = os.path.join(current_app.static_folder, path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return send_from_directory(current_app.static_folder, path)
    return send_from_directory(current_app.static_folder, 'index.html')
