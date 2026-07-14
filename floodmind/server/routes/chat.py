"""聊天 SSE 流式路由

核心路由: POST /api/chat → NDJSON SSE stream
断线重连: GET /api/stream/resume → 事件回放 + 继续流
"""
import contextvars
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Blueprint, request, jsonify, Response

from floodmind.server.agent_factory import get_or_create_agent
from floodmind.server.config import SSE_MAX_LIFETIME_SEC
from floodmind.server.sanitize import (
    sanitize_output, sanitize_payload, sanitize_tool_output,
    passthrough_workflow_content, sanitize_artifact_event, server_error_json,
)
from floodmind.server.session_state import (
    ensure_session_state, init_stream_snapshot, touch_stream_snapshot,
    finish_stream_snapshot, accumulate_token_usage,
    session_abort_flags, session_abort_flags_lock,
    session_streaming_flags, session_streaming_lock,
)
from floodmind.server.file_utils import (
    get_session_files_map, get_session_output_dir,
    extract_validated_artifact_paths, build_artifact_event,
    resolve_artifact_references, save_session_artifact_events,
)

logger = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)


def _sm():
    from flask import current_app
    return current_app.config['SESSION_MANAGER']


def _require_session_id(raw):
    from floodmind.memory.session_manager import validate_session_id
    return validate_session_id(raw or "default")


def _generate_session_title(message: str, model_key: str = "") -> str:
    """调用 LLM 生成简短会话标题。"""
    import re
    from floodmind.agent.native.model_client import ModelClient
    from floodmind.config.model_presets import get_preset, resolve_api_key, resolve_base_url

    prompt = (
        "请根据用户问题拟一个简短的中文会话标题，用于左侧历史会话列表展示。"
        "要求：10到18个字，突出任务目标，不要带引号，不要解释，不要句号。\n\n"
        f"用户问题：{message}"
    )
    if model_key:
        preset = get_preset(model_key)
        if preset:
            llm = ModelClient(
                api_key=resolve_api_key(preset),
                model_name=preset["model_name"],
                base_url=resolve_base_url(preset),
                temperature=0.2, max_tokens=60, enable_thinking=False,
            )
        else:
            llm = ModelClient.from_settings(temperature=0.2, max_tokens=60, enable_thinking=False)
    else:
        llm = ModelClient.from_settings(temperature=0.2, max_tokens=60, enable_thinking=False)

    result = llm.invoke(prompt)
    if result is None or not hasattr(result, 'content'):
        return ""
    raw = (result.content or '').strip()
    title = raw.splitlines()[0].strip().strip('"""')
    title = re.sub(r"^[#\-*\d.\s]+", "", title).strip()
    return title[:24] if title else ''


def schedule_session_title_generation(session_id: str, message: str, model_key: str = "") -> None:
    """后台异步生成会话标题。"""
    def _worker():
        try:
            title = _generate_session_title(message, model_key=model_key)
            if title:
                _sm().update_session_title(session_id, title)
                logger.info("会话标题已更新: %s -> %s", session_id, title)
        except Exception as e:
            logger.warning("生成会话标题失败: %s", e)

    threading.Thread(target=_worker, daemon=True, name=f"session-title-{session_id[:8]}").start()


def stream_json_line(payload: Dict[str, Any]) -> str:
    """将流事件编码为 NDJSON 行。"""
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _buffered_yield(buf: list, payload: dict,
                    resume_event: Optional[threading.Event] = None,
                    buffer_lock: Optional[threading.Lock] = None) -> str:
    line = stream_json_line(payload)
    if buffer_lock:
        with buffer_lock:
            buf.append(line)
    else:
        buf.append(line)
    if resume_event:
        resume_event.set()
    return line


# ── POST /api/chat ────────────────────────────────────

