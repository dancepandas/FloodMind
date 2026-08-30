"""FloodMind Gateway — FastAPI 应用工厂。"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from floodmind.gateway.state import GatewayState

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


# ── 请求/响应模型 ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    message: str


class PermissionRespondRequest(BaseModel):
    session_id: str
    ask_id: str
    approved: bool


class AbortRequest(BaseModel):
    session_id: str


# ── 应用工厂 ──────────────────────────────────────────────────

def create_app(
    workspace_root: str | Path = ".",
    auth_token: str = "",
    allowed_origins: Optional[list] = None,
    auth_enabled: bool = True,
) -> FastAPI:
    state = GatewayState(workspace_root=Path(workspace_root), auth_token=auth_token)

    app = FastAPI(title="FloodMind Gateway", version="1.0.0", docs_url="/api/docs")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_token(authorization: str = Header(default="")) -> None:
        if not auth_enabled:
            return  # --no-auth 本地模式：显式关闭鉴权
        if not state.auth_token:
            # 开着鉴权却没 token 意味着服务裸奔——显式拒绝而不是静默放行（P3-3）
            raise HTTPException(status_code=503, detail="Gateway 未配置鉴权 token，拒绝服务")
        if not secrets.compare_digest(authorization, f"Bearer {state.auth_token}"):
            raise HTTPException(status_code=401, detail="无效的鉴权 token")

    # ── 基础 ──────────────────────────────────────────────────

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "workspace": str(state.workspace_root),
            "auth_required": auth_enabled,
        }

    # ── 会话管理 ──────────────────────────────────────────────

    @app.get("/api/sessions", dependencies=[Depends(require_token)])
    def list_sessions() -> Dict[str, Any]:
        return {"status": "ok", "sessions": state.sessions.list_sessions()}

    @app.post("/api/sessions", dependencies=[Depends(require_token)])
    def create_session(body: Dict[str, str]) -> Dict[str, Any]:
        session_id = str(body.get("session_id") or "").strip()
        try:
            _, _agent = state.sessions.get_or_create_session(
                session_id, agent_factory=state.agent_factory
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"非法 session_id: {exc}")
        return {"status": "ok", "session_id": session_id}

    @app.delete("/api/sessions/{session_id}", dependencies=[Depends(require_token)])
    def delete_session(session_id: str) -> Dict[str, Any]:
        # 先中止该会话在跑的 run，避免 rmtree 与运行中写入竞争（P2-7）
        state.trigger_abort(session_id)
        state.sessions.delete_session(session_id)
        return {"status": "ok"}

    @app.get("/api/sessions/{session_id}/messages", dependencies=[Depends(require_token)])
    def session_messages(session_id: str) -> Dict[str, Any]:
        """会话历史：从 v2 canonical journal 投影（SessionManager 的旧布局不含 v2 journal）。"""
        from floodmind.agent.runtime.services.history_projection import project_conversation

        runtime_dir = state.workspace_root / ".floodmind"
        messages: list = []
        session_meta = runtime_dir / "sessions" / session_id / "session.json"
        if session_meta.exists():
            try:
                conversation_id = str(
                    json.loads(session_meta.read_text(encoding="utf-8")).get("conversation_id") or ""
                )
            except Exception:
                conversation_id = ""
            if conversation_id:
                turns = project_conversation(runtime_dir, conversation_id)
                pending_assistant: list = []

                def _flush():
                    nonlocal pending_assistant
                    if pending_assistant:
                        text = "\n\n".join(pending_assistant).strip()
                        if text:
                            messages.append({"role": "assistant", "content": text})
                        pending_assistant = []

                for turn in turns:
                    role = str(turn.get("role", ""))
                    content = str(turn.get("content", "") or "")
                    if role == "user":
                        _flush()
                        if content:
                            messages.append({"role": "user", "content": content})
                    elif role == "assistant":
                        pending_assistant.append(content)
                    # tool turns 由 assistant 轮聚合，不单独输出
                _flush()
        return {"status": "ok", "session_id": session_id, "messages": messages}

    # ── 对话（SSE） ───────────────────────────────────────────

    @app.post("/api/chat", dependencies=[Depends(require_token)])
    def chat(body: ChatRequest) -> StreamingResponse:
        try:
            _, agent = state.sessions.get_or_create_session(
                body.session_id, agent_factory=state.agent_factory
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"非法 session_id: {exc}")
        # 首条用户消息自动作为会话标题（仅未命名会话；"Untitled" 视为未命名）
        title_now = ""
        try:
            title_now = state.sessions.get_session_title(body.session_id) or ""
        except Exception:
            pass
        if title_now.strip() in ("", "Untitled", "新会话"):
            title = " ".join(body.message.split())[:40]
            if title:
                try:
                    state.sessions.update_session_title(body.session_id, title)
                except Exception:
                    pass
        # 同会话新 run 到来时中止上一个仍在跑的 run（P2-7：后到请求覆盖前者）
        state.trigger_abort(body.session_id)
        abort_event = state.register_abort(body.session_id)
        message = body.message

        def gen():
            # P2-6：finally 中禁止 yield——客户端断开触发 GeneratorExit 后再 yield
            # 会抛 RuntimeError("generator ignored GeneratorExit")。错误/完成事件
            # 只在正常流程中产出；断开时 finally 仅做簿记并重新抛出。
            try:
                for event in agent.stream(message, abort_check=abort_event.is_set):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                yield 'data: {"type": "__done__"}\n\n'
            except GeneratorExit:
                raise
            except Exception as exc:
                logger.exception("chat 流异常")
                try:
                    yield "data: " + json.dumps(
                        {"type": "error", "content": str(exc)}, ensure_ascii=False
                    ) + "\n\n"
                    yield 'data: {"type": "__done__"}\n\n'
                except GeneratorExit:
                    raise
            finally:
                state.release_abort(body.session_id, abort_event)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chat/abort", dependencies=[Depends(require_token)])
    def abort(body: AbortRequest) -> Dict[str, Any]:
        ok = state.trigger_abort(body.session_id)
        return {"status": "ok" if ok else "no_active_run", "aborted": ok}

    # ── 权限应答（非阻塞 ASK 闭环） ────────────────────────────

    @app.post("/api/permission/respond", dependencies=[Depends(require_token)])
    def permission_respond(body: PermissionRespondRequest) -> Dict[str, Any]:
        from floodmind.agent.runtime.contracts.permissions import PermissionAskResponse
        from floodmind.agent.runtime.services.ask_service import get_ask_service

        svc = get_ask_service()
        delivered = svc.respond(
            PermissionAskResponse(
                session_id=body.session_id,
                ask_id=body.ask_id,
                approved=body.approved,
            )
        )
        if not delivered:
            raise HTTPException(status_code=404, detail="审批请求不存在或已关闭")
        return {"status": "ok", "delivered": True}

    # ── 产物文件服务 ──────────────────────────────────────────

    @app.get("/api/file", dependencies=[Depends(require_token)])
    def get_file(session_id: str, path: str):
        """按会话提供产物文件下载/预览（image_generated/file_generated 事件的
        download_url 在本地模式下是文件系统路径，需要网关转换为 HTTP 服务）。

        安全：resolve 后必须位于工作区根内（containment 断言，防路径穿越）。
        """
        from fastapi.responses import FileResponse

        try:
            resolved = Path(path).resolve()
            resolved.relative_to(state.workspace_root)
        except (ValueError, OSError):
            raise HTTPException(status_code=400, detail="非法文件路径")
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        media_type = None
        suffix = resolved.suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            media_type = f"image/{'jpeg' if suffix in ('.jpg', '.jpeg') else suffix.lstrip('.')}"
        return FileResponse(resolved, filename=resolved.name, media_type=media_type)

    # ── 静态 Web UI（/ 与 /index.html） ────────────────────────

    if _STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
    else:  # pragma: no cover
        @app.get("/")
        def index() -> Dict[str, Any]:
            return {"status": "ok", "hint": "Web UI 未打包（floodmind/gateway/static 缺失）"}

    return app
