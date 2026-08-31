"""Chainlit 历史会话持久化（本地零部署版）。

Chainlit 的历史会话侧栏仅在 ``requireLogin && dataPersistence`` 同时为真时展示，
而 ``/project/threads`` 等接口在未鉴权时直接 401。本地桌面工具不应弹出登录页，
因此这里做了三件事：

1. SqlAlchemyDataLayer + SQLite（数据落在 ``<workspace>/.floodmind/chainlit/``），
   建表脚本内置于本模块（SqlAlchemyDataLayer 不会自动建表）。
2. LocalStorageClient：文件/图片元素落盘，并注册本地路由回源，
   让历史会话里的产物在重启后仍可查看。
3. 无感本地鉴权补丁：把 Chainlit 的鉴权解析为固定本地用户
   （与 gateway 回环免鉴权、Jupyter/Ollama 本地惯例一致，用户零交互）。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

LOCAL_USER_IDENTIFIER = "floodmind-local"
FILES_ROUTE = "/floodmind-files"

# SqlAlchemyDataLayer 的 SQL 全部动态拼列，这里提供各表的超集列定义；
# 启动时对已存在的旧表做 PRAGMA 比对，缺列则 ALTER TABLE 补齐。
_SCHEMA: Dict[str, Dict[str, str]] = {
    "users": {
        "id": "TEXT PRIMARY KEY",
        "identifier": "TEXT",
        "createdAt": "TEXT",
        "metadata": "TEXT",
    },
    "threads": {
        "id": "TEXT PRIMARY KEY",
        "createdAt": "TEXT",
        "name": "TEXT",
        "userId": "TEXT",
        "userIdentifier": "TEXT",
        "tags": "TEXT",
        "metadata": "TEXT",
    },
    "steps": {
        "id": "TEXT PRIMARY KEY",
        "name": "TEXT",
        "type": "TEXT",
        "threadId": "TEXT",
        "parentId": "TEXT",
        "disableFeedback": "NUMERIC",
        "streaming": "NUMERIC",
        "waitForAnswer": "NUMERIC",
        "isError": "NUMERIC",
        "isFailure": "NUMERIC",
        "metadata": "TEXT",
        "tags": "TEXT",
        "input": "TEXT",
        "output": "TEXT",
        "createdAt": "TEXT",
        "start": "TEXT",
        "end": "TEXT",
        "generation": "TEXT",
        "showInput": "TEXT",
        "defaultOpen": "NUMERIC",
        "autoCollapse": "NUMERIC",
        "language": "TEXT",
        "forceMessage": "NUMERIC",
        "forId": "TEXT",
        "feedback": "TEXT",
        "tracing": "TEXT",
    },
    "elements": {
        "id": "TEXT PRIMARY KEY",
        "threadId": "TEXT",
        "forId": "TEXT",
        "type": "TEXT",
        "chainlitKey": "TEXT",
        "url": "TEXT",
        "objectKey": "TEXT",
        "name": "TEXT",
        "mime": "TEXT",
        "display": "TEXT",
        "size": "TEXT",
        "language": "TEXT",
        "page": "NUMERIC",
        "autoPlay": "NUMERIC",
        "playerConfig": "TEXT",
        "props": "TEXT",
    },
    "feedbacks": {
        "id": "TEXT PRIMARY KEY",
        "forId": "TEXT",
        "threadId": "TEXT",
        "value": "NUMERIC",
        "comment": "TEXT",
    },
}


def _ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        for table, columns in _SCHEMA.items():
            cols_sql = ", ".join(f'"{name}" {decl}' for name, decl in columns.items())
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols_sql})')
            existing = {
                row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')
            }
            for name, decl in columns.items():
                if name not in existing and "PRIMARY KEY" not in decl:
                    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {decl}')
        conn.commit()
    finally:
        conn.close()


def _ensure_jwt_secret(db_dir: Path) -> None:
    """ensure_jwt_secret() 只在 requireLogin 时校验环境变量；持久化一个随机值。"""
    if os.environ.get("CHAINLIT_AUTH_SECRET"):
        return
    secret_file = db_dir / ".jwt_secret"
    try:
        if secret_file.exists():
            secret = secret_file.read_text(encoding="utf-8").strip()
        else:
            secret = secrets.token_urlsafe(32)
            db_dir.mkdir(parents=True, exist_ok=True)
            secret_file.write_text(secret, encoding="utf-8")
        os.environ["CHAINLIT_AUTH_SECRET"] = secret
    except OSError:
        os.environ.setdefault("CHAINLIT_AUTH_SECRET", "floodmind-local-secret")


class LocalStorageClient:
    """把 Chainlit 元素文件落到本地磁盘（其余云存储客户端的本地替代品）。"""

    def __init__(self, files_dir: Path):
        self._files_dir = files_dir.resolve()
        self._files_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, object_key: str) -> Optional[Path]:
        target = (self._files_dir / object_key).resolve()
        if not target.is_relative_to(self._files_dir):
            return None
        return target

    async def upload_file(
        self,
        object_key: str,
        data: Any,
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = self._resolve(object_key)
        if target is None:
            raise ValueError(f"illegal object key: {object_key}")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = data.encode("utf-8") if isinstance(data, str) else data
        with open(target, "wb") as f:
            f.write(payload)
        return {"url": f"{FILES_ROUTE}/{quote(object_key)}", "object_key": object_key}

    async def delete_file(self, object_key: str) -> bool:
        target = self._resolve(object_key)
        if target is None or not target.is_file():
            return False
        try:
            target.unlink()
            return True
        except OSError:
            return False

    async def get_read_url(self, object_key: str) -> str:
        return f"{FILES_ROUTE}/{quote(object_key)}"

    async def close(self) -> None:
        return None


def _register_file_route(files_dir: Path) -> None:
    """注册产物文件回源路由，并插到 SPA 兜底路由 ``/{full_path:path}`` 之前。"""
    from fastapi.responses import FileResponse, PlainTextResponse
    from chainlit.server import app as chainlit_app

    files_dir = Path(files_dir).resolve()

    async def _serve(key: str):
        target = (files_dir / key).resolve()
        if not target.is_relative_to(files_dir) or not target.is_file():
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(target)

    chainlit_app.add_api_route(f"{FILES_ROUTE}/{{key:path}}", _serve, methods=["GET"])
    routes = chainlit_app.router.routes
    ours = routes.pop()
    catch_all = next(
        (
            i
            for i, r in enumerate(routes)
            if getattr(r, "path", "") == "/{full_path:path}"
        ),
        None,
    )
    if catch_all is None:
        routes.append(ours)
    else:
        routes.insert(catch_all, ours)


def _patch_local_auth() -> None:
    """把鉴权解析为固定本地用户，浏览器零交互（不弹登录页、不发 token）。"""
    import chainlit as cl
    import chainlit.auth as cl_auth
    import chainlit.socket as cl_socket
    from chainlit.data import get_data_layer
    from chainlit.user import PersistedUser

    async def _authenticate_local(token=None, *args, **kwargs) -> Any:
        layer = get_data_layer()
        if layer is None:
            return cl.User(identifier=LOCAL_USER_IDENTIFIER, display_name="FloodMind")
        persisted = await layer.get_user(LOCAL_USER_IDENTIFIER)
        if persisted is None:
            persisted = await layer.create_user(
                cl.User(identifier=LOCAL_USER_IDENTIFIER, display_name="FloodMind")
            )
        assert isinstance(persisted, PersistedUser)
        return persisted

    # get_current_user 的实现体在调用期从 chainlit.auth 全局取这两个名字，
    # 因此替换模块全局即可生效（含 FastAPI Depends 与 websocket 两条路径）。
    cl_auth.require_login = lambda: True
    cl_auth.authenticate_user = _authenticate_local
    cl_socket.require_login = lambda: True

    # websocket 握手在无 cookie 时根本不会调 get_current_user（直接拒绝），
    # 本地无感登录需要替换握手逻辑本身。
    async def _authenticate_connection(environ) -> Any:
        try:
            user = await cl_socket.get_current_user(token=None)
        except Exception as exc:
            logger.warning("本地 websocket 鉴权失败: %s", exc)
            user = None
        if user:
            return user, None
        return None, None

    cl_socket._authenticate_connection = _authenticate_connection


def _apply_branding(workspace_root: Path) -> None:
    """把 FloodMind 的名称/logo/favicon/头像落到 APP_ROOT/public（启动 cwd=工作区）。"""
    import shutil

    from chainlit.config import config

    config.ui.name = "FloodMind"

    # 修正"创建新对话"确认弹窗文案：有数据层时旧会话会完整存入侧栏历史并可恢复，
    # 默认文案"这将清除您当前的聊天记录"与实际行为不符（会吓退用户）。
    # config 是 pydantic 模型不允许实例属性赋值，因此在类上包装方法。
    _orig_load_translation = type(config).load_translation

    def _patched_load_translation(self, language):
        t = _orig_load_translation(self, language)
        try:
            dialog = (((t.get("navigation") or {}).get("newChat") or {}).get("dialog") or {})
            if dialog.get("description"):
                dialog["description"] = (
                    "当前会话会自动保存到左侧历史，可随时点击恢复。开始新会话吗？"
                )
                if str(language).startswith("en"):
                    dialog["description"] = (
                        "The current thread is saved to the sidebar history and can be "
                        "restored anytime. Start a new thread?"
                    )
            # 回合折叠行的前缀净化：Chainlit 默认在折叠头加"已使用/使用中"（chat.
            # messages.status.used/using），如"已使用 运行 xxx"。清空前缀后折叠头
            # 直接显示动词化标题（"运行 xxx · 0.6s"/"思考了 3.2 秒"/回合摘要）。
            status = ((t.get("chat") or {}).get("messages") or {}).get("status")
            if isinstance(status, dict):
                status["used"] = ""
                status["using"] = ""
        except Exception:
            pass
        return t

    type(config).load_translation = _patched_load_translation

    src_dir = Path(__file__).resolve().parent / "chainlit_public"
    if not src_dir.is_dir():
        return
    public_dir = Path(workspace_root) / "public"
    try:
        public_dir.mkdir(parents=True, exist_ok=True)
        for name in ("logo_dark.svg", "logo_light.svg", "favicon.svg"):
            src = src_dir / name
            if src.is_file():
                shutil.copyfile(src, public_dir / name)
        avatar_src = src_dir / "avatars" / "floodmind.svg"
        avatar_dir = public_dir / "avatars"
        if avatar_src.is_file():
            avatar_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(avatar_src, avatar_dir / "floodmind.svg")
        # UI 增强：显式"新建会话"按钮（custom_css/custom_js 走 /public/ 路由）
        for name in ("floodmind-ui.js", "floodmind-ui.css"):
            src = src_dir / name
            if src.is_file():
                shutil.copyfile(src, public_dir / name)
        config.ui.custom_js = "/public/floodmind-ui.js"
        config.ui.custom_css = "/public/floodmind-ui.css"
    except OSError as exc:
        logger.warning("FloodMind 品牌素材落盘失败（忽略）: %s", exc)


def install(base_dir: Path, workspace_root: Optional[Path] = None) -> None:
    """在 chainlit_app 模块导入期调用：注册数据层、建表、补丁鉴权与文件路由。"""
    try:
        import chainlit as cl
        from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
    except Exception as exc:  # pragma: no cover - 环境缺依赖时降级为无历史
        logger.warning("Chainlit 历史持久化不可用（%s）", exc)
        return

    base_dir.mkdir(parents=True, exist_ok=True)
    db_path = base_dir / "threads.db"
    files_dir = base_dir / "files"
    try:
        _ensure_schema(db_path)
    except Exception as exc:
        logger.warning("Chainlit 历史库建表失败（%s），继续以内存模式运行", exc)
        return

    _ensure_jwt_secret(base_dir)

    layer = SQLAlchemyDataLayer(
        conninfo=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        storage_provider=LocalStorageClient(files_dir),
    )

    @cl.data_layer
    def _data_layer():
        return layer

    try:
        _register_file_route(files_dir)
    except Exception as exc:
        logger.warning("Chainlit 产物文件路由注册失败（%s）", exc)

    _patch_local_auth()

    if workspace_root is not None:
        _apply_branding(workspace_root)
        logger.info("FloodMind 品牌素材已应用（logo/favicon/头像/名称）")


def session_id_for_thread(thread_id: str) -> str:
    """thread_id（持久、跨重启稳定）→ FloodMind session id（确定性映射）。"""
    return "cl-" + (thread_id or "").replace("-", "")[:12]


def thread_metadata(metadata: Any) -> dict:
    """ThreadDict.metadata 可能是 JSON 字符串（SQLite 读回），统一成 dict。"""
    if isinstance(metadata, str):
        try:
            return json.loads(metadata)
        except Exception:
            return {}
    return dict(metadata or {})