@chat_bp.route('/api/chat', methods=['POST'])
def chat():
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

        logger.info("[Backend Debug] 接收到请求, enable_reasoning: %s", enable_reasoning)

        if not message:
            return jsonify({'error': '消息不能为空'}), 400

        with session_streaming_lock:
            is_queued = session_streaming_flags.get(session_id, False)
            if not is_queued:
                session_streaming_flags[session_id] = True

        sm = _sm()
        sm.get_or_create_session(session_id, agent_factory=None)
        sm.increment_message_count(session_id)
        session_info = sm.get_session_info(session_id)
        if session_info and session_info.message_count == 1 and not session_info.title:
            schedule_session_title_generation(session_id, message, model_key=state.get('model_key', ''))

        if not is_queued:
            with session_abort_flags_lock:
                session_abort_flags[session_id] = False

        if enable_reasoning != state.get('enable_reasoning', True):
            state['enable_reasoning'] = enable_reasoning
            if hasattr(sm, '_agents') and session_id in sm._agents:
                del sm._agents[session_id]
                logger.info("推理模式变更，重新创建Agent: %s", session_id)

        agent = get_or_create_agent(session_id, sm)

        # Wire SyncEvent persistence
        from floodmind.memory.session_store import get_last_event_index
        _event_index_tracker = {"idx": get_last_event_index(session_id)}

        def _persist_event(event: dict) -> None:
            etype = event.get("type", "unknown")
            if etype in ("heartbeat", "answer_delta", "thought_delta"):
                return
            try:
                _event_index_tracker["idx"] += 1
                from floodmind.memory.session_store import append_sync_event
                append_sync_event(session_id, _event_index_tracker["idx"], etype, event)
            except Exception:
                logger.warning("sync_event persist failed (session=%s idx=%s)",
                               session_id, _event_index_tracker["idx"], exc_info=True)

        if hasattr(agent, '_event_bus'):
            agent._event_bus.set_persist_callback(_persist_event)

        session_file_map = get_session_files_map(session_id, sm)
        file_context = ""
        if uploaded_files and session_file_map:
            available_files = []
            for file_id in uploaded_files:
                if file_id in session_file_map:
                    available_files.append(session_file_map[file_id])
            if available_files:
                file_context = "\n[已上传的文件]\n"
                for f in available_files:
                    file_context += f"- 文件名: {f['name']}, 路径: {f['path']}\n"
                file_context += "用户提到'已上传的文件'或'上传的文件'时，请使用上述路径。\n"

        enhanced_message = file_context + "\n\n" + message if file_context else message

        # 排队路径
        if is_queued:
            try:
                if hasattr(agent, 'memory') and agent.memory is not None and hasattr(agent.memory, 'add_user_message'):
                    agent.memory.add_user_message(enhanced_message)
                    logger.info("[chat] 排队消息（运行中）: session=%s, msg=%s", session_id, message[:50])
                else:
                    logger.warning("[chat] 排队失败：agent.memory 不可用 session=%s", session_id)
            except Exception as e:
                logger.warning("[chat] 排队消息写入 memory 失败: %s", e)
            return jsonify({'status': 'queued', 'message': '消息已排队，将在当前任务完成后处理'}), 202

        # 构建图片附件列表
        from floodmind.agent.native.types import Attachment
        attachments = []
        if uploaded_files and session_file_map:
            for file_id in uploaded_files:
                if file_id in session_file_map:
                    info = session_file_map[file_id]
                    if info.get('kind') == 'image':
                        attachments.append(Attachment(
                            file_id=info['id'], name=info['name'], path=info['path'],
                            kind='image', mime_type=info.get('mime_type', 'image/png'),
                            size=info['size'],
                        ))
            if attachments:
                from floodmind.config.model_presets import get_preset, get_models_list, get_default_model_key
                current_model_key = state.get('model_key', get_default_model_key())
                preset = get_preset(current_model_key)
                if preset and not preset.get('supports_vision'):
                    with session_streaming_lock:
                        session_streaming_flags.pop(session_id, None)
                    vision_model_names = [
                        m['label'] for m in get_models_list() if m.get('supports_vision')
                    ]
                    return jsonify({
                        'error': f'当前模型不支持图像理解，请切换至支持视觉的模型（{" / ".join(vision_model_names)}）后再上传图片'
                    }), 400
                logger.info("本轮请求携带 %d 张图片附件", len(attachments))

        request_started_at = time.time()

        def _run_agent_pump(snapshot, event_buffer, resume_event,
                            approved_artifact_paths, streamed_text_parts, attachments):
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
                if isinstance(payload, dict):
                    payload = sanitize_payload(payload)
                return _buffered_yield(event_buffer, payload, resume_event, buffer_lock)

            try:
                for chunk in agent.stream(
                    enhanced_message, enable_reasoning=enable_reasoning,
                    user_message=message, attachments=attachments,
                    abort_check=lambda: session_abort_flags.get(session_id, False),
                ):
                    with session_abort_flags_lock:
                        is_aborted = session_abort_flags.get(session_id, False)
                    if is_aborted:
                        finish_stream_snapshot(session_id)
                        emit({'type': 'stream_paused', 'content': '会话已被用户暂停'})
                        emit({'type': 'stream_end'})
                        return

                    if not isinstance(chunk, dict):
                        chunk = {"type": "content", "content": str(chunk)}

                    # ── workflow ──
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
                                    normalized_status = (
                                        raw_status if raw_status in ('completed', 'running', 'pending', 'error')
                                        else ('completed' if raw_status == 'done' else raw_status)
                                    )
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
                                logger.warning("[workflow] unknown step_key='%s' not in plan steps, ignoring", step_key)
                            workflow_snapshot['steps'] = updated_steps
                        snapshot['workflow'] = workflow_snapshot
                        touch_stream_snapshot(session_id)
                        emit(chunk)
                        continue

                    # ── reasoning ──
                    if chunk.get("type") in {"reasoning", "thought_delta"}:
                        if enable_reasoning or is_workflow_stream:
                            snapshot['raw_reasoning'] += chunk.get('content', '')
                            snapshot['reasoning'] = snapshot['raw_reasoning']
                            touch_stream_snapshot(session_id)
                            event = {'type': 'thought_delta', 'content': chunk.get('content', '')}
                            step_key = chunk.get('step_key', '')
                            if step_key:
                                event['step_key'] = step_key
                            emit(event)
                        continue

                    # ── error / token error ──
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

                    # ── permission ──
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

                    # ── final_text ──
                    if chunk.get("type") == "final_text":
                        final_answer_text = sanitize_output(chunk.get('content', '') or '')
                        if final_answer_text:
                            snapshot['content'] = final_answer_text
                            touch_stream_snapshot(session_id)
                        continue

                    # ── tool status / action_start ──
                    if chunk.get("type") in {"tool_status", "action_start"}:
                        call_id = chunk.get('call_id', '')
                        step_key = chunk.get('step_key', '')
                        safe_chunk = {
                            'type': 'action_start',
                            'tool_name': chunk.get('tool_name', ''),
                            'status': chunk.get('status', 'running'),
                        }
                        if step_key:
                            safe_chunk['step_key'] = step_key
                        if call_id:
                            safe_chunk['call_id'] = call_id
                        tool_input = chunk.get('tool_input', '')
                        if tool_input:
                            safe_chunk['tool_input'] = tool_input
                        if tool_input and chunk.get('tool_name', '') in ('SubAgent', 'ParallelSubAgent', 'ParallelTask'):
                            safe_chunk['delegation'] = {
                                'task': '', 'skill_name': '', 'label': 'SubAgent',
                            }
                        if chunk.get('status') == 'error':
                            safe_chunk['content'] = '工具执行失败，智能体正在继续处理。'
                        touch_stream_snapshot(session_id)
                        emit(safe_chunk)
                        continue

                    # ── tool result / action_end ──
                    if chunk.get("type") in {"tool_result", "action_end"}:
                        original_content = chunk.get("content", "")
                        tool_name = chunk.get('tool_name', '')
                        call_id = chunk.get('call_id', '')
                        filtered_content = (
                            passthrough_workflow_content(original_content) if is_workflow_stream
                            else sanitize_tool_output(tool_name, original_content)
                        )
                        if not filtered_content:
                            continue

                        logger.info("action_end 内容: %s",
                                    filtered_content[:200] if len(filtered_content) > 200 else filtered_content)

                        validated_paths = extract_validated_artifact_paths(original_content, session_id=session_id, session_manager=sm)
                        logger.info("[ARTIFACT] extract_validated_artifact_paths result: paths=%s, tool=%s",
                                    validated_paths, tool_name)
                        if validated_paths:
                            for vp in validated_paths:
                                rp = os.path.realpath(vp)
                                import os as _os
                                if rp not in {_os.path.realpath(p) for p in approved_artifact_paths}:
                                    approved_artifact_paths.append(vp)

                        result_event = {'type': 'action_end', 'tool_name': tool_name, 'content': filtered_content}
                        if call_id:
                            result_event['call_id'] = call_id
                        step_key = chunk.get('step_key', '')
                        if step_key:
                            result_event['step_key'] = step_key

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

                    # ── answer_delta (text stream) ──
                    if chunk.get("type") in {"token", "answer_delta"} and chunk.get("content"):
                        raw_content = chunk["content"]
                        safe_content = raw_content if is_workflow_stream else sanitize_output(raw_content)
                        streamed_text_parts.append(safe_content)
                        snapshot['content'] += safe_content
                        touch_stream_snapshot(session_id)
                        event = {'type': 'answer_delta', 'content': safe_content}
                        step_key = chunk.get('step_key', '')
                        if step_key:
                            event['step_key'] = step_key
                        emit(event)
                        continue

                    # ── token_usage ──
                    if chunk.get("type") == "token_usage":
                        touch_stream_snapshot(session_id)
                        accumulate_token_usage(session_id, chunk)
                        emit(chunk)
                        continue

                    # ── 新增事件类型 ──
                    if chunk.get("type") == "llm_step_start":
                        touch_stream_snapshot(session_id)
                        emit(chunk)
                        continue
                    if chunk.get("type") == "llm_step_end":
                        touch_stream_snapshot(session_id)
                        emit(chunk)
                        continue
                    if chunk.get("type") == "retry_attempt":
                        emit(chunk)
                        continue
                    if chunk.get("type") == "context_compress_start":
                        emit(chunk)
                        continue
                    if chunk.get("type") == "context_compress_done":
                        emit(chunk)
                        continue

                # ── stream complete: finalize artifacts ──
                final_text = final_answer_text.strip() or ''.join(streamed_text_parts)
                approved_artifact_paths[:] = resolve_artifact_references(
                    session_id, approved_artifact_paths, final_text, sm)
                logger.info("[ARTIFACT] approved_artifact_paths after resolve: %s", approved_artifact_paths)

                approved_artifact_events: List[Dict[str, Any]] = []
                emitted_paths: set = set()
                for artifact_path in approved_artifact_paths:
                    artifact_event = build_artifact_event(artifact_path, session_id, emitted_paths, sm)
                    if artifact_event:
                        logger.info("[ARTIFACT] built event: type=%s, filename=%s",
                                    artifact_event.get('type'), artifact_event.get('filename'))
                        approved_artifact_events.append(artifact_event)
                    else:
                        logger.warning("[ARTIFACT] build_artifact_event returned None for path: %s", artifact_path)

                snapshot['artifacts'] = approved_artifact_events
                touch_stream_snapshot(session_id)
                save_session_artifact_events(session_id, approved_artifact_events, sm)

                final_event = {
                    'type': 'final',
                    'content': final_text,
                    'artifacts': [sanitize_artifact_event(e) for e in approved_artifact_events],
                }
                snapshot['content'] = final_text
                touch_stream_snapshot(session_id)
                emit(final_event)

                _pump_stop_heartbeat.set()
                finish_stream_snapshot(session_id)
                emit({'type': 'stream_end'})

                session_info = sm.get_session_info(session_id)
                if session_info and not session_info.title:
                    from floodmind.memory.session_manager import SessionManager as _SM
                    title = _SM._extract_title_from_user_input(message)
                    sm.update_session_title(session_id, title)

            except Exception as e:
                logger.error("流式输出错误: %s", e)
                _pump_stop_heartbeat.set()
                finish_stream_snapshot(session_id)
                emit({'type': 'error', 'content': '处理请求时出错，请查看服务器日志'})
                emit({'type': 'stream_end'})
            finally:
                _pump_stop_heartbeat.set()
                if snapshot.get('is_streaming'):
                    finish_stream_snapshot(session_id)
                    logger.info("_run_agent_pump interrupted, force-finished stream snapshot")
                with session_streaming_lock:
                    session_streaming_flags.pop(session_id, None)

        def generate():
            """Buffer-following reader: yield from event_buffer."""
            snapshot = init_stream_snapshot(session_id, assistant_message_id)
            approved_artifact_paths: list = []
            streamed_text_parts: list = []
            event_buffer = snapshot['event_buffer']
            resume_event = snapshot['resume_event']
            buffer_lock = snapshot.get('buffer_lock', threading.Lock())

            pump_ctx = contextvars.copy_context()
            pump_thread = threading.Thread(
                target=pump_ctx.run,
                args=(_run_agent_pump, snapshot, event_buffer, resume_event,
                      approved_artifact_paths, streamed_text_parts, attachments),
                daemon=True,
                name=f"agent-pump-{session_id[:8]}",
            )
            pump_thread.start()

            replayed = 0
            stream_start = time.time()
            try:
                while True:
                    with buffer_lock:
                        buf_len = len(event_buffer)
                    while replayed < buf_len:
                        yield event_buffer[replayed]
                        replayed += 1
                    if not snapshot.get('is_streaming'):
                        break
                    if time.time() - stream_start > SSE_MAX_LIFETIME_SEC:
                        yield stream_json_line({
                            'type': 'notify', 'level': 'warning',
                            'message': '会话已超过最大时长，连接自动关闭',
                        })
                        yield stream_json_line({'type': 'stream_end'})
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
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )

    except Exception as e:
        logger.error("聊天接口错误: %s", e)
        if session_id:
            with session_streaming_lock:
                session_streaming_flags.pop(session_id, None)
        return server_error_json(e)


