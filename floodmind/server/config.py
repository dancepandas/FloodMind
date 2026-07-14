"""
FloodMind Web Server — 配置常量
"""

import os
import re
from pathlib import Path

# ── 目录 ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REACT_DIST_DIR = os.path.join(PROJECT_ROOT, 'web', 'dist')
LEGACY_WEB_DIR = os.path.join(PROJECT_ROOT, 'web')

_react_flag = os.environ.get('USE_REACT_FRONTEND')
if _react_flag is None:
    USE_REACT_FRONTEND = os.path.exists(REACT_DIST_DIR)
else:
    USE_REACT_FRONTEND = _react_flag == '1' and os.path.exists(REACT_DIST_DIR)

STATIC_WEB_DIR = REACT_DIST_DIR if USE_REACT_FRONTEND else LEGACY_WEB_DIR
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(PROJECT_ROOT, 'data'))

# ── 文件上传 ───────────────────────────────────────────
ALLOWED_EXTENSIONS = {
    'csv', 'xlsx', 'xls', 'txt', 'json', 'docx', 'pdf', 'md',
    'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp',
}

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}

DOWNLOADABLE_EXTENSIONS = {
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pdf': 'application/pdf',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.md': 'text/markdown',
}

ARTIFACT_EXTENSIONS = IMAGE_EXTENSIONS | set(DOWNLOADABLE_EXTENSIONS.keys())

# ── 产物识别 ───────────────────────────────────────────
ARTIFACT_PATH_PATTERN = re.compile(
    r'[A-Za-z]:\\[^\s\n]*\.(?:png|jpg|jpeg|docx|pdf|pptx|xlsx|md|txt)|/[^\s\n]*\.(?:png|jpg|jpeg|docx|pdf|pptx|xlsx|md|txt)',
    re.IGNORECASE,
)
ARTIFACT_FILENAME_PATTERN = re.compile(
    r'`?([\w\-一-鿿]+\.(?:png|jpg|jpeg|docx|pdf|pptx|xlsx|md|txt))`?',
    re.IGNORECASE,
)

# ── CORS ───────────────────────────────────────────────
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:13014",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:13014",
]

# ── SSE ────────────────────────────────────────────────
SSE_MAX_LIFETIME_SEC = 600  # 10 分钟最大流生命周期
