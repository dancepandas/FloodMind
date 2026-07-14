"""FloodMind Server Routes — 注册入口"""
from flask import Flask


def _get_session_manager():
    """从 Flask app 配置中获取 SessionManager 单例。"""
    from flask import current_app
    return current_app.config['SESSION_MANAGER']


def register_routes(app: Flask, session_manager) -> None:
    """注册所有 Blueprint 路由到 Flask app。"""
    app.config['SESSION_MANAGER'] = session_manager

    from floodmind.server.routes.static import static_bp
    from floodmind.server.routes.files import files_bp
    from floodmind.server.routes.chat import chat_bp
    from floodmind.server.routes.sessions import sessions_bp
    from floodmind.server.routes.models import models_bp
    from floodmind.server.routes.tasks import tasks_bp
    from floodmind.server.routes.memory import memory_bp
    from floodmind.server.routes.permission import permission_bp
    from floodmind.server.routes.checkpoints import checkpoints_bp

    app.register_blueprint(static_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(models_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(memory_bp)
    app.register_blueprint(permission_bp)
    app.register_blueprint(checkpoints_bp)
