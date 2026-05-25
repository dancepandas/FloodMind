"""
FloodAgent Web 服务器
基于 Flask 的后端 API，为新的前端提供流式聊天服务
支持文件上传和下载
"""

import os
import time
import sys
import json
import uuid
import base64
import logging
import re
import threading
import hashlib
from typing import Dict, Any, Optional
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, Response, send_from_directory, send_file
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# 加载环境变量
load_dotenv()

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import settings
from models import QwenLLMService, get_qwen_llm_service, create_llm_service_from_preset
from config.model_presets import get_preset, get_default_model_key, get_models_list, resolve_api_key, resolve_base_url
from memory import SimpleMemory, DualMemory, SessionManager
from memory.session_manager import validate_session_id
from agent.native import create_flood_agent
from agent.scheduled_task_runtime import get_scheduled_task_runtime
from tools import set_rag_config, set_memory_instance, set_session_context

# 配置日志
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(logs_dir, exist_ok=True)

# 创建日志格式
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# 配置根日志记录器
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# 移除已存在的处理器
if root_logger.handlers:
    for handler in root_logger.handlers:
        root_logger.removeHandler(handler)

# 创建文件处理器（按日期分割）
from logging.handlers import TimedRotatingFileHandler
file_handler = TimedRotatingFileHandler(
    os.path.join(logs_dir, 'web_server.log'),
    when='midnight',
    interval=1,
    backupCount=30,
    encoding='utf-8'
)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)

# 创建控制台处理器
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

# 添加处理器到根日志记录器
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

# 创建 Flask 应用
REACT_DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'dist')
LEGACY_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
react_frontend_flag = os.environ.get('USE_REACT_FRONTEND')
if react_frontend_flag is None:
    USE_REACT_FRONTEND = os.path.exists(REACT_DIST_DIR)
else:
    USE_REACT_FRONTEND = react_frontend_flag == '1' and os.path.exists(REACT_DIST_DIR)
STATIC_WEB_DIR = REACT_DIST_DIR if USE_REACT_FRONTEND else LEGACY_WEB_DIR

app = Flask(__name__, static_folder=STATIC_WEB_DIR)
CORS(app)

logger.debug(f"USE_REACT_FRONTEND: {USE_REACT_FRONTEND}")
logger.debug(f"STATIC_WEB_DIR: {STATIC_WEB_DIR}")

DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'sessions'), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'vector_store'), exist_ok=True)

logger.info(f"DATA_DIR: {DATA_DIR}")

ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls', 'txt', 'json', 'docx', 'pdf', 'md'}

session_manager = SessionManager({
    "max_active_sessions": int(os.environ.get('MAX_SESSIONS', 10)),
    "idle_timeout_minutes": int(os.environ.get('IDLE_TIMEOUT', 30)),
    "session_retention_days": int(os.environ.get('SESSION_RETENTION', 30)),
    "upload_retention_days": int(os.environ.get('UPLOAD_RETENTION', 7)),
    "output_retention_days": int(os.environ.get('OUTPUT_RETENTION', 30)),
    "cleanup_interval_minutes": int(os.environ.get('CLEANUP_INTERVAL', 60)),
    "data_dir": DATA_DIR,
})

session_files: Dict[str, Dict[str, dict]] = {}
session_files_lock = threading.RLock()

session_states: Dict[str, Dict[str, Any]] = {}
session_states_lock = threading.RLock()

session_abort_flags: Dict[str, bool] = {}
session_abort_flags_lock = threading.RLock()

session_streaming_flags: Dict[str, bool] = {}
session_streaming_lock = threading.RLock()

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg'}
DOWNLOADABLE_EXTENSIONS = {
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pdf': 'application/pdf',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.md': 'text/markdown',
}
ARTIFACT_EXTENSIONS = IMAGE_EXTENSIONS | set(DOWNLOADABLE_EXTENSIONS.keys())
ARTIFACT_PATH_PATTERN = re.compile(
    r'[A-Za-z]:\\[^\s\n]*\.(?:png|jpg|jpeg|docx|pdf|pptx|xlsx|md|txt)|/[^\s\n]*\.(?:png|jpg|jpeg|docx|pdf|pptx|xlsx|md|txt)',
    re.IGNORECASE,
)
ARTIFACT_FILENAME_PATTERN = re.compile(
    r'`?([\w\-\u4e00-\u9fff]+\.(?:png|jpg|jpeg|docx|pdf|pptx|xlsx|md|txt))`?',
    re.IGNORECASE,
)


def _require_session_id(raw_session_id: Optional[str]) -> str:
    return validate_session_id(raw_session_id or "default")


def _is_within_dir(path: str, base_dir: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(base_dir)]) == os.path.realpath(base_dir)
    except ValueError:
        return False


def _public_file_info(file_info: dict) -> dict:
    return {
        'id': file_info.get('id', ''),
        'name': file_info.get('name', ''),
        'size': file_info.get('size', 0),
        'upload_time': file_info.get('upload_time', ''),
    }


def _stable_upload_file_id(filename: str) -> str:
    return hashlib.sha1(str(filename).encode("utf-8")).hexdigest()[:16]


def _get_upload_index_path(session_id: str) -> Path:
    return session_manager.get_session_dir(session_id) / "uploads_index.json"


def _save_session_files(session_id: str) -> None:
    with session_files_lock:
        records = list(session_files.get(session_id, {}).values())
    _get_upload_index_path(session_id).write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_session_files(session_id: str) -> Dict[str, dict]:
    session_id = _require_session_id(session_id)
    uploads_dir = Path(get_session_upload_dir(session_id))
    index_path = _get_upload_index_path(session_id)
    records: Dict[str, dict] = {}

    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    file_id = str(item.get("id", "")).strip()
                    file_name = str(item.get("name", "")).strip()
                    file_path = str(item.get("path", "")).strip()
                    if not file_id or not file_name or not file_path or not os.path.isfile(file_path):
                        continue
                    records[file_id] = {
                        "id": file_id,
                        "name": file_name,
                        "path": file_path,
                        "size": int(item.get("size", 0) or 0),
                        "upload_time": str(item.get("upload_time", "") or ""),
                    }
        except Exception as e:
            logger.warning(f"读取上传文件索引失败: {e}")

    if not records and uploads_dir.exists():
        for path in sorted(uploads_dir.iterdir()):
            if not path.is_file():
                continue
            file_id = _stable_upload_file_id(path.name)
            records[file_id] = {
                "id": file_id,
                "name": path.name,
                "path": str(path),
                "size": path.stat().st_size,
                "upload_time": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
            }

    with session_files_lock:
        session_files[session_id] = records
    if records:
        _save_session_files(session_id)
    return records


def _get_session_files_map(session_id: str) -> Dict[str, dict]:
    session_id = _require_session_id(session_id)
    with session_files_lock:
        cached = session_files.get(session_id)
    if cached is not None:
        return cached
    return _load_session_files(session_id)


def _remove_session_files(session_id: str) -> None:
    with session_files_lock:
        session_files.pop(session_id, None)
    index_path = _get_upload_index_path(session_id)
    if index_path.exists():
        index_path.unlink()


def allowed_file(filename: str) -> bool:
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _safe_filename(filename: str) -> str:
    """保留中文的文件名安全化处理，仅移除路径分隔符等危险字符。"""
    import re
    name, ext = os.path.splitext(filename)
    name = re.sub(r'[\\/:*?"<>|]', '_', name).strip()
    if not name:
        name = 'file'
    return f"{name}{ext}"


def _dedup_filename(directory: str, filename: str) -> str:
    """若目录下已存在同名文件，自动加编号，如 文件(1).docx。"""
    target = os.path.join(directory, filename)
    if not os.path.exists(target):
        return filename
    name, ext = os.path.splitext(filename)
    counter = 1
    while os.path.exists(os.path.join(directory, f"{name}({counter}){ext}")):
        counter += 1
    return f"{name}({counter}){ext}"


def build_session_output_url(filepath: str, fallback_session_id: str) -> str:
    """根据输出文件路径构造会话输出 URL，保留子目录相对路径。"""
    real_path = os.path.realpath(filepath)
    output_dir = os.path.realpath(get_session_output_dir(fallback_session_id))
    if _is_within_dir(real_path, output_dir):
        rel = os.path.relpath(real_path, output_dir).replace(os.sep, "/")
        return f"/api/sessions/{fallback_session_id}/outputs/{rel}"

    return f"/api/sessions/{fallback_session_id}/outputs/{os.path.basename(filepath)}"


def build_uploaded_file_preview(file_info: dict) -> dict:
    """安全读取上传文件预览内容，不暴露 uploads 原始路径。"""
    file_path = file_info.get('path', '')
    file_name = file_info.get('name', '')
    ext = os.path.splitext(file_name)[1].lower()

    preview = {
        'file_id': file_info.get('id', ''),
        'file_name': file_name,
        'size': file_info.get('size', 0),
        'preview_type': 'unsupported',
        'content': '',
    }

    if not os.path.exists(file_path):
        preview['preview_type'] = 'missing'
        preview['content'] = '文件不存在或已被清理。'
        return preview

    if ext in {'.txt', '.json'}:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as handle:
            preview['content'] = handle.read(6000)
        preview['preview_type'] = 'text'
        return preview

    if ext == '.csv':
        import pandas as pd
        df = pd.read_csv(file_path).head(20)
        preview['preview_type'] = 'table'
        preview['columns'] = [str(col) for col in df.columns.tolist()]
        preview['rows'] = df.fillna('').astype(str).values.tolist()
        return preview

    if ext in {'.xlsx', '.xls'}:
        import pandas as pd
        excel = pd.ExcelFile(file_path)
        preview['preview_type'] = 'excel'
        preview['sheets'] = []
        for sheet_name in excel.sheet_names[:5]:
            df = pd.read_excel(file_path, sheet_name=sheet_name).head(12)
            preview['sheets'].append({
                'sheet_name': sheet_name,
                'columns': [str(col) for col in df.columns.tolist()],
                'rows': df.fillna('').astype(str).values.tolist(),
            })
        return preview

    if ext == '.md':
        with open(file_path, 'r', encoding='utf-8', errors='replace') as handle:
            preview['content'] = handle.read(6000)
        preview['preview_type'] = 'text'
        return preview

    if ext in {'.docx', '.pdf'}:
        preview['preview_type'] = 'document'
        preview['content'] = '点击预览按钮查看文档内容。'
        return preview

    preview['content'] = '该文件类型暂不支持在线预览。'
    return preview


