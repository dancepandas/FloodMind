"""
FloodMind Web Server

Flask 应用工厂。模块化架构：
- config.py         — 常量 & 配置
- sanitize.py       — 输出脱敏
- session_state.py  — 会话运行时状态
- agent_factory.py  — Agent 创建/复用
- file_utils.py     — 文件工具 & 产物提取
- routes/           — Flask Blueprint 路由
"""

import logging
import os

from flask import Flask
from flask_cors import CORS

from floodmind.server.config import STATIC_WEB_DIR, CORS_ORIGINS

logger = logging.getLogger(__name__)


def create_app(session_manager) -> Flask:
    """创建 Flask 应用并注册所有路由。

    Args:
        session_manager: SessionManager 实例（由入口 web_server.py 创建）
    """
    app = Flask(__name__, static_folder=STATIC_WEB_DIR)
    CORS(app, origins=CORS_ORIGINS, supports_credentials=True)
    app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB

    # 注册所有路由
    from floodmind.server.routes import register_routes
    register_routes(app, session_manager)

    # 错误处理
    @app.errorhandler(404)
    def not_found(error):
        from flask import jsonify
        return jsonify({'error': 'Not found'}), 404

    @app.errorhandler(500)
    def internal_error(error):
        from flask import jsonify
        return jsonify({'error': 'Internal server error'}), 500

    logger.info("FloodMind Web Server 已初始化 (static=%s)", STATIC_WEB_DIR)
    return app
