"""
输出脱敏 & SSE payload 过滤

统一出口：所有 emit() 出的 dict、snapshot 序列化、事件回放都经此处理，
避免逐点补 sanitize 漏掉新增事件类型。
"""

import json
import re
from typing import Any, Dict, Optional

# ── SSE 展示字段白名单 ──────────────────────────────────
# 仅对这些字段的字符串值做 sanitize_output；结构字段（step_key/call_id/status/type 等）
# 绝不触碰——否则会破坏前端用 step_key 关联步骤、用 call_id 配对事件的能力。
_SSE_SANITIZE_FIELDS = frozenset({
    'content', 'detail', 'outcome', 'title', 'label',
    'summary', 'task', 'reasoning', 'message', 'skill_name', 'stage_label', 'reason',
})

# tool_input 单列：工具参数结构不定（file_path/command/target 等任意键），
# 其任意字符串值都可能含路径，故对所有 str 值整体脱敏，不走按字段名的白名单
_SSE_FULL_SANITIZE_KEYS = frozenset({'tool_input'})

# _sanitize_deep 中不应脱敏的结构字段（标识符 / 索引 / 枚举）——
# 前后端关联依赖这些键，原样保留；其余字符串值全量脱敏
_SANITIZE_DEEP_SKIP_KEYS = frozenset({
    'session_id', 'id', 'message_id', 'part_id', 'cursor',
    'tool_call_id', 'call_id', 'checkpoint_id', 'event_index', 'event_type',
    'task_id', 'parent_checkpoint_id', 'run_id',
})


def _path_to_basename(match: re.Match) -> str:
    """绝对路径脱敏：仅保留 basename，避免泄露服务端目录结构。"""
    full = match.group(0)
    parts = re.split(r'[\\/]', full)
    return parts[-1] if parts and parts[-1] else ''


def sanitize_output(text: str) -> str:
    """过滤输出中的内部路径和敏感信息（公网生产脱敏）。

    三类处理：
    1. 绝对路径 → basename（保留文件名，脱敏目录）
    2. 内部标识符（session/sub-session/ckpt/run id）→ 占位符
    3. 其他敏感模式（技能说明头、data/sessions 相对路径等）→ 移除
    """
    if not text:
        return text

    # 1) 绝对路径 → basename
    result = re.sub(r'[A-Za-z]:\\[^\s\'"]+', _path_to_basename, text)
    result = re.sub(r'/(?:app|home|Users|opt|var|tmp|root)/[^\s\'"]+', _path_to_basename, result)

    # 2) 内部标识符 → 占位符（sub-session 必须在 session 之前替换，避免部分匹配）
    result = re.sub(r'sub-session-[0-9a-zA-Z-]+', '<subagent>', result)
    result = re.sub(r'ckpt-[a-f0-9]{8,}', '<checkpoint>', result)
    result = re.sub(r'run-[0-9]{10,}', '<run>', result)
    result = re.sub(r'session-[0-9]+-[a-z0-9]+', '<session>', result, flags=re.IGNORECASE)

    # 3) 其他敏感模式 → 移除
    patterns_to_remove = [
        r"Invoking:\s*`[^`]+`",
        r'=== 技能【[^】]+】完整说明 ===',
        r'[\\/]?data[\\/]sessions[^\s\n]*',
        r'[\\/]?skills[\\/][^\s\n]+',
        r'\[会话环境信息\][\s\S]*?(?=\n\n|\Z)',
        r'\[已上传的文件\][\s\S]*?(?=\n\n|\Z)',
        r'已成功生成[^，。！？\n]*[，：]\s*文件保存于[^\n]*',
    ]
    for pattern in patterns_to_remove:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)

    result = re.sub(r'\n\s*\n\s*\n', '\n\n', result)
    return result.strip()


def _sanitize_tool_input(val: Any) -> Any:
    """递归对工具参数的所有字符串值脱敏（参数键名不可枚举，统一处理）。"""
    if isinstance(val, str):
        return sanitize_output(val)
    if isinstance(val, dict):
        return {k: _sanitize_tool_input(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_sanitize_tool_input(i) for i in val]
    return val


def sanitize_payload(obj: Any) -> Any:
    """递归遍历 SSE 事件 payload，对白名单展示字段做脱敏。

    统一出口过滤：所有 emit() 出去的 dict、snapshot 序列化、事件回放都经此处理，
    避免逐点补 sanitize 漏掉新增事件类型，也覆盖 workflow steps、delegation 等嵌套结构。
    对已 sanitize 过的文本幂等（再跑无害）。
    """
    if isinstance(obj, dict):
        return {
            k: (_sanitize_tool_input(v) if k in _SSE_FULL_SANITIZE_KEYS
                else (sanitize_output(v) if k in _SSE_SANITIZE_FIELDS and isinstance(v, str)
                      else sanitize_payload(v)))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [sanitize_payload(i) for i in obj]
    return obj


def sanitize_event_row(row: dict) -> dict:
    """对持久化事件行的 event_data 做脱敏（断线重连/事件回放出口统一过滤）。

    持久化层保留原始数据供内部 trace；仅在 API 出口脱敏，避免泄露绝对路径/内部 id。
    """
    if not isinstance(row, dict):
        return row
    out = dict(row)
    raw = out.get('event_data')
    if isinstance(raw, str):
        try:
            out['event_data'] = json.dumps(sanitize_payload(json.loads(raw)), ensure_ascii=False)
        except Exception:
            pass
    return out


def sanitize_deep(obj: Any) -> Any:
    """递归对所有字符串值做脱敏（不限字段名），但跳过结构标识符字段。

    用于消息历史等"纯展示"出口——所有非标识符 str 都过 sanitize_output（无路径文本幂等无害）；
    session_id/id/cursor/call_id 等标识符原样保留（前端关联依赖，且部分形如 session-N-xxx
    会误中 sanitize_output 的 id 正则，必须跳过）。
    """
    if isinstance(obj, str):
        return sanitize_output(obj)
    if isinstance(obj, dict):
        return {
            k: (v if k in _SANITIZE_DEEP_SKIP_KEYS else sanitize_deep(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [sanitize_deep(i) for i in obj]
    return obj


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


def sanitize_artifact_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """剥离 artifact 事件中的内部路径字段。"""
    return {k: v for k, v in (event or {}).items() if k != 'filepath'}


def server_error_json(exc: Exception, status: int = 500):
    """对外错误响应：异常消息经脱敏（剥离绝对路径 / 内部 id），保留可读语义。"""
    from flask import jsonify
    return jsonify({'error': sanitize_output(str(exc)) or '服务器内部错误'}), status