def _generate_session_title(message: str) -> str:
    prompt = (
        "请根据用户问题拟一个简短的中文会话标题，用于左侧历史会话列表展示。"
        "要求：10到18个字，突出任务目标，不要带引号，不要解释，不要句号。\n\n"
        f"用户问题：{message}"
    )
    # 标题生成是后台辅助任务，必须使用独立实例，避免污染会话主链路的推理模式。
    llm = QwenLLMService(
        api_key=settings.qwen.api_key,
        model_name=settings.qwen.model_name,
        temperature=0.2,
        max_tokens=60,
        enable_thinking=False,
    )
    raw = (llm.invoke(prompt).content or '').strip()
    title = raw.splitlines()[0].strip().strip('"“”')
    title = re.sub(r"^[#\-*\d.\s]+", "", title).strip()
    return title[:24] if title else ''


def schedule_session_title_generation(session_id: str, message: str) -> None:
    """后台异步生成会话标题，不向前端展示过程。"""
    def _worker() -> None:
        try:
            title = _generate_session_title(message)
            if title:
                session_manager.update_session_title(session_id, title)
                logger.info(f"会话标题已更新: {session_id} -> {title}")
        except Exception as e:
            logger.warning(f"生成会话标题失败: {e}")

    threading.Thread(target=_worker, daemon=True, name=f"session-title-{session_id[:8]}").start()


def extract_generated_paths(content: str) -> list[str]:
    """从工具输出或 token 中提取生成文件路径。"""
    if not content:
        return []

    return [match.strip().strip('*').strip('-').strip('`').strip() for match in ARTIFACT_PATH_PATTERN.findall(content)]


def extract_generated_filenames(content: str) -> list[str]:
    """从文本中提取仅包含文件名的成果引用。"""
    if not content:
        return []

    seen: list[str] = []
    for match in ARTIFACT_FILENAME_PATTERN.findall(content):
        filename = str(match).strip().strip('`').strip()
        if filename and filename not in seen:
            seen.append(filename)
    return seen


def build_artifact_event(filepath: str, fallback_session_id: str, emitted_paths: set[str]) -> dict | None:
    """为新生成的输出文件构造 SSE 事件。"""
    real_path = os.path.realpath(filepath)
    ext = os.path.splitext(real_path)[1].lower()

    if ext not in ARTIFACT_EXTENSIONS:
        return None

    if not os.path.exists(real_path):
        logger.warning(f"生成文件不存在: {real_path}")
        return None

    output_dir = os.path.realpath(get_session_output_dir(fallback_session_id))
    if not _is_within_dir(real_path, output_dir):
        logger.warning(f"生成文件不在会话输出目录内: {real_path}")
        return None

    if real_path in emitted_paths:
        return None

    emitted_paths.add(real_path)
    url = build_session_output_url(real_path, fallback_session_id)
    file_info = {
        'filename': os.path.basename(real_path),
        'size': os.path.getsize(real_path),
    }

    if ext in IMAGE_EXTENSIONS:
        file_info['type'] = 'image_generated'
        file_info['image_url'] = url
        file_info['download_url'] = url
    else:
        file_info['type'] = 'file_generated'
        file_info['download_url'] = url

    return file_info


def list_recent_output_artifacts(output_dir: str, request_started_at: float) -> list[str]:
    """列出本轮请求期间新生成的输出文件。"""
    if not os.path.isdir(output_dir):
        return []

    artifacts = []
    for filename in os.listdir(output_dir):
        full_path = os.path.join(output_dir, filename)
        if not os.path.isfile(full_path):
            continue

        ext = os.path.splitext(filename)[1].lower()
        if ext not in ARTIFACT_EXTENSIONS:
            continue

        try:
            if os.path.getmtime(full_path) >= request_started_at - 1:
                artifacts.append(full_path)
        except OSError as exc:
            logger.warning(f"读取输出文件时间失败: {full_path}, {exc}")

    artifacts.sort(key=lambda path: os.path.getmtime(path))
    return artifacts


def build_artifact_summary_text(artifact_events: list[dict[str, Any]], original_text: str) -> Optional[str]:
    """基于本轮真实生成文件构造更面向用户的最终交付文案。"""
    if not artifact_events:
        return None

    lines = []
    image_events = [event for event in artifact_events if event.get('type') == 'image_generated']
    deliverable_exts = {'.xlsx', '.xls', '.docx', '.doc', '.pdf'}
    file_events = [
        event for event in artifact_events
        if event.get('type') == 'file_generated'
        and os.path.splitext(str(event.get('filename', '')))[1].lower() in deliverable_exts
    ]

    cleaned_original = (original_text or "").strip()
    if not cleaned_original:
        cleaned_original = "任务已完成。"

    internal_artifact_names = {'input.json', 'result.json', 'result.xlsx', 'result.csv', 'result.txt'}
    is_explicit_delivery = any(
        kw in cleaned_original for kw in ('最终交付', '已生成', '导出文件', '生成报告', '生成文件', '保存文件', '下载文件')
    )

    if image_events:
        label = "最终交付图片" if is_explicit_delivery else "附带图片"
        lines.append(f"{label}：")
        for event in image_events:
            lines.append(f"- `{event.get('filename', '')}`")

    if file_events:
        internal_files = [e for e in file_events if e.get('filename', '') in internal_artifact_names]
        deliverable_files = [e for e in file_events if e not in internal_files]

        if deliverable_files:
            label = "最终交付文件" if is_explicit_delivery else "附带结果文件"
            lines.append(f"{label}：")
            for event in deliverable_files:
                lines.append(f"- `{event.get('filename', '')}`")

        if internal_files and not deliverable_files:
            lines.append("附带结果文件：")
            for event in internal_files:
                lines.append(f"- `{event.get('filename', '')}`")

    if not lines:
        return None

    summary_block = "\n".join(lines)
    if summary_block in cleaned_original:
        return cleaned_original
    return f"{cleaned_original}\n\n{summary_block}"


def stream_json_line(payload: dict[str, Any]) -> str:
    """将流事件编码为 NDJSON 行。"""
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _buffered_yield(buf: list, payload: dict, resume_event: Optional[threading.Event] = None, buffer_lock: Optional[threading.Lock] = None) -> str:
    line = stream_json_line(payload)
    if buffer_lock:
        with buffer_lock:
            buf.append(line)
    else:
        buf.append(line)
    if resume_event:
        resume_event.set()
    return line


def _serialize_snapshot(snapshot: Optional[dict]) -> Optional[dict]:
    if not snapshot:
        return None
    return {k: v for k, v in snapshot.items() if k not in ('event_buffer', 'resume_event', 'buffer_lock')}


def ensure_session_state(session_id: str) -> dict[str, Any]:
    with session_states_lock:
        state = session_states.setdefault(session_id, {})
        state.setdefault('model_key', get_default_model_key())
        state.setdefault('enable_search', False)
        state.setdefault('enable_rag', True)
        state.setdefault('enable_reasoning', True)
        state.setdefault('is_paused', False)
        state.setdefault('is_streaming', False)
        state.setdefault('stream_snapshot', None)
        return state


def init_stream_snapshot(session_id: str, message_id: str) -> dict[str, Any]:
    state = ensure_session_state(session_id)
    snapshot = {
        'message_id': message_id,
        'content': '',
        'reasoning': '',
        'raw_reasoning': '',
        'tool_results': [],
        'artifacts': [],
        'workflow': None,
        'is_streaming': True,
        'updated_at': datetime.now().isoformat(),
        'event_buffer': [],
        'resume_event': threading.Event(),
        'buffer_lock': threading.Lock(),
    }
    state['is_streaming'] = True
    state['stream_snapshot'] = snapshot
    return snapshot


def touch_stream_snapshot(session_id: str) -> Optional[dict[str, Any]]:
    snapshot = ensure_session_state(session_id).get('stream_snapshot')
    if snapshot:
        snapshot['updated_at'] = datetime.now().isoformat()
    return snapshot


def _sanitize_artifact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (event or {}).items() if k != 'filepath'}


def finish_stream_snapshot(session_id: str) -> None:
    state = ensure_session_state(session_id)
    state['is_streaming'] = False
    snapshot = state.get('stream_snapshot')
    if snapshot:
        snapshot['is_streaming'] = False
        snapshot['updated_at'] = datetime.now().isoformat()
        resume_event = snapshot.get('resume_event')
        if resume_event:
            resume_event.set()