# ── GET /api/stream/resume ────────────────────────────

@chat_bp.route('/api/stream/resume', methods=['GET'])
def stream_resume():
    """恢复断开的流式连接。"""
    session_id = _require_session_id(request.args.get('session_id', 'default'))
    after_index = int(request.args.get('after_index', '0'))
    state = ensure_session_state(session_id)
    snapshot = state.get('stream_snapshot')

    def replay_and_continue():
        # 优先从 sync_events 表回放持久化事件
        try:
            from floodmind.memory.session_store import get_sync_events
            persisted = get_sync_events(session_id, after_index=after_index, limit=500)
        except Exception:
            persisted = None
        after_replay = after_index
        if persisted:
            for evt in persisted:
                try:
                    yield stream_json_line(sanitize_payload(json.loads(evt['event_data'])))
                except Exception:
                    logger.warning("[resume] 跳过损坏事件 index=%s", evt.get('event_index'))
                after_replay = evt['event_index']

        # 回退到内存 event_buffer 继续实时流
        event_buffer = snapshot.get('event_buffer', []) if snapshot else []
        buffer_lock = snapshot.get('buffer_lock', threading.Lock()) if snapshot else threading.Lock()
        replayed = max(after_replay, after_index)
        stale_rounds = 0
        max_stale_rounds = 6

        while True:
            with buffer_lock:
                buf_len = len(event_buffer)
            while replayed < buf_len:
                yield event_buffer[replayed]
                replayed += 1
                stale_rounds = 0
            if not snapshot or not snapshot.get('is_streaming'):
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
                    yield stream_json_line({'type': 'stream_end'})
                    break
            else:
                stale_rounds = 0
        with buffer_lock:
            while replayed < len(event_buffer):
                yield event_buffer[replayed]
                replayed += 1

    if not snapshot:
        return jsonify({'status': 'idle', 'message': '没有正在进行的流'}), 200
    return Response(replay_and_continue(), mimetype='application/x-ndjson')
