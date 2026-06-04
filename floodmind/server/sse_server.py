"""
FloodMind SSE Server — 客户端-服务器分离

提供 REST API + SSE 事件流，TUI 通过 HTTP 连接 Agent。
参照 OpenCode 的 /global/event SSE 架构。
"""

import json
import logging
import queue
import re
import threading

from flask import Flask, request, jsonify, Response, stream_with_context

from floodmind.config.settings import settings
from floodmind.agent.native.model_client import ModelClient

logger = logging.getLogger(__name__)

_app = Flask("floodmind-server")
_agents: dict = {}  # session_id → agent
_agents_lock = threading.Lock()
_llm = None
_llm_lock = threading.Lock()


def _get_or_create_agent(session_id: str):
    global _llm
    with _llm_lock:
        if _llm is None:
            _llm = ModelClient.from_settings(
                temperature=settings.model.temperature,
                max_tokens=settings.model.max_tokens,
            )
    with _agents_lock:
        if session_id not in _agents:
            memory = DualMemory(
                session_id=session_id,
                max_short_term=settings.agent.max_history,
                context_window=settings.agent.context_window,
                llm=_llm,
            )
            agent = create_flood_agent(llm_service=_llm, memory=memory, session_id=session_id)
            _agents[session_id] = agent
            if not get_session(session_id):
                create_session(session_id=session_id)
        return _agents[session_id]


@_app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@_app.route("/api/chat", methods=["POST"])
def chat():
    """发送消息 → 返回 SSE 流"""
    data = request.get_json(force=True)
    user_input = data.get("message", "").strip()
    session_id = data.get("session_id", "").strip() or "default"
    if not re.match(r'^[a-zA-Z0-9_\-]+$', session_id):
        return jsonify({"error": "invalid session_id"}), 400
    if not user_input:
        return jsonify({"error": "message required"}), 400

    agent = _get_or_create_agent(session_id)
    # Record user message
    add_message(session_id, "user", parts=[{"type": "text", "text": user_input}])

    assistant_msg_id = add_message(session_id, "assistant", mode="primary")

    def generate():
        answer_text = ""
        reasoning_text = ""
        tool_states = []
        stream_error = None
        try:
            for chunk in agent.stream(user_input):
                t = chunk.get("type", "")
                if t == "answer_delta":
                    answer_text += chunk.get("content", "")
                elif t == "thought_delta":
                    reasoning_text += chunk.get("content", "")
                elif t in ("action_start", "action_end", "error", "heartbeat", "__done__"):
                    pass
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        except Exception as e:
            stream_error = e
            err = {"type": "error", "content": str(e)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        finally:
            if not stream_error:
                yield "data: {\"type\": \"__done__\"}\n\n"
            # Save assistant message parts
            parts = []
            if reasoning_text:
                parts.append({"type": "reasoning", "text": reasoning_text})
            if answer_text:
                parts.append({"type": "text", "text": answer_text})
            complete_message(assistant_msg_id, append_parts=parts)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@_app.route("/api/sessions", methods=["GET"])
def api_list_sessions():
    """列出所有 session。"""
    sessions = list_sessions()
    return jsonify(sessions)


@_app.route("/api/sessions/<session_id>", methods=["GET"])
def api_get_session(session_id: str):
    """获取 session 详情。"""
    s = get_session(session_id)
    if not s:
        return jsonify({"error": "not found"}), 404
    return jsonify(s)


@_app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_delete_session(session_id: str):
    delete_session(session_id)
    with _agents_lock:
        if session_id in _agents:
            _agents[session_id].clear_memory()
            del _agents[session_id]
    return jsonify({"ok": True})


@_app.route("/api/sessions/<session_id>/rename", methods=["POST"])
def api_rename_session(session_id: str):
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    rename_session(session_id, title)
    return jsonify({"ok": True})


@_app.route("/api/sessions/<session_id>/messages", methods=["GET"])
def api_get_messages(session_id: str):
    messages = get_messages(session_id)
    return jsonify(messages)


@_app.route("/api/sessions/<session_id>/fork", methods=["POST"])
def api_fork_session(session_id: str):
    data = request.get_json(silent=True) or {}
    up_to = data.get("message_id")
    new_id = fork_session(session_id, up_to_message_id=up_to)
    return jsonify({"id": new_id})


@_app.route("/api/sessions/<session_id>/revert", methods=["POST"])
def api_revert_session(session_id: str):
    data = request.get_json(force=True)
    message_id = data.get("message_id", "").strip()
    if not message_id:
        return jsonify({"error": "message_id required"}), 400
    revert_session(session_id, message_id)
    return jsonify({"ok": True})


@_app.route("/api/sessions/<session_id>/compact", methods=["POST"])
def api_compact_session(session_id: str):
    global _llm
    agent = _agents.get(session_id)
    if agent is None and _llm is None:
        with _llm_lock:
            if _llm is None:
                _llm = ModelClient.from_settings(
                    temperature=settings.model.temperature,
                    max_tokens=settings.model.max_tokens,
                )
    llm = agent.llm_service if agent else _llm
    summary = compact_session(session_id, llm=llm)
    return jsonify({"ok": True, "summary": summary})


@_app.route("/api/sessions/<session_id>/export", methods=["GET"])
def api_export_session(session_id: str):
    md = export_session_markdown(session_id)
    if not md:
        return jsonify({"error": "session not found"}), 404
    return Response(md, mimetype="text/markdown",
                    headers={"Content-Disposition": f"attachment; filename=session-{session_id[:8]}.md"})


def run_server(host: str = "0.0.0.0", port: int = 13014) -> None:
    """启动 SSE 服务器。"""
    from waitress import serve
    logger.info(f"FloodMind SSE Server 启动: http://{host}:{port}")
    serve(_app, host=host, port=port, threads=8, channel_timeout=300)