def list_session_artifact_events(session_id: str) -> list[dict[str, Any]]:
    """列出某个会话最终确认可交付的产物事件。"""
    artifacts_file = os.path.join(str(session_manager.get_session_dir(session_id)), 'approved_artifacts.json')
    if os.path.exists(artifacts_file):
        try:
            data = json.loads(Path(artifacts_file).read_text(encoding='utf-8'))
            if isinstance(data, list):
                items = [_sanitize_artifact_event(item) for item in data if isinstance(item, dict)]
                for item in items:
                    filename = item.get('filename', '')
                    if not item.get('download_url') and filename:
                        item['download_url'] = f"/api/sessions/{session_id}/outputs/{filename}"
                    if item.get('type') == 'image_generated' and not item.get('image_url') and item.get('download_url'):
                        item['image_url'] = item['download_url']
                return items
        except Exception as e:
            logger.warning(f"读取最终成果文件记录失败: {e}")
    return []


def _artifact_dedup_key(event: dict[str, Any]) -> str:
    url = event.get('download_url') or event.get('image_url', '')
    if url:
        return str(url)
    return f"{event.get('type', '')}:{event.get('filename', '')}"


def save_session_artifact_events(session_id: str, artifact_events: list[dict[str, Any]]) -> None:
    artifacts_file = os.path.join(str(session_manager.get_session_dir(session_id)), 'approved_artifacts.json')
    existing: list[dict[str, Any]] = []
    if os.path.exists(artifacts_file):
        try:
            data = json.loads(Path(artifacts_file).read_text(encoding='utf-8'))
            if isinstance(data, list):
                existing = [item for item in data if isinstance(item, dict)]
        except Exception:
            pass
    new_sanitized = [_sanitize_artifact_event(item) for item in artifact_events]
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for item in existing:
        key = _artifact_dedup_key(item)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    for item in new_sanitized:
        key = _artifact_dedup_key(item)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    Path(artifacts_file).write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def extract_validated_artifact_paths(content: str, session_id: str = "") -> list[str]:
    """只提取已经被调度器/校验逻辑认定为最终可交付的成果文件。"""
    text = (content or '').strip()
    if not text:
        return []

    try:
        payload = json.loads(text)
    except Exception:
        return []

    if not isinstance(payload, dict):
        return []

    is_native_delegate = payload.get('is_native_delegate', False)
    validation = payload.get('validation') or {}
    overall_status = str(validation.get('overall_status', '')).strip().lower()
    is_final_goal_met = validation.get('is_final_goal_met')
    current_result_type = str(validation.get('current_result_type', '')).strip().lower()
    if not is_native_delegate and overall_status != 'pass' and is_final_goal_met is not True:
        return []
    if not is_native_delegate and current_result_type and current_result_type != 'final_deliverable':
        return []

    artifacts = payload.get('artifacts') or []
    if not isinstance(artifacts, list):
        return []

    output_dir = get_session_output_dir(session_id) if session_id else ""
    deliverable_exts = {'.xlsx', '.xls', '.docx', '.doc', '.pdf', '.png', '.jpg', '.jpeg', '.gif'}
    result = []
    for path in artifacts:
        normalized = str(path).strip()
        if not normalized:
            continue
        ext = os.path.splitext(normalized)[1].lower()
        if ext not in deliverable_exts:
            continue
        if output_dir and not _is_within_dir(os.path.realpath(normalized), os.path.realpath(output_dir)):
            continue
        result.append(normalized)
    return result


def filter_final_delivery_artifacts(artifact_paths: list[str], final_text: str) -> list[str]:
    """只保留最终回答里明确点名交付的成果文件。"""
    if not artifact_paths:
        return []

    normalized_text = (final_text or '').strip()
    if not normalized_text:
        return artifact_paths

    mentioned = []
    for path in artifact_paths:
        filename = os.path.basename(str(path))
        if filename and filename in normalized_text:
            mentioned.append(path)

    return mentioned or artifact_paths


def resolve_artifact_references(session_id: str, artifact_paths: list[str], final_text: str) -> list[str]:
    """综合完整路径和仅文件名引用，解析最终可交付文件。"""
    resolved: list[str] = []
    seen: set[str] = set()

    for path in filter_final_delivery_artifacts(artifact_paths, final_text):
        normalized = os.path.realpath(str(path))
        if normalized not in seen:
            seen.add(normalized)
            resolved.append(normalized)

    output_dir = get_session_output_dir(session_id)
    for filename in extract_generated_filenames(final_text):
        candidate = os.path.realpath(os.path.join(output_dir, filename))
        if os.path.isfile(candidate) and candidate not in seen:
            seen.add(candidate)
            resolved.append(candidate)

    return resolved


def filter_system_info(content: str) -> str:
    """过滤消息中的系统路径和环境信息"""
    import re
    
    if not content:
        return content
    
    filtered = content
    
    patterns_to_remove = [
        r'\[会话环境信息\][\s\S]*?(?=\n\n|\Z)',
        r'\[已上传的文件\][\s\S]*?(?=\n\n|\Z)',
        r'已成功生成[^，。！？\n]*[，：]\s*文件保存于[^\n]*',
    ]
    
    for pattern in patterns_to_remove:
        filtered = re.sub(pattern, '', filtered, flags=re.IGNORECASE)
    
    path_pattern = r'[A-Za-z]:\\[^\s<>\n]+\.(?:csv|xlsx|xls|json|txt|docx|doc|pdf|png|jpg|jpeg|gif)'
    filtered = re.sub(path_pattern, lambda m: '[' + m.group(0).split('\\')[-1] + ']', filtered)
    
    filtered = re.sub(r'\n{3,}', '\n\n', filtered)
    filtered = filtered.strip()
    
    return filtered


def create_agent_for_session(session_id: str, enable_search: bool = False, enable_rag: Optional[bool] = None, enable_reasoning: bool = True, model_key: Optional[str] = None):
    """为会话创建 Agent 实例（Native Runtime）"""
    session_id = _require_session_id(session_id)
    logger.info(f"创建新的智能体实例: {session_id}, runtime={settings.agent.runtime}")
    
    if enable_rag is None:
        enable_rag = settings.rag.enabled

    if model_key:
        llm_service = create_llm_service_from_preset(
            model_key,
            enable_reasoning=enable_reasoning,
        )
    else:
        llm_service = get_qwen_llm_service(
            api_key=settings.qwen.api_key,
            model_name=settings.qwen.model_name,
            temperature=settings.qwen.temperature,
            max_tokens=settings.qwen.max_tokens,
            enable_reasoning=enable_reasoning,
        )
    
    memory_dir = session_manager.get_memory_dir(session_id)
    memory = DualMemory(
        session_id=session_id,
        max_short_term=20,
        context_window=32768,
        persist_dir=memory_dir,
        llm=llm_service,
    )
    
    agent = create_flood_agent(
        llm_service=llm_service,
        memory=memory,
        session_id=session_id,
        enable_search=enable_search,
    )

    # 记录创建该 agent 时绑定的会话配置，供后续复用前比对。
    agent._session_model_key = model_key or get_default_model_key()
    agent._session_enable_search = enable_search
    agent._session_enable_rag = enable_rag
    agent._session_enable_reasoning = enable_reasoning
    
    logger.info(f"RAG 配置: enabled={enable_rag}, persist_dir={settings.rag.persist_dir}")
    
    set_rag_config(
        enabled=enable_rag,
        persist_dir=settings.rag.persist_dir,
        embedding_model=settings.rag.embedding_model,
        top_k=settings.rag.top_k,
        session_id=session_id,
    )
    
    set_session_context(
        session_id=session_id,
        output_dir=str(session_manager.get_output_dir(session_id)),
    )
    
    return agent


def get_or_create_agent(session_id: str):
    """获取或创建会话智能体"""
    session_id = _require_session_id(session_id)
    session_manager.touch_session(session_id)
    
    set_session_context(
        session_id=session_id,
        output_dir=str(session_manager.get_output_dir(session_id)),
    )
    
    with session_states_lock:
        state = dict(session_states.get(session_id, {}))
    enable_search = state.get('enable_search', False)
    enable_rag = state.get('enable_rag', settings.rag.enabled)
    enable_reasoning = state.get('enable_reasoning', True)
    model_key = state.get('model_key') or get_default_model_key()

    agent = session_manager.get_agent(session_id)
    if agent:
        current_model_key = getattr(agent, '_session_model_key', get_default_model_key())
        current_enable_search = getattr(agent, '_session_enable_search', False)
        current_enable_rag = getattr(agent, '_session_enable_rag', settings.rag.enabled)
        current_enable_reasoning = getattr(agent, '_session_enable_reasoning', True)

        if (
            current_model_key == model_key
            and current_enable_search == enable_search
            and current_enable_rag == enable_rag
            and current_enable_reasoning == enable_reasoning
        ):
            return agent

        logger.info(
            "会话 %s 配置已变更，重建 Agent: model %s -> %s, search %s -> %s, rag %s -> %s, reasoning %s -> %s",
            session_id,
            current_model_key,
            model_key,
            current_enable_search,
            enable_search,
            current_enable_rag,
            enable_rag,
            current_enable_reasoning,
            enable_reasoning,
        )
        if hasattr(session_manager, '_agents') and session_id in session_manager._agents:
            del session_manager._agents[session_id]

    _, agent = session_manager.get_or_create_session(
        session_id,
        agent_factory=lambda sid: create_agent_for_session(
            sid, enable_search=enable_search, enable_rag=enable_rag, enable_reasoning=enable_reasoning, model_key=model_key
        )
    )
    
    return agent


def get_session_upload_dir(session_id: str) -> str:
    """获取会话上传目录"""
    session_id = _require_session_id(session_id)
    return str(session_manager.get_upload_dir(session_id))


