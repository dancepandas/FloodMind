"""
FloodAgent Web 服务器 — 入口

基于 Flask 的后端 API，为 React 前端提供流式聊天服务。
模块化架构见 floodmind/server/ 包。
"""

import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# ── 日志配置 ───────────────────────────────────────────
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(logs_dir, exist_ok=True)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
if root_logger.handlers:
    for handler in root_logger.handlers:
        root_logger.removeHandler(handler)

file_handler = TimedRotatingFileHandler(
    os.path.join(logs_dir, 'web_server.log'),
    when='midnight', interval=1, backupCount=30, encoding='utf-8',
)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

# ── 初始化 ─────────────────────────────────────────────
from floodmind.server.config import DATA_DIR, USE_REACT_FRONTEND, STATIC_WEB_DIR

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'sessions'), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'vector_store'), exist_ok=True)

logger.info("USE_REACT_FRONTEND: %s", USE_REACT_FRONTEND)
logger.info("STATIC_WEB_DIR: %s", STATIC_WEB_DIR)
logger.info("DATA_DIR: %s", DATA_DIR)

from floodmind.memory import SessionManager

session_manager = SessionManager({
    "max_active_sessions": int(os.environ.get('MAX_SESSIONS', 10)),
    "idle_timeout_minutes": int(os.environ.get('IDLE_TIMEOUT', 30)),
    "session_retention_days": int(os.environ.get('SESSION_RETENTION', 30)),
    "upload_retention_days": int(os.environ.get('UPLOAD_RETENTION', 7)),
    "output_retention_days": int(os.environ.get('OUTPUT_RETENTION', 30)),
    "cleanup_interval_minutes": int(os.environ.get('CLEANUP_INTERVAL', 60)),
    "data_dir": DATA_DIR,
})

from floodmind.server import create_app

app = create_app(session_manager)


# ── 主程序入口 ─────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    import platform

    parser = argparse.ArgumentParser(description='FloodAgent Web Server')
    parser.add_argument('--host', default='0.0.0.0', help='主机地址')
    parser.add_argument('--port', type=int, default=13014, help='端口号')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    args = parser.parse_args()

    session_manager.start_cleanup_thread()

    logger.info("启动 FloodAgent Web 服务器")
    logger.info("访问地址: http://%s:%s", args.host, args.port)
    logger.info("数据目录: %s", DATA_DIR)
    logger.info("最大会话数: %s", session_manager.config['max_active_sessions'])

    try:
        if args.debug:
            app.run(host=args.host, port=args.port, debug=True, threaded=True)
        else:
            if platform.system() == 'Windows':
                try:
                    from waitress import serve
                    logger.info("使用 waitress 生产服务器 (Windows)")
                    serve(app, host=args.host, port=args.port, threads=8, channel_timeout=300)
                except ImportError:
                    logger.warning("waitress 未安装，使用 Flask 开发服务器（不建议生产使用）")
                    logger.warning("安装: pip install waitress")
                    app.run(host=args.host, port=args.port, threaded=True)
            else:
                try:
                    from gunicorn.app.base import BaseApplication

                    class StandaloneApplication(BaseApplication):
                        def __init__(self, application, options=None):
                            self.options = options or {}
                            self.application = application
                            super().__init__()

                        def load_config(self):
                            for key, value in self.options.items():
                                if key in self.cfg.settings and value is not None:
                                    self.cfg.set(key.lower(), value)

                        def load(self):
                            return self.application

                    options = {
                        'bind': f'{args.host}:{args.port}',
                        'workers': 1,
                        'timeout': 300,
                        'worker_class': 'gthread',
                        'threads': 4,
                    }
                    logger.info("使用 gunicorn 生产服务器 (Linux)")
                    StandaloneApplication(app, options).run()
                except ImportError:
                    logger.warning("gunicorn 未安装，使用 Flask 开发服务器（不建议生产使用）")
                    logger.warning("安装: pip install gunicorn")
                    app.run(host=args.host, port=args.port, threaded=True)
    finally:
        session_manager.stop_cleanup_thread()
        session_manager.save_all()
        logger.info("服务器已关闭，所有会话已保存")
