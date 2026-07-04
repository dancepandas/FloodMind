"""模型 & 配置路由"""
import logging
from datetime import datetime

from flask import Blueprint, request, jsonify

from floodmind.config.model_presets import get_preset, get_default_model_key, get_models_list
from floodmind.server.agent_factory import get_or_create_agent
from floodmind.server.sanitize import sanitize_output
from floodmind.server.session_state import ensure_session_state

logger = logging.getLogger(__name__)

models_bp = Blueprint('models', __name__)


def _sm():
    from flask import current_app
    return current_app.config['SESSION_MANAGER']


def _require_session_id(raw):
    from floodmind.memory.session_manager import validate_session_id
    return validate_session_id(raw or "default")


# ── Agent 初始化 ──────────────────────────────────────

@models_bp.route('/api/init', methods=['POST'])
def init_agent():
    try:
        data = request.get_json()
        session_id = _require_session_id(data.get('session_id', 'default'))
        enable_search = data.get('enable_search', True)
        enable_rag = data.get('enable_rag', True)
        enable_reasoning = data.get('enable_reasoning', True)
        model_key = data.get('model_key', '').strip()

        state = ensure_session_state(session_id)
        state['enable_search'] = enable_search
        state['enable_rag'] = enable_rag
        state['enable_reasoning'] = enable_reasoning
        if model_key and get_preset(model_key):
            state['model_key'] = model_key

        sm = _sm()
        if hasattr(sm, '_agents') and session_id in sm._agents:
            del sm._agents[session_id]

        agent = get_or_create_agent(session_id, sm)

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
            'timestamp': datetime.now().isoformat(),
        })
    except Exception as e:
        logger.error("初始化失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 模型列表 ──────────────────────────────────────────

@models_bp.route('/api/models', methods=['GET'])
def list_models():
    try:
        from floodmind.config.model_presets import reload_presets
        reload_presets()  # 刷新配置缓存，确保 settings.json 变更生效
        models = get_models_list()
        default_key = get_default_model_key()
        return jsonify({'status': 'success', 'default_model_key': default_key, 'models': models})
    except Exception as e:
        logger.error("获取模型列表失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 配置信息 ──────────────────────────────────────────

@models_bp.route('/api/config', methods=['GET'])
def get_config():
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
        ],
    })


# ── 会话配置更新 ──────────────────────────────────────

@models_bp.route('/api/session/config', methods=['POST'])
def update_session_config():
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
            status_messages.append(f"模型已切换为 {preset['label']}")
            if not preset.get('supports_reasoning') and state.get('enable_reasoning'):
                state['enable_reasoning'] = False
                status_messages.append("深度思考模式已关闭（当前模型不支持）")

        if enable_search is not None and enable_search != state.get('enable_search'):
            state['enable_search'] = enable_search
            config_changed = True
            status_messages.append(f"联网搜索功能已{'启用' if enable_search else '关闭'}")

        if enable_rag is not None and enable_rag != state.get('enable_rag'):
            state['enable_rag'] = enable_rag
            config_changed = True
            status_messages.append(f"知识库检索(RAG)功能已{'启用' if enable_rag else '关闭'}")

        if enable_reasoning is not None and enable_reasoning != state.get('enable_reasoning'):
            effective_model_key = state.get('model_key', get_default_model_key())
            preset = get_preset(effective_model_key)
            if enable_reasoning and preset and not preset.get('supports_reasoning'):
                return jsonify({'status': 'error',
                                'message': f'当前模型 {preset["label"]} 不支持深度思考'}), 400
            state['enable_reasoning'] = enable_reasoning
            config_changed = True
            status_messages.append(f"深度思考模式已{'启用' if enable_reasoning else '关闭'}")

        if config_changed:
            sm = _sm()
            agent = sm.get_agent(session_id)
            if agent and hasattr(agent, 'memory'):
                system_notice = (
                    f"[系统通知] 功能状态更新：{', '.join(status_messages)}。"
                    f"请在后续对话中使用更新后的功能状态。"
                )
                if hasattr(agent.memory, 'add_user_message'):
                    agent.memory.add_user_message(system_notice)
                    if hasattr(agent.memory, 'add_ai_message'):
                        agent.memory.add_ai_message("收到，已更新功能状态配置。")
            if hasattr(sm, '_agents') and session_id in sm._agents:
                del sm._agents[session_id]

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
            },
        })
    except Exception as e:
        logger.error("更新会话配置失败: %s", e)
        return jsonify({'status': 'error', 'message': sanitize_output(str(e)) or '服务器内部错误'}), 500


# ── 清空记忆（复用 /api/session/config，前端传 clear_memory=true） ──
# 注：原 web_server.py 中 clear_memory 和 update_session_config 共享同一路由，
# Flask 后者覆盖前者 → clear_memory 为死代码。现统一到 update_session_config 中。