def get_session_output_dir(session_id: str) -> str:
    """获取会话输出目录"""
    session_id = _require_session_id(session_id)
    return str(session_manager.get_output_dir(session_id))


def parse_stream_chunk(chunk: str) -> Dict[str, Any]:
    """解析流式输出块"""
    if "[思考]" in chunk:
        thought = chunk.replace("[思考]", "").strip()
        return {"type": "thought", "content": thought}
    elif "[调用工具:" in chunk:
        tool_name = chunk.split(":")[1].strip("]\n ")
        return {"type": "tool_call", "content": tool_name}
    elif "[工具返回]" in chunk:
        return {"type": "tool_result", "content": "工具执行完成"}
    else:
        return {"type": "content", "content": chunk}


def sanitize_output(text: str) -> str:
    """过滤输出中的内部路径和敏感信息"""
    import re
    
    if not text:
        return text
    
    patterns_to_remove = [
        r'D:\\[^\s\n]+\\FloodAgent[^\s\n]*',
        r'C:\\[^\s\n]+\\[^\s\n]*',
        r'/app/[^\s\n]*',
        r'/home/[^\s\n]+/[^\s\n]*',
        r'session-[0-9]+-[a-z0-9]+',
        r'Invoking:\s*`[^`]+`',
        r"Invoking:\s*`GetSkill`\s*with\s*`[^`]+`",
        r"Invoking:\s*`Bash`\s*with\s*`[^`]+`",
        r"Invoking:\s*`KnowledgeSearch`\s*with\s*`[^`]+`",
        r'=== 技能【[^】]+】完整说明 ===',
        r'\\data\\sessions\\[^\s\n]+',
        r'\\skills\\[^\s\n]+',
    ]
    
    result = text
    for pattern in patterns_to_remove:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    
    result = re.sub(r'\n\s*\n\s*\n', '\n\n', result)
    result = result.strip()
    
    return result


def sanitize_tool_output(tool_name: str, content: str) -> Optional[str]:
    """过滤可安全展示给前端的工具输出。"""
    tool_name = (tool_name or '').strip()
    content = (content or '').strip()
    if not content:
        return None

    if tool_name == 'get_skill':
        return None

    sanitized = sanitize_output(content)
    if not sanitized:
        return None

    lowered = sanitized.lower()
    blocked_prefixes = (
        '错误：',
        '命令执行失败',
        '脚本执行失败',
        '命令执行超时',
        '脚本执行超时',
    )
    if sanitized.startswith(blocked_prefixes):
        return None

    if '=== 技能【' in sanitized or '【触发条件】' in sanitized:
        return None

    if '[stderr]:' in lowered:
        sanitized = sanitized.split('[stderr]:', 1)[0].strip()

    return sanitized or None


def passthrough_workflow_content(content: str) -> Optional[str]:
    """workflow 模式下尽量保留原始内容，只做最基础的空值处理。"""
    text = (content or "").strip()
    return text or None


# ============================================
# 前端静态文件服务
# ============================================
@app.route('/')
def index():
    """首页"""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/<path:path>')
def static_files(path):
    """静态文件"""
    if path.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    file_path = os.path.join(app.static_folder, path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, 'index.html')


# ============================================
# 文件上传下载 API
# ============================================
@app.route('/api/upload', methods=['POST'])
def upload_file():
    """上传文件"""
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': '没有选择文件'}), 400
        
        file = request.files['file']
        session_id = _require_session_id(request.form.get('session_id', 'default'))
        session_manager.get_or_create_session(session_id, agent_factory=None)
        
        filename = file.filename or ''

        if filename == '':
            return jsonify({'status': 'error', 'message': '没有选择文件'}), 400
        
        if not allowed_file(filename):
            return jsonify({
                'status': 'error', 
                'message': f'不支持的文件类型，允许的类型: {", ".join(ALLOWED_EXTENSIONS)}'
            }), 400
        
        # 生成唯一文件ID
        file_id = str(uuid.uuid4())
        filename = _safe_filename(filename)
        
        session_dir = get_session_upload_dir(session_id)
        filename = _dedup_filename(session_dir, filename)
        file_path = os.path.join(session_dir, filename)
        file.save(file_path)
        
        # 记录文件信息
        session_file_map = _get_session_files_map(session_id)
        session_file_map[file_id] = {
            'id': file_id,
            'name': filename,
            'path': file_path,
            'size': os.path.getsize(file_path),
            'upload_time': datetime.now().isoformat()
        }
        with session_files_lock:
            session_files[session_id] = session_file_map
        _save_session_files(session_id)
        
        logger.info(f"文件上传成功: {filename} -> {file_path}")
        
        return jsonify({
            'status': 'success',
            'file_id': file_id,
            'file_name': filename,
            'size': os.path.getsize(file_path)
        })
        
    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/files/<file_id>', methods=['DELETE'])
def delete_file(file_id: str):
    """删除上传的文件"""
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        
        session_file_map = _get_session_files_map(session_id)
        if file_id in session_file_map:
            file_info = session_file_map[file_id]
            file_path = file_info.get('path')
            
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            
            del session_file_map[file_id]
            with session_files_lock:
                session_files[session_id] = session_file_map
            _save_session_files(session_id)
            logger.info(f"文件已删除: {file_id}")
        
        return jsonify({'status': 'success', 'message': '文件已删除'})
        
    except Exception as e:
        logger.error(f"文件删除失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/files', methods=['GET'])
def list_files():
    """列出会话的所有文件"""
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        
        files = [_public_file_info(item) for item in _get_session_files_map(session_id).values()]
        
        return jsonify({'status': 'success', 'files': files})
        
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/files/<file_id>/preview', methods=['GET'])
def preview_uploaded_file(file_id: str):
    """预览会话中已上传的文件内容。"""
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        session_file_map = _get_session_files_map(session_id)
        if file_id not in session_file_map:
            return jsonify({'status': 'error', 'message': '文件不存在'}), 404

        file_info = session_file_map[file_id]
        preview = build_uploaded_file_preview(file_info)
        preview['download_url'] = f"/api/files/{file_id}/download?session_id={session_id}"
        return jsonify({'status': 'success', 'preview': preview})
    except Exception as e:
        logger.error(f"预览上传文件失败: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/files/<file_id>/download', methods=['GET'])
def download_uploaded_file(file_id: str):
    """下载或内联返回已上传的文件。"""
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        session_file_map = _get_session_files_map(session_id)
        if file_id not in session_file_map:
            return jsonify({'status': 'error', 'message': '文件不存在'}), 404

        file_info = session_file_map[file_id]
        file_path = file_info.get('path', '')
        file_name = file_info.get('name', '')

        if not os.path.exists(file_path):
            return jsonify({'error': '文件不存在'}), 404

        uploads_dir = os.path.realpath(get_session_upload_dir(session_id))
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
            '.txt': 'text/plain',
            '.json': 'application/json',
            '.csv': 'text/csv',
            '.md': 'text/markdown',
        }
        mimetype = mimetype_map.get(ext, 'application/octet-stream')

        return send_file(
            real_path,
            mimetype=mimetype,
            as_attachment=not inline,
            download_name=file_name if not inline else None,
        )
    except Exception as e:
        logger.error(f"下载上传文件失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/download/<path:file_path>')
def download_file(file_path: str):
    """下载文件"""
    try:
        from urllib.parse import unquote
        file_path = unquote(file_path)
        
        logger.info("请求下载文件")
        
        abs_path = None
        
        if file_path.startswith('/app/data/sessions/'):
            abs_path = file_path
        elif file_path.startswith('sessions/'):
            abs_path = os.path.join(DATA_DIR, file_path)
        elif '/' in file_path and not file_path.startswith('/'):
            parts = file_path.split('/')
            if len(parts) >= 3 and parts[0].startswith('session-'):
                abs_path = os.path.join(DATA_DIR, 'sessions', file_path)

        if abs_path is None:
            abs_path = os.path.abspath(file_path)
        
        sessions_dir = os.path.join(DATA_DIR, "sessions")
        sessions_dir_abs = os.path.abspath(sessions_dir)
        
        if not _is_within_dir(abs_path, sessions_dir_abs):
            logger.warning(f"非法文件路径访问: {abs_path}, 允许的目录: {sessions_dir_abs}")
            return jsonify({'error': f'非法文件路径'}), 403
        
        if not os.path.exists(abs_path):
            logger.warning(f"文件不存在: {abs_path}")
            return jsonify({'error': f'文件不存在'}), 404
        
        if not os.access(abs_path, os.R_OK):
            logger.warning(f"无读取权限: {abs_path}")
            return jsonify({'error': f'无读取权限'}), 403
        
        filename = os.path.basename(abs_path)
        
        logger.info(f"下载文件成功: {abs_path}")
        
        return send_file(
            abs_path,
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"文件下载失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/sessions/<session_id>/outputs/<path:filename>')
def get_session_output_file(session_id: str, filename: str):
    """获取会话输出目录中的文件（图片等）"""
    try:
        session_id = _require_session_id(session_id)
        output_dir = get_session_output_dir(session_id)
        file_path = os.path.join(output_dir, filename)
        
        logger.info(f"[OUTPUT_FILE] 请求: session={session_id}, filename={filename}, constructed_path={file_path}")

        real_path = os.path.realpath(file_path)
        real_output_dir = os.path.realpath(output_dir)
        if not _is_within_dir(real_path, real_output_dir):
            logger.warning(f"[OUTPUT_FILE] 非法路径: real_path={real_path}, real_output_dir={real_output_dir}")
            return jsonify({'error': '非法路径'}), 403
        
        if not os.path.exists(real_path):
            logger.warning(f"[OUTPUT_FILE] 文件不存在: {real_path}, output_dir内容={os.listdir(real_output_dir) if os.path.isdir(real_output_dir) else 'N/A'}")
            return jsonify({'error': '文件不存在'}), 404
        
        file_size = os.path.getsize(real_path)
        ext = os.path.splitext(filename)[1].lower()
        logger.info(f"[OUTPUT_FILE] 命中: ext={ext}, size={file_size}, real_path={real_path}")

        if ext == '.png':
            response = send_file(real_path, mimetype='image/png')
            logger.info(f"[OUTPUT_FILE] 返回PNG图片, size={file_size}")
            return response

        if ext in {'.jpg', '.jpeg'}:
            return send_file(real_path, mimetype='image/jpeg')

        if ext in DOWNLOADABLE_EXTENSIONS:
            if request.args.get('inline') == 'true':
                return send_file(
                    real_path,
                    mimetype=DOWNLOADABLE_EXTENSIONS[ext],
                    as_attachment=False,
                )
            return send_file(
                real_path,
                mimetype=DOWNLOADABLE_EXTENSIONS[ext],
                as_attachment=True,
                download_name=filename,
            )

        return send_file(real_path)
        
    except Exception as e:
        logger.error(f"[OUTPUT_FILE] 获取输出文件失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs')
def download_logs():
    """打包下载 logs/ 目录下所有日志文件"""
    try:
        import zipfile
        import io

        logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
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
        return send_file(
            buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'logs_{ts}.zip',
        )
    except Exception as e:
        logger.error(f"下载日志失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/sessions/<session_id>/outputs/download')
def download_session_outputs(session_id: str):
    """打包下载会话 outputs 目录下所有文件"""
    try:
        session_id = _require_session_id(session_id)
        import zipfile
        import io

        output_dir = get_session_output_dir(session_id)
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
        return send_file(
            buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'{safe_id}_outputs_{ts}.zip',
        )
    except Exception as e:
        logger.error(f"下载会话输出失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ============================================
# API 路由
# ============================================
@app.route('/api/init', methods=['POST'])
def init_agent():
    """初始化智能体"""
    try:
        data = request.get_json()
        session_id = _require_session_id(data.get('session_id', 'default'))
        enable_search = data.get('enable_search', False)
        enable_rag = data.get('enable_rag', True)
        enable_reasoning = data.get('enable_reasoning', True)
        model_key = data.get('model_key', '').strip()

        state = ensure_session_state(session_id)
        state['enable_search'] = enable_search
        state['enable_rag'] = enable_rag
        state['enable_reasoning'] = enable_reasoning
        if model_key and get_preset(model_key):
            state['model_key'] = model_key

        # init_agent 也可能在已有会话上被重复调用，先清理旧实例，确保使用最新配置重建。
        if hasattr(session_manager, '_agents') and session_id in session_manager._agents:
            del session_manager._agents[session_id]
        
        agent = get_or_create_agent(session_id)

        effective_model_key = state.get('model_key', get_default_model_key())
        preset = get_preset(effective_model_key)
        model_label = preset['label'] if preset else effective_model_key
        
        return jsonify({
            'status': 'success',
            'message': '智能体初始化成功',
            'model_key': effective_model_key,
            'model_name': model_label,
            'enable_search': enable_search,
            'enable_rag': enable_rag,
            'enable_reasoning': enable_reasoning,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/chat', methods=['POST'])
def chat():
    """流式聊天接口"""
    try:
        session_id = None
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        message = data.get('message', '')
        uploaded_files = data.get('uploaded_files', [])
        enable_reasoning = data.get('enable_reasoning', None)
        assistant_message_id = data.get('assistant_message_id', '') or f"stream-{int(time.time() * 1000)}"
        
        state = ensure_session_state(session_id)
        if enable_reasoning is None:
            enable_reasoning = state.get('enable_reasoning', True)
        
        logger.info(f"[Backend Debug] 接收到请求, enable_reasoning: {enable_reasoning}")
        
        if not message:
            return jsonify({'error': '消息不能为空'}), 400

        with session_streaming_lock:
            if session_streaming_flags.get(session_id):
                return jsonify({'error': '当前会话正在处理中，请稍后再试'}), 429
            session_streaming_flags[session_id] = True

        session_manager.get_or_create_session(session_id, agent_factory=None)
        session_manager.increment_message_count(session_id)
        session_info = session_manager.get_session_info(session_id)
        if session_info and session_info.message_count == 1 and not session_info.title:
            schedule_session_title_generation(session_id, message)
        
        with session_abort_flags_lock:
            session_abort_flags[session_id] = False

        state['is_paused'] = False

        if enable_reasoning != state.get('enable_reasoning', True):
            state['enable_reasoning'] = enable_reasoning
            if hasattr(session_manager, '_agents') and session_id in session_manager._agents:
                del session_manager._agents[session_id]
                logger.info(f"推理模式变更，重新创建Agent: {session_id}")
        
        agent = get_or_create_agent(session_id)
        
        session_file_map = _get_session_files_map(session_id)
        file_context = ""
        if uploaded_files and session_file_map:
            available_files = []
            for file_id in uploaded_files:
                if file_id in session_file_map:
                    file_info = session_file_map[file_id]
                    available_files.append(file_info)
            
            if available_files:
                file_context = "\n[已上传的文件]\n"
                for f in available_files:
                    file_context += f"- 文件名: {f['name']}, 路径: {f['path']}\n"
                file_context += "用户提到'已上传的文件'或'上传的文件'时，请使用上述路径。\n"
        
        enhanced_message = file_context + "\n\n" + message if file_context else message
        
        request_started_at = time.time()

        def _run_agent_pump(snapshot, event_buffer, resume_event, approved_artifact_paths, streamed_text_parts):
            """Background thread: consume agent.stream() and write to event_buffer via emit()."""
            is_workflow_stream = False
            final_answer_text = ""
            _pump_stop_heartbeat = threading.Event()
            buffer_lock = snapshot.get('buffer_lock')

            def _heartbeat():
                while not _pump_stop_heartbeat.wait(8):
                    if buffer_lock:
                        with buffer_lock:
                            event_buffer.append(stream_json_line({'type': 'heartbeat'}))
                    else:
                        event_buffer.append(stream_json_line({'type': 'heartbeat'}))
                    resume_event.set()

            ht = threading.Thread(target=_heartbeat, daemon=True, name=f"heartbeat-{session_id[:8]}")
            ht.start()

            def emit(payload: dict):
                return _buffered_yield(event_buffer, payload, resume_event, buffer_lock)

            try:
                for chunk in agent.stream(enhanced_message, enable_reasoning=enable_reasoning, user_message=message, abort_check=lambda: session_abort_flags.get(session_id, False)):
                    with session_abort_flags_lock:
                        is_aborted = session_abort_flags.get(session_id, False)
                    if is_aborted:
                        finish_stream_snapshot(session_id)
                        emit({'type': 'error', 'content': '会话已被用户暂停'})
                        emit({'type': 'stream_end'})
                        return
                    
                    if not isinstance(chunk, dict):
                        chunk = {"type": "content", "content": str(chunk)}

                    if chunk.get("type") in {"workflow_plan", "workflow_step"}:
                        is_workflow_stream = True
                        workflow_snapshot = snapshot.get('workflow') or {'title': '', 'steps': []}
                        if chunk.get("type") == "workflow_plan":
                            workflow_snapshot = {
                                'title': chunk.get('title', ''),
                                'steps': chunk.get('steps', []),
                            }
                        else:
                            step_key = chunk.get('step_key', '')
                            updated_steps = []
                            seen = False
                            for step in workflow_snapshot.get('steps', []):
                                if step.get('key') == step_key:
                                    merged = dict(step)
                                    raw_status = chunk.get('status', step.get('status', 'pending'))
                                    normalized_status = raw_status if raw_status in ('completed', 'running', 'pending', 'error') else ('completed' if raw_status == 'done' else raw_status)
                                    merged.update({
                                        'label': chunk.get('label', step.get('label', '')),
                                        'status': normalized_status,
                                        'title': chunk.get('title', step.get('title', '待分析')),
                                        'detail': chunk.get('detail', step.get('detail', '')),
                                        'outcome': chunk.get('outcome', step.get('outcome', '')),
                                    })
                                    updated_steps.append(merged)
                                    seen = True
                                else:
                                    updated_steps.append(step)
                            if not seen and step_key:
                                logger.warning(f"[workflow] unknown step_key='{step_key}' not in plan steps, ignoring")
                            workflow_snapshot['steps'] = updated_steps
                        snapshot['workflow'] = workflow_snapshot
                        touch_stream_snapshot(session_id)
                        emit(chunk)
                        continue

                    if chunk.get("type") in {"reasoning", "thought_delta"}:
                        if enable_reasoning or is_workflow_stream:
                            snapshot['raw_reasoning'] += chunk.get('content', '')
                            snapshot['reasoning'] = snapshot['raw_reasoning']
                            touch_stream_snapshot(session_id)
                            emit({'type': 'thought_delta', 'content': chunk.get('content', '')})
                        continue

                    if chunk.get("type") == "llm_token_error":
                        finish_stream_snapshot(session_id)
                        emit({'type': 'error', 'content': 'LLM模型服务账号Token余额不足，无法提供服务'})
                        emit({'type': 'stream_end'})
                        return

                    if chunk.get("type") == "error":
                        error_content = chunk.get('content', '处理请求时出错')
                        is_timeout = "超时" in error_content or "timeout" in error_content.lower() or "timed out" in error_content.lower()
                        emit({'type': 'error', 'content': error_content})
                        if is_timeout:
                            finish_stream_snapshot(session_id)
                            emit({'type': 'stream_end'})
                            _pump_stop_heartbeat.set()
                            return
                        continue

                    if chunk.get("type") == "permission_ask":
                        touch_stream_snapshot(session_id)
                        emit(chunk)
                        continue

                    if chunk.get("type") == "artifact_warning":
                        emit({'type': 'artifact_warning', 'content': chunk.get('content', '')})
                        continue

                    if chunk.get("type") == "memory_status":
                        touch_stream_snapshot(session_id)
                        emit(chunk)
                        continue

                    if chunk.get("type") == "final_text":
                        final_answer_text = sanitize_output(chunk.get('content', '') or '')
                        if final_answer_text:
                            snapshot['content'] = final_answer_text
                            touch_stream_snapshot(session_id)
                        continue

                    if chunk.get("type") in {"tool_status", "action_start"}:
                        call_id = chunk.get('call_id', '')
                        safe_chunk = {
                            'type': 'action_start',
                            'tool_name': chunk.get('tool_name', ''),
                            'status': chunk.get('status', 'running'),
                        }
                        if call_id:
                            safe_chunk['call_id'] = call_id
                        tool_input = chunk.get('tool_input', '')
                        if tool_input:
                            safe_chunk['tool_input'] = tool_input
                        if tool_input and chunk.get('tool_name', '') in ('SubAgent', 'ParallelSubAgent', 'ParallelTask'):
                            safe_chunk['delegation'] = {
                                'task': '',
                                'skill_name': '',
                                'label': 'SubAgent',
                            }
                        if chunk.get('status') == 'error':
                            safe_chunk['content'] = '工具执行失败，智能体正在继续处理。'
                        touch_stream_snapshot(session_id)
                        emit(safe_chunk)
                        continue

                    if chunk.get("type") in {"tool_result", "action_end"}:
                        original_content = chunk.get("content", "")
                        tool_name = chunk.get('tool_name', '')
                        call_id = chunk.get('call_id', '')
                        filtered_content = passthrough_workflow_content(original_content) if is_workflow_stream else sanitize_tool_output(tool_name, original_content)
                        if not filtered_content:
                            continue
                        
                        logger.info(f"action_end 内容: {filtered_content[:200] if len(filtered_content) > 200 else filtered_content}")

                        validated_paths = extract_validated_artifact_paths(original_content, session_id=session_id)
                        logger.info(f"[ARTIFACT] extract_validated_artifact_paths result: paths={validated_paths}, tool={tool_name}")
                        if validated_paths:
                            for vp in validated_paths:
                                rp = os.path.realpath(vp)
                                if rp not in {os.path.realpath(p) for p in approved_artifact_paths}:
                                    approved_artifact_paths.append(vp)

                        result_event = {'type': 'action_end', 'tool_name': tool_name, 'content': filtered_content}
                        if call_id:
                            result_event['call_id'] = call_id

                        if tool_name == 'SubAgent':
                            try:
                                payload = json.loads(original_content)
                                if isinstance(payload, dict):
                                    task_desc = payload.get('task', '')
                                    summary = payload.get('summary', '')
                                    stage_label = payload.get('stage_label', 'Execution Specialist')
                                    skill_name = payload.get('skill_name', '')
                                    label = f"{stage_label}: {task_desc}" if task_desc else stage_label
                                    if skill_name:
                                        label += f" (skill: {skill_name})"
                                    result_event['delegation'] = {
                                        'task': task_desc,
                                        'summary': summary[:500] if summary else '',
                                        'label': label,
                                        'skill_name': skill_name,
                                    }
                            except (json.JSONDecodeError, TypeError):
                                pass

                        snapshot['tool_results'].append({'tool_name': tool_name, 'content': filtered_content})
                        touch_stream_snapshot(session_id)
                        emit(result_event)
                        continue

                    if chunk.get("type") in {"token", "answer_delta"} and chunk.get("content"):
                        raw_content = chunk["content"]
                        chunk["content"] = raw_content if is_workflow_stream else sanitize_output(raw_content)
                        streamed_text_parts.append(chunk["content"])
                        snapshot['content'] += chunk['content']
                        touch_stream_snapshot(session_id)
                        emit({'type': 'answer_delta', 'content': chunk["content"]})
                        continue

                final_text = final_answer_text.strip() or ''.join(streamed_text_parts)
                approved_artifact_paths[:] = resolve_artifact_references(session_id, approved_artifact_paths, final_text)
                logger.info(f"[ARTIFACT] approved_artifact_paths after resolve: {approved_artifact_paths}")

                approved_artifact_events: list[dict[str, Any]] = []
                emitted_paths: set[str] = set()
                for artifact_path in approved_artifact_paths:
                    artifact_event = build_artifact_event(artifact_path, session_id, emitted_paths)
                    if artifact_event:
                        logger.info(f"[ARTIFACT] built event: type={artifact_event.get('type')}, filename={artifact_event.get('filename')}, image_url={artifact_event.get('image_url', '')}, download_url={artifact_event.get('download_url', '')}, size={artifact_event.get('size')}")
                        approved_artifact_events.append(artifact_event)
                    else:
                        logger.warning(f"[ARTIFACT] build_artifact_event returned None for path: {artifact_path}")

                snapshot['artifacts'] = approved_artifact_events
                touch_stream_snapshot(session_id)
                save_session_artifact_events(session_id, approved_artifact_events)
                logger.info(f"[ARTIFACT] total approved events: {len(approved_artifact_events)}, saved to approved_artifacts.json")

                final_event = {
                    'type': 'final',
                    'content': final_text,
                    'artifacts': [_sanitize_artifact_event(e) for e in approved_artifact_events],
                }
                snapshot['content'] = final_text
                touch_stream_snapshot(session_id)

                # agent 内部已自动保存对话历史，无需额外调用

                emit(final_event)

                _pump_stop_heartbeat.set()
                finish_stream_snapshot(session_id)
                logger.info(f"[ARTIFACT] stream finished, sending stream_end. SSE stream connection will close now.")
                emit({'type': 'stream_end'})

                session_info = session_manager.get_session_info(session_id)
                if session_info and not session_info.title:
                    from memory.session_manager import SessionManager as _SM
                    title = _SM._extract_title_from_user_input(message)
                    session_manager.update_session_title(session_id, title)

            except Exception as e:
                logger.error(f"流式输出错误: {e}")
                _pump_stop_heartbeat.set()
                finish_stream_snapshot(session_id)
                error_msg = {
                    'type': 'error',
                    'content': f'处理请求时出错: {str(e)}'
                }
                emit(error_msg)
                emit({'type': 'stream_end'})
            finally:
                _pump_stop_heartbeat.set()
                if snapshot.get('is_streaming'):
                    finish_stream_snapshot(session_id)
                    logger.info("_run_agent_pump interrupted, force-finished stream snapshot")

        def generate():
            """Buffer-following reader: yield from event_buffer."""
            snapshot = init_stream_snapshot(session_id, assistant_message_id)
            approved_artifact_paths: list[str] = []
            streamed_text_parts: list[str] = []
            event_buffer = snapshot['event_buffer']
            resume_event = snapshot['resume_event']
            buffer_lock = snapshot.get('buffer_lock', threading.Lock())

            pump_thread = threading.Thread(
                target=_run_agent_pump,
                args=(snapshot, event_buffer, resume_event, approved_artifact_paths, streamed_text_parts),
                daemon=True,
                name=f"agent-pump-{session_id[:8]}",
            )
            pump_thread.start()

            replayed = 0
            try:
                while True:
                    with buffer_lock:
                        buf_len = len(event_buffer)
                    while replayed < buf_len:
                        yield event_buffer[replayed]
                        replayed += 1

                    if not snapshot.get('is_streaming'):
                        break

                    resume_event.wait(timeout=5.0)
                    resume_event.clear()

                with buffer_lock:
                    while replayed < len(event_buffer):
                        yield event_buffer[replayed]
                        replayed += 1
            finally:
                with session_streaming_lock:
                    session_streaming_flags.pop(session_id, None)
        
        return Response(
            generate(),
            mimetype='application/x-ndjson',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            }
        )
        
    except Exception as e:
        logger.error(f"聊天接口错误: {e}")
        if session_id:
            with session_streaming_lock:
                session_streaming_flags.pop(session_id, None)
        return jsonify({'error': str(e)}), 500


@app.route('/api/stream/resume', methods=['GET'])
def stream_resume():
    """恢复断开的流式连接，回放已缓冲的事件并继续流式输出"""
    session_id = _require_session_id(request.args.get('session_id', 'default'))
    after_index = int(request.args.get('after_index', '0'))
    state = ensure_session_state(session_id)
    snapshot = state.get('stream_snapshot')

    if not snapshot:
        return jsonify({'status': 'idle', 'message': '没有正在进行的流'}), 200

    def replay_and_continue():
        event_buffer = snapshot.get('event_buffer', [])
        buffer_lock = snapshot.get('buffer_lock', threading.Lock())
        replayed = after_index
        stale_rounds = 0
        max_stale_rounds = 6

        while True:
            with buffer_lock:
                buf_len = len(event_buffer)
            while replayed < buf_len:
                yield event_buffer[replayed]
                replayed += 1
                stale_rounds = 0

            if not snapshot.get('is_streaming'):
                break

            resume_event = snapshot.get('resume_event')
            if resume_event:
                resume_event.wait(timeout=5.0)
                resume_event.clear()
            else:
                time.sleep(0.5)

            with buffer_lock:
                new_buf_len = len(event_buffer)
            if new_buf_len == buf_len:
                stale_rounds += 1
                if stale_rounds >= max_stale_rounds:
                    logger.info("stream_resume: no new events for 30s, closing")
                    break
            else:
                stale_rounds = 0

        with buffer_lock:
            while replayed < len(event_buffer):
                yield event_buffer[replayed]
                replayed += 1

    return Response(
        replay_and_continue(),
        mimetype='application/x-ndjson',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@app.route('/api/clear', methods=['POST'])
def clear_memory():
    """清空会话记忆"""
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        
        agent = session_manager.get_agent(session_id)
        if agent:
            agent.clear_memory()
            logger.info(f"已清空会话记忆: {session_id}")
        
        return jsonify({
            'status': 'success',
            'message': '记忆已清空'
        })
    except Exception as e:
        logger.error(f"清空记忆失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/sessions', methods=['GET'])
def list_sessions():
    """列出所有会话"""
    try:
        sessions = session_manager.get_all_sessions()
        result = []
        for s in sessions:
            session_data = s.to_dict()
            session_data['title'] = session_manager.get_session_title(s.session_id)
            session_data['updated_at'] = session_data.get('last_active', '')
            result.append(session_data)
        
        return jsonify({
            'status': 'success',
            'sessions': result,
            'stats': session_manager.get_stats()
        })
    except Exception as e:
        logger.error(f"获取会话列表失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/sessions/save', methods=['POST'])
def save_current_session():
    """保存当前会话的对话历史"""
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        
        agent = session_manager.get_agent(session_id)
        if agent and hasattr(agent.memory, 'save_chat_history'):
            agent.memory.save_chat_history()
            return jsonify({
                'status': 'success',
                'message': '会话已保存'
            })
        else:
            return jsonify({
                'status': 'success',
                'message': '会话已保存（无Agent实例）'
            })
    except Exception as e:
        logger.error(f"保存会话失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/sessions/cleanup', methods=['POST'])
def trigger_cleanup():
    """手动触发清理"""
    try:
        session_manager.cleanup_expired_sessions()
        session_manager.cleanup_old_files()
        session_manager.cleanup_idle_sessions()
        
        return jsonify({
            'status': 'success',
            'message': '清理完成',
            'stats': session_manager.get_stats()
        })
    except Exception as e:
        logger.error(f"清理失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/sessions/stats', methods=['GET'])
def get_session_stats():
    """获取会话统计"""
    try:
        return jsonify({
            'status': 'success',
            'stats': session_manager.get_stats()
        })
    except Exception as e:
        logger.error(f"获取统计失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/sessions/<session_id>', methods=['GET'])
def get_session(session_id: str):
    """获取会话详情（包括对话历史）"""
    try:
        session_id = _require_session_id(session_id)
        info = session_manager.get_session_info(session_id)
        if not info:
            return jsonify({
                'status': 'error',
                'message': '会话不存在'
            }), 404
        
        messages = session_manager.get_session_messages(session_id)
        title = session_manager.get_session_title(session_id)
        
        filtered_messages = []
        for msg in messages:
            filtered_msg = {
                'role': msg.get('role', ''),
                'content': filter_system_info(msg.get('content', ''))
            }
            if msg.get('reasoning'):
                filtered_msg['reasoning'] = filter_system_info(msg.get('reasoning', ''))
            if msg.get('tool_calls'):
                filtered_msg['tool_calls'] = [
                    {
                        'tool_name': item.get('tool_name', ''),
                        'tool_input': filter_system_info(str(item.get('tool_input', ''))),
                        'tool_output': filter_system_info(str(item.get('tool_output', ''))),
                    }
                    for item in msg.get('tool_calls', [])
                    if isinstance(item, dict)
                ]
            filtered_messages.append(filtered_msg)
        
        # 只在流式输出进行中返回 in_progress，已完成的不返回（避免与 messages 重复）
        snapshot = ensure_session_state(session_id).get('stream_snapshot')
        in_progress = _serialize_snapshot(snapshot) if (snapshot and snapshot.get('is_streaming')) else None
        
        return jsonify({
            'status': 'success',
            'session': {
                **info.to_dict(),
                'title': title,
            },
            'messages': filtered_messages,
            'artifacts': list_session_artifact_events(session_id),
            'in_progress': in_progress,
        })
    except Exception as e:
        logger.error(f"获取会话详情失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def delete_session_route(session_id: str):
    """删除会话"""
    try:
        session_id = _require_session_id(session_id)
        
        from agent.runtime.adapters.flask_permission_api import handle_permission_cancel_session
        cancelled = handle_permission_cancel_session(session_id)
        if cancelled:
            logger.info(f"删除会话 {session_id}: 已取消 {cancelled} 个 pending ASK")
        
        session_manager.delete_session(session_id)
        
        _remove_session_files(session_id)
        
        with session_states_lock:
            session_states.pop(session_id, None)
        with session_abort_flags_lock:
            session_abort_flags.pop(session_id, None)
        with session_streaming_lock:
            session_streaming_flags.pop(session_id, None)
        
        logger.info(f"已删除会话: {session_id}")
        
        return jsonify({
            'status': 'success',
            'message': f'会话 {session_id} 已删除'
        })
    except Exception as e:
        logger.error(f"删除会话失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0.0'
    })


@app.route('/favicon.ico')
def favicon():
    return ('', 204)


@app.route('/api/models', methods=['GET'])
def list_models():
    """获取可用模型列表"""
    try:
        models = get_models_list()
        default_key = get_default_model_key()
        return jsonify({
            'status': 'success',
            'default_model_key': default_key,
            'models': models,
        })
    except Exception as e:
        logger.error(f"获取模型列表失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/config', methods=['GET'])
def get_config():
    """获取配置信息"""
    default_key = get_default_model_key()
    preset = get_preset(default_key)
    model_label = preset['label'] if preset else default_key
    return jsonify({
        'model_key': default_key,
        'model_name': model_label,
        'tools': [
            {'name': 'read_data_file', 'desc': '读取数据文件'},
            {'name': 'prepare_forecast_input', 'desc': '提取预测输入'},
            {'name': 'flood_prediction', 'desc': 'Chronos-2 预测'},
            {'name': 'model_validation', 'desc': '滚动精度验证'},
            {'name': 'write_word_document', 'desc': '生成 Word 报告'},
        ]
    })


@app.route('/api/scheduled-tasks', methods=['GET'])
def list_scheduled_task_api():
    """查询定时任务列表。"""
    try:
        session_id = request.args.get('session_id', '')
        include_all = request.args.get('include_all', '0') == '1'
        if session_id:
            session_id = _require_session_id(session_id)
        tasks = get_scheduled_task_runtime().list_tasks(session_id='' if include_all else session_id)
        return jsonify({
            'status': 'success',
            'count': len(tasks),
            'tasks': tasks,
        })
    except Exception as e:
        logger.error(f"查询定时任务失败: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/scheduled-tasks/<task_id>', methods=['GET'])
def get_scheduled_task_api(task_id: str):
    """查询定时任务详情。"""
    try:
        task = get_scheduled_task_runtime().get_task(task_id)
        if not task:
            return jsonify({'status': 'error', 'message': '定时任务不存在'}), 404
        return jsonify({'status': 'success', 'task': task})
    except Exception as e:
        logger.error(f"查询定时任务详情失败: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/scheduled-tasks/<task_id>', methods=['PATCH'])
def update_scheduled_task_api(task_id: str):
    """修改定时任务基础字段。"""
    try:
        data = request.get_json() or {}
        updates = {key: data[key] for key in ('command', 'enabled', 'run_time', 'scheduled_at', 'repeat', 'status') if key in data}
        task = get_scheduled_task_runtime().update_task(task_id, **updates)
        return jsonify({'status': 'success', 'task': task})
    except Exception as e:
        logger.error(f"修改定时任务失败: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/scheduled-tasks/<task_id>', methods=['DELETE'])
def delete_scheduled_task_api(task_id: str):
    """删除定时任务。"""
    try:
        task = get_scheduled_task_runtime().delete_task(task_id)
        return jsonify({'status': 'success', 'task': task})
    except Exception as e:
        logger.error(f"删除定时任务失败: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/scheduled-tasks/<task_id>/artifacts', methods=['GET'])
def list_scheduled_task_artifacts_api(task_id: str):
    """查询定时任务最近一次新增产物。"""
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
        logger.error(f"查询定时任务产物失败: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/memory/stats', methods=['GET'])
def get_memory_stats():
    """获取记忆系统统计信息"""
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        
        agent = session_manager.get_agent(session_id)
        if agent:
            stats = agent.get_memory_summary()
            return jsonify({
                'status': 'success',
                'stats': stats
            })
        else:
            return jsonify({
                'status': 'success',
                'stats': {
                    'message': '会话尚未初始化'
                }
            })
    except Exception as e:
        logger.error(f"获取记忆统计失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/memory/heartbeat', methods=['POST'])
def trigger_heartbeat():
    """手动触发心跳归纳"""
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        
        agent = session_manager.get_agent(session_id)
        if agent:
            if hasattr(agent.memory, 'force_heartbeat'):
                result = agent.memory.force_heartbeat()
                return jsonify({
                    'status': 'success',
                    'message': result
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': '当前记忆系统不支持心跳归纳'
                }), 400
        else:
            return jsonify({
                'status': 'error',
                'message': '会话不存在'
            }), 404
    except Exception as e:
        logger.error(f"触发心跳失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/memory/search', methods=['POST'])
def search_long_term_memory():
    """搜索长期记忆"""
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        query = data.get('query', '')
        top_k = data.get('top_k', 5)
        
        if not query:
            return jsonify({
                'status': 'error',
                'message': '查询内容不能为空'
            }), 400
        
        agent = session_manager.get_agent(session_id)
        if agent:
            if hasattr(agent.memory, 'search_long_term'):
                results = agent.memory.search_long_term(query, top_k)
                return jsonify({
                    'status': 'success',
                    'results': results
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': '当前记忆系统不支持长期记忆搜索'
                }), 400
        else:
            return jsonify({
                'status': 'error',
                'message': '会话不存在'
            }), 404
    except Exception as e:
        logger.error(f"搜索长期记忆失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/memory/add', methods=['POST'])
def add_long_term_memory():
    """手动添加长期记忆"""
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        content = data.get('content', '')
        entry_type = data.get('type', 'note')
        
        if not content:
            return jsonify({
                'status': 'error',
                'message': '记忆内容不能为空'
            }), 400
        
        agent = session_manager.get_agent(session_id)
        if agent:
            if hasattr(agent.memory, 'add_long_term_memory'):
                success = agent.memory.add_long_term_memory(content, entry_type)
                return jsonify({
                    'status': 'success' if success else 'error',
                    'message': '已添加到长期记忆' if success else '添加失败（可能重复）'
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': '当前记忆系统不支持手动添加长期记忆'
                }), 400
        else:
            return jsonify({
                'status': 'error',
                'message': '会话不存在'
            }), 404
    except Exception as e:
        logger.error(f"添加长期记忆失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/permission/respond', methods=['POST'])
def permission_respond():
    """用户对权限 ASK 请求的响应"""
    try:
        from agent.runtime.adapters.flask_permission_api import handle_permission_respond
        data = request.get_json() or {}
        result, status_code = handle_permission_respond(data)
        return jsonify(result), status_code
    except Exception as e:
        logger.error(f"权限确认响应失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/permission/pending', methods=['GET'])
def permission_pending():
    """查询当前所有 pending ASK 请求"""
    try:
        from agent.runtime.adapters.flask_permission_api import handle_permission_pending
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        result, status_code = handle_permission_pending(session_id)
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/session/pause', methods=['POST'])
def pause_session():
    """暂停会话"""
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        
        with session_abort_flags_lock:
            session_abort_flags[session_id] = True
        ensure_session_state(session_id)['is_paused'] = True

        try:
            from agent.runtime.adapters.flask_permission_api import handle_permission_cancel_session
            cancelled = handle_permission_cancel_session(session_id)
            if cancelled:
                logger.info(f"暂停会话 {session_id}: 已取消 {cancelled} 个 pending ASK")
        except Exception:
            pass
        
        logger.info(f"会话已暂停: {session_id}")
        
        return jsonify({
            'status': 'success',
            'message': '会话已暂停'
        })
    except Exception as e:
        logger.error(f"暂停会话失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/session/resume', methods=['POST'])
def resume_session():
    """恢复会话"""
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        
        with session_abort_flags_lock:
            session_abort_flags[session_id] = False
        ensure_session_state(session_id)['is_paused'] = False
        
        logger.info(f"会话已恢复: {session_id}")
        
        return jsonify({
            'status': 'success',
            'message': '会话已恢复'
        })
    except Exception as e:
        logger.error(f"恢复会话失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/session/status', methods=['GET'])
def get_session_status():
    """获取会话状态"""
    try:
        session_id = _require_session_id(request.args.get('session_id', 'default'))
        state = ensure_session_state(session_id)
        
        safe_state = {k: v for k, v in state.items() if k != 'stream_snapshot'}
        snapshot = state.get('stream_snapshot')
        in_progress = _serialize_snapshot(snapshot) if (snapshot and snapshot.get('is_streaming')) else None
        safe_state['stream_snapshot'] = in_progress
        return jsonify({
            'status': 'success',
            'session_state': safe_state,
            'in_progress': in_progress
        })
    except Exception as e:
        logger.error(f"获取会话状态失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/session/config', methods=['POST'])
def update_session_config():
    """更新会话配置（模型、搜索、RAG和推理模式功能开关）"""
    try:
        data = request.get_json() or {}
        session_id = _require_session_id(data.get('session_id', 'default'))
        enable_search = data.get('enable_search')
        enable_rag = data.get('enable_rag')
        enable_reasoning = data.get('enable_reasoning')
        model_key = data.get('model_key')
        
        state = ensure_session_state(session_id)
        
        config_changed = False
        status_messages = []
        
        if model_key is not None and model_key != state.get('model_key'):
            preset = get_preset(model_key)
            if preset is None:
                return jsonify({'status': 'error', 'message': f'未知的模型: {model_key}'}), 400
            state['model_key'] = model_key
            config_changed = True
            status_msg = f"模型已切换为 {preset['label']}"
            status_messages.append(status_msg)
            logger.info(f"会话 {session_id} {status_msg}")
            if not preset.get('supports_reasoning') and state.get('enable_reasoning'):
                state['enable_reasoning'] = False
                status_messages.append("深度思考模式已关闭（当前模型不支持）")
        
        if enable_search is not None and enable_search != state.get('enable_search'):
            state['enable_search'] = enable_search
            config_changed = True
            status_msg = f"联网搜索功能已{'启用' if enable_search else '关闭'}"
            status_messages.append(status_msg)
            logger.info(f"会话 {session_id} {status_msg}")
        
        if enable_rag is not None and enable_rag != state.get('enable_rag'):
            state['enable_rag'] = enable_rag
            config_changed = True
            status_msg = f"知识库检索(RAG)功能已{'启用' if enable_rag else '关闭'}"
            status_messages.append(status_msg)
            logger.info(f"会话 {session_id} {status_msg}")
        
        if enable_reasoning is not None and enable_reasoning != state.get('enable_reasoning'):
            effective_model_key = state.get('model_key', get_default_model_key())
            preset = get_preset(effective_model_key)
            if enable_reasoning and preset and not preset.get('supports_reasoning'):
                return jsonify({'status': 'error', 'message': f'当前模型 {preset["label"]} 不支持深度思考'}), 400
            state['enable_reasoning'] = enable_reasoning
            config_changed = True
            status_msg = f"深度思考模式已{'启用' if enable_reasoning else '关闭'}"
            status_messages.append(status_msg)
            logger.info(f"会话 {session_id} {status_msg}")
        
        if config_changed:
            agent = session_manager.get_agent(session_id)
            if agent and hasattr(agent, 'memory'):
                system_notice = f"[系统通知] 功能状态更新：{', '.join(status_messages)}。请在后续对话中使用更新后的功能状态。"
                if hasattr(agent.memory, 'add_user_message'):
                    agent.memory.add_user_message(system_notice)
                    if hasattr(agent.memory, 'add_ai_message'):
                        agent.memory.add_ai_message("收到，已更新功能状态配置。")
                    logger.info(f"已向会话 {session_id} 注入功能状态变更通知")
            
            if hasattr(session_manager, '_agents') and session_id in session_manager._agents:
                del session_manager._agents[session_id]
                logger.info(f"会话 {session_id} Agent实例已清除，将在下次请求时重新创建")
        
        effective_model_key = state.get('model_key', get_default_model_key())
        preset = get_preset(effective_model_key)
        model_label = preset['label'] if preset else effective_model_key
        return jsonify({
            'status': 'success',
            'message': '配置已更新',
            'config': {
                'model_key': effective_model_key,
                'model_name': model_label,
                'enable_search': state.get('enable_search', False),
                'enable_rag': state.get('enable_rag', True),
                'enable_reasoning': state.get('enable_reasoning', True),
            }
        })
    except Exception as e:
        logger.error(f"更新会话配置失败: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# ============================================
# 错误处理
# ============================================
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500


# ============================================
# 主程序入口
# ============================================
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='FloodAgent Web Server')
    parser.add_argument('--host', default='0.0.0.0', help='主机地址')
    parser.add_argument('--port', type=int, default=13014, help='端口号')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    
    args = parser.parse_args()
    
    session_manager.start_cleanup_thread()
    
    logger.info("预加载 Embedding 模型与知识检索器...")
    try:
        from tools.base_tools import _get_retriever, set_rag_config
        set_rag_config(enabled=True, persist_dir="./data/vector_store", embedding_model="BAAI/bge-base-zh-v1.5", top_k=10, session_id=None)
        _get_retriever()
        logger.info("Embedding 模型与知识检索器预加载完成")
    except Exception as e:
        logger.warning(f"预加载知识检索器失败（不影响功能）: {e}")
    
    logger.info(f"启动 FloodAgent Web 服务器")
    logger.info(f"访问地址: http://{args.host}:{args.port}")
    logger.info(f"数据目录: {DATA_DIR}")
    logger.info(f"最大会话数: {session_manager.config['max_active_sessions']}")
    
    try:
        if args.debug:
            app.run(
                host=args.host,
                port=args.port,
                debug=True,
                threaded=True
            )
        else:
            import platform
            if platform.system() == 'Windows':
                from waitress import serve
                logger.info(f"使用 waitress 生产服务器 (Windows)")
                serve(app, host=args.host, port=args.port, threads=8, channel_timeout=300)
            else:
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
                logger.info(f"使用 gunicorn 生产服务器 (Linux)")
                StandaloneApplication(app, options).run()
    finally:
        session_manager.stop_cleanup_thread()
        session_manager.save_all()
        logger.info("服务器已关闭，所有会话已保存")
