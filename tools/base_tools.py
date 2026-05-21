"""
基础工具模块

提供Agent执行技能所需的核心工具，所有工具统一使用 build_agent_tool 构建，
具备完整的行为元数据（readonly/destructive/concurrency_safe/interrupt_behavior）。

工具分类：
- 只读工具: get_skill, search_artifacts, read_artifact, knowledge_search, search_memory, search_tool_error_memory
- 写入工具: write_text_file, update_project_instructions, add_knowledge, add_memory
- 执行工具: exec_bash, run_script, exec_python_file
- 网络工具: web_search
"""

import os
import sys
import json
import logging
import re
import shutil
import hashlib
import subprocess
import threading
import contextvars
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Union

from pydantic import BaseModel, Field

from tools.agent_tool import (
    ToolRegistry,
    build_agent_tool,
    UpdateProjectInstructionsInput,
    get_agents_md_path,
    make_readonly_permission_fn,
    make_write_permission_fn,
    make_exec_permission_fn,
    make_skill_script_permission_fn,
    make_ask_permission_fn,
    make_read_path_permission_fn,
    resolve_tool_path,
)
from agent.runtime.contracts.permissions import ToolPermissionPolicy

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RETRY_GUARD_LOCK = threading.Lock()
_RETRY_GUARD_STATES: Dict[str, dict] = {}


def _get_retry_guard_state() -> dict:
    session_id = _SESSION_CONTEXT.get("session_id", "") or "default"
    with _RETRY_GUARD_LOCK:
        if session_id not in _RETRY_GUARD_STATES:
            _RETRY_GUARD_STATES[session_id] = {
                "signature": None,
                "consecutive_failures": 0,
                "last_error_output": None,
            }
        return _RETRY_GUARD_STATES[session_id]
_RETRY_GUARD_PROMPT = (
    "\n\n[重试保护提示]\n"
    "你已经连续三次对同一个工具或 skill 使用相同或等价参数且都失败了。"
    "不要继续原样重试。请先分析错误原因，检查参数、前置条件、skill 名称、脚本名称、环境依赖，"
    "并判断是否应该改用其他工具或先向用户补充确认信息，然后再决定下一步调用。"
)


def _build_exec_env() -> Dict[str, str]:
    """为 exec_bash 构建更稳定的运行环境。"""
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    existing_pythonpath = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = str(_PROJECT_ROOT) if not existing_pythonpath else f"{_PROJECT_ROOT}{os.pathsep}{existing_pythonpath}"
    env.setdefault('MPLBACKEND', 'Agg')
    env.setdefault('MPLCONFIGDIR', str(_PROJECT_ROOT / 'data' / 'matplotlib'))
    output_dir = _SESSION_CONTEXT.get("output_dir")
    session_id = _SESSION_CONTEXT.get("session_id")
    if session_id:
        env['SESSION_ID'] = str(session_id)
    if output_dir:
        env['SESSION_OUTPUT_DIR'] = str(output_dir)
    return env


def _detect_shell_command() -> tuple[list[str], str]:
    """自动选择当前环境可用的 shell。"""
    if shutil.which('powershell.exe'):
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"], "powershell"
    if shutil.which('pwsh'):
        return ["pwsh", "-NoProfile", "-NonInteractive", "-Command"], "pwsh"
    if shutil.which('bash'):
        return ["bash", "-lc"], "bash"
    if shutil.which('sh'):
        return ["sh", "-lc"], "sh"
    raise FileNotFoundError("未找到可用 shell（powershell.exe / pwsh / bash / sh）")


def _parse_json_if_needed(value: str) -> dict:
    """如果值是 JSON 字符串，解析它"""
    if not value:
        return {}
    value = str(value).strip()
    if value.startswith('{') and value.endswith('}'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return {}


def _normalize_args(args: Union[str, List, None]) -> List[str]:
    """将 args 参数标准化为字符串列表"""
    if args is None:
        return []
    if isinstance(args, list):
        result = []
        for item in args:
            if isinstance(item, (dict, list)):
                result.append(json.dumps(item, ensure_ascii=False))
            else:
                result.append(str(item))
        return result
    if isinstance(args, str):
        args = args.strip()
        if args.startswith('[') and args.endswith(']'):
            try:
                parsed = json.loads(args)
                if isinstance(parsed, list):
                    result = []
                    for item in parsed:
                        if isinstance(item, (dict, list)):
                            result.append(json.dumps(item, ensure_ascii=False))
                        else:
                            result.append(str(item))
                    return result
            except json.JSONDecodeError:
                pass
        return [args] if args else []
    return []


def reset_retry_guard() -> None:
    """在每次新的 Agent 请求开始前重置重复失败检测状态。"""
    state = _get_retry_guard_state()
    with _RETRY_GUARD_LOCK:
        state["signature"] = None
        state["consecutive_failures"] = 0


def _normalize_signature_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(value)
    return str(value).strip()


def _build_call_signature(tool_name: str, **kwargs: Any) -> str:
    parts = [tool_name]
    for key in sorted(kwargs):
        parts.append(f"{key}={_normalize_signature_value(kwargs[key])}")
    return " | ".join(parts)


def _looks_like_error_output(output: str) -> bool:
    text = (output or "").strip()
    if not text:
        return False

    error_markers = (
        "错误：",
        "未找到技能",
        "中未找到脚本",
        "执行失败",
        "执行超时",
        "命令执行失败",
        "命令执行超时",
        "添加知识失败",
        "文档添加失败",
        "搜索失败",
        "搜索请求失败",
        "搜索超时",
        "搜索记忆失败",
        "添加长期记忆失败",
        "记忆系统未初始化",
        "未配置",
        "缺少必要的依赖库",
        "遇到问题",
        "功能未启用",
    )
    return any(marker in text for marker in error_markers)


def _check_retry_guard_before_exec(tool_name: str, **signature_parts: Any) -> Optional[str]:
    """在工具执行前检查是否已被重试保护拦截。返回拦截消息或 None。"""
    signature = _build_call_signature(tool_name, **signature_parts)
    state = _get_retry_guard_state()
    with _RETRY_GUARD_LOCK:
        if state["signature"] == signature and state["consecutive_failures"] >= 3:
            last_err = state.get("last_error_output") or "无详细错误信息"
            sig_display = signature[:300]
            state["signature"] = None
            state["consecutive_failures"] = 0
            state["last_error_output"] = None
            return (
                f"[重试保护] 使用 [{sig_display}] 执行工具已经连续错误三次，请重新阅读 SKILL.md 文档，调整参数后再调用工具。\n\n"
                f"完整报错信息：\n{last_err}"
            )
    return None


def _apply_retry_guard(tool_name: str, signature: str, output: str) -> str:
    state = _get_retry_guard_state()
    if not _looks_like_error_output(output):
        with _RETRY_GUARD_LOCK:
            state["signature"] = None
            state["consecutive_failures"] = 0
        return output

    with _RETRY_GUARD_LOCK:
        if state["signature"] == signature:
            state["consecutive_failures"] += 1
        else:
            state["signature"] = signature
            state["consecutive_failures"] = 1

        state["last_error_output"] = output.strip()[:2000]
        consecutive_failures = state["consecutive_failures"]

    if consecutive_failures >= 3 and _RETRY_GUARD_PROMPT not in output:
        logger.warning("检测到连续 %s 次相同失败调用: %s", consecutive_failures, signature)
        return f"{output}{_RETRY_GUARD_PROMPT}"
    return output


_MAX_INLINE_OUTPUT_CHARS = 8000
_TRUNCATED_OUTPUT_DIR = _PROJECT_ROOT / "data" / "truncated_outputs"


def _save_truncated_output(tool_name: str, output: str) -> Optional[str]:
    try:
        _TRUNCATED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[^\w]', '_', tool_name)
        filename = f"{safe_name}_{ts}.txt"
        path = _TRUNCATED_OUTPUT_DIR / filename
        path.write_text(output, encoding="utf-8")
        return str(path)
    except Exception as e:
        logger.warning(f"保存截断输出失败: {e}")
        return None


def _finalize_tool_output(tool_name: str, output: str, **signature_parts: Any) -> str:
    _record_tool_error_memory(tool_name, output, **signature_parts)
    signature = _build_call_signature(tool_name, **signature_parts)
    result = _apply_retry_guard(tool_name, signature, output)
    if len(result) > _MAX_INLINE_OUTPUT_CHARS:
        saved_path = _save_truncated_output(tool_name, result)
        if saved_path:
            preview = result[:_MAX_INLINE_OUTPUT_CHARS]
            result = f"{preview}\n\n... [输出过长，已截断。完整结果已保存至: {saved_path}]"
    return result


class GetSkillInput(BaseModel):
    """获取技能说明的输入参数"""
    skill_name: str = Field(default="", description="技能名称")


class RunScriptInput(BaseModel):
    """执行脚本的输入参数"""
    skill_name: str = Field(default="", description="技能名称")
    script_name: str = Field(default="", description="脚本文件名，如 main.py")
    args: Union[str, List] = Field(default="", description="脚本参数，JSON数组或列表")
    env: str = Field(default="{}", description="环境变量，JSON对象格式")


class ExecBashInput(BaseModel):
    """执行 Bash 命令的输入参数"""
    command: str = Field(default="", description="要执行的 Bash 命令")
    timeout: int = Field(default=120, description="超时时间（秒）")


class ExecPythonFileInput(BaseModel):
    """执行 Python 文件的输入参数"""
    script_path: str = Field(default="", description="要执行的 Python 文件路径")
    args: Union[str, List] = Field(default="", description="脚本参数，JSON数组或列表")
    env: str = Field(default="{}", description="环境变量，JSON对象格式")
    timeout: int = Field(default=900, description="超时时间（秒）")
    workdir: str = Field(default="", description="工作目录，可选；默认使用脚本所在目录")


def _strip_session_prefix(path_str: str) -> str:
    from tools.agent_tool import _strip_session_prefix as _agent_strip
    return _agent_strip(path_str)


def _resolve_path(path_str: str, *, access: str = "read") -> Path:
    return resolve_tool_path(path_str, access=access).resolved


class WriteTextFileInput(BaseModel):
    """写入文本文件的输入参数"""
    file_path: str = Field(default="", description="要写入的文件路径")
    content: str = Field(default="", description="完整文件内容")
    encoding: str = Field(default="utf-8", description="文件编码，默认 utf-8")


class SearchArtifactsInput(BaseModel):
    """搜索历史产物的输入参数"""
    query: str = Field(default="", description="搜索关键词，支持文件类型、任务类型、目标文件名等")
    scope: str = Field(default="current", description="搜索范围：current 或 reusable")
    path_filter: str = Field(default="", description="路径过滤，仅返回路径包含该子串的产物（如会话ID、目录名）")
    limit: int = Field(default=10, description="返回结果上限，默认10")


class CheckArtifactExistsInput(BaseModel):
    """检查产物是否存在的输入参数"""
    artifact_path: str = Field(default="", description="要检查的产物路径或文件名")
    scope: str = Field(default="current", description="搜索范围：current 或 reusable")


class SearchToolErrorMemoryInput(BaseModel):
    """搜索工具错误记忆库的输入参数"""
    query: str = Field(default="", description="搜索关键词，可用 tool 名、skill 名、脚本名、错误摘要等")
    limit: int = Field(default=10, description="返回条数上限，默认 10")


class ReadArtifactInput(BaseModel):
    """读取文本产物的输入参数"""
    artifact_path: str = Field(default="", description="产物文件路径")
    max_chars: int = Field(default=12000, description="最多读取字符数，默认12000")


class CreateScheduledTaskInput(BaseModel):
    """创建定时任务的输入参数"""
    command: str = Field(default="", description="未来到点后交给Agent执行的自然语言任务，不要包含定时表达")
    repeat: str = Field(default="none", description="重复规则：none 或 daily")
    run_time: str = Field(default="", description="每日任务执行时间，HH:MM")
    scheduled_at: str = Field(default="", description="一次性任务执行时间，ISO格式或 YYYY-MM-DD HH:MM:SS")
    timezone: str = Field(default="Asia/Shanghai", description="时区标识，默认 Asia/Shanghai")
    enabled: bool = Field(default=True, description="是否启用任务")


class ListScheduledTasksInput(BaseModel):
    """查询定时任务的输入参数"""
    include_all_sessions: bool = Field(default=False, description="是否查询所有会话任务，默认只查当前会话")


class CancelScheduledTaskInput(BaseModel):
    """取消定时任务的输入参数"""
    task_id: str = Field(default="", description="要取消的定时任务ID")


_SKILL_REGISTRY: List[Any] = []
_SESSION_ROOT = _PROJECT_ROOT / "data" / "sessions"
_REUSABLE_SCRIPT_EXTENSIONS = {".py"}
_READABLE_ARTIFACT_EXTENSIONS = {".py", ".md", ".txt", ".json", ".csv"}
_TOOL_ERROR_MEMORY_PATH = _PROJECT_ROOT / "data" / "tool_error_memory.md"
_TOOL_ERROR_INDEX_PATH = _PROJECT_ROOT / "data" / ".tool_error_memory_index.json"
_TOOL_ERROR_MEMORY_LOCK = threading.Lock()


def set_skill_registry(skills: List[Any]):
    """设置技能注册表（由 skills/__init__.py 调用）"""
    global _SKILL_REGISTRY
    _SKILL_REGISTRY = skills


def _find_skill(skill_name: str) -> Optional[Any]:
    """查找技能"""
    for skill in _SKILL_REGISTRY:
        if skill.name == skill_name:
            return skill
    return None


def _load_session_index() -> Dict[str, Any]:
    index_path = _SESSION_ROOT / ".session_index.json"
    if not index_path.exists():
        return {"sessions": []}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取 session 索引失败: {e}")
        return {"sessions": []}


def _get_active_session_id() -> str:
    index = _load_session_index()
    sessions = index.get("sessions", [])
    active_sessions = [s for s in sessions if s.get("status") == "active"]
    if active_sessions:
        active_sessions.sort(key=lambda s: s.get("last_active", ""), reverse=True)
        return active_sessions[0].get("session_id", "")
    if sessions:
        sessions.sort(key=lambda s: s.get("last_active", ""), reverse=True)
        return sessions[0].get("session_id", "")
    return ""


def _iter_candidate_artifacts(scope: str) -> List[Path]:
    if not _SESSION_ROOT.exists():
        return []

    scope = (scope or "current").strip().lower()
    candidates: List[Path] = []

    if scope == "current":
        session_id = _get_active_session_id()
        if not session_id:
            return []
        session_dir = _SESSION_ROOT / session_id
        # 搜索整个会话目录下的所有文件
        for path in session_dir.rglob("*"):
            if path.is_file():
                candidates.append(path)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates

    if scope == "reusable":
        # 跨会话搜索可复用脚本（outputs 目录下的 .py）
        for outputs_dir in _SESSION_ROOT.glob("session-*/outputs"):
            for path in outputs_dir.iterdir():
                if path.is_file() and path.suffix.lower() in _REUSABLE_SCRIPT_EXTENSIONS:
                    candidates.append(path)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates

    return []


def _build_artifact_record(path: Path) -> Dict[str, Any]:
    session_id = ""
    for part in path.parts:
        if part.startswith("session-"):
            session_id = part
            break

    stat = path.stat()
    ext = path.suffix.lower()
    reusable = ext in _REUSABLE_SCRIPT_EXTENSIONS and path.parent.name == "outputs"
    artifact_type = "text"
    if ext == ".py":
        artifact_type = "python_script"
    elif ext == ".json":
        artifact_type = "json"
    elif ext in {".csv", ".tsv"}:
        artifact_type = "table"
    elif ext in {".xlsx", ".xls", ".xlsm"}:
        artifact_type = "spreadsheet"

    return {
        "path": str(path),
        "name": path.name,
        "session_id": session_id,
        "artifact_type": artifact_type,
        "extension": ext,
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "reusable": reusable,
    }


def _artifact_matches_query(record: Dict[str, Any], query: str) -> bool:
    if not query:
        return True
    searchable = " ".join([
        str(record.get("name", "")).lower(),
        str(record.get("path", "")).lower(),
        str(record.get("artifact_type", "")).lower(),
        str(record.get("extension", "")).lower(),
        str(record.get("session_id", "")).lower(),
    ])
    # 支持逗号、空格分隔的多关键词，任一匹配即可
    keywords = [w.strip().lower() for w in query.replace(",", " ").split() if len(w.strip()) > 1]
    if not keywords:
        return True
    return any(kw in searchable for kw in keywords)


def _resolve_artifact_candidates(artifact_path: str, scope: str = "current") -> List[Path]:
    raw = str(artifact_path or "").strip().strip('"').strip("'")
    if not raw:
        return []

    direct_path = Path(raw)
    if direct_path.is_absolute():
        resolved_direct = direct_path.resolve()
    else:
        resolved_direct = _resolve_path(raw)

    candidates: List[Path] = []
    if resolved_direct.exists() and resolved_direct.is_file():
        candidates.append(resolved_direct)

    normalized_raw = raw.replace("\\", "/").lower()
    base_name = Path(raw).name.lower()
    for path in _iter_candidate_artifacts(scope):
        normalized_candidate = str(path).replace("\\", "/").lower()
        if normalized_candidate == normalized_raw or normalized_candidate.endswith(normalized_raw):
            candidates.append(path)
            continue
        if path.name.lower() == base_name:
            candidates.append(path)

    deduped: List[Path] = []
    seen = set()
    for path in candidates:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _extract_error_core(output: str) -> str:
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if not lines:
        return ""
    for line in reversed(lines):
        if not line.lower().startswith("traceback"):
            return line
    return lines[-1]


def _sanitize_error_signature_text(text: str) -> str:
    normalized = (text or "").lower()
    normalized = re.sub(r"[a-z]:\\[^\s]+", "<path>", normalized)
    normalized = re.sub(r"/[^\s]+", "<path>", normalized)
    normalized = re.sub(r"session-[0-9]+-[a-z0-9]+", "session-<id>", normalized)
    normalized = re.sub(r"\b\d+\b", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _build_tool_error_entry(tool_name: str, output: str, signature_parts: Dict[str, Any]) -> Dict[str, Any]:
    error_core = _extract_error_core(output)
    target = signature_parts.get("script_name") or signature_parts.get("script_path") or signature_parts.get("skill_name") or signature_parts.get("command") or ""
    raw_signature = " | ".join([tool_name, str(target), _sanitize_error_signature_text(error_core)])
    signature = hashlib.sha1(raw_signature.encode("utf-8")).hexdigest()
    input_fields = {key: _normalize_signature_value(value) for key, value in signature_parts.items() if value not in (None, "", [], {})}
    now = datetime.now().isoformat()
    return {
        "signature": signature,
        "tool_name": tool_name,
        "target": str(target),
        "input_fields": input_fields,
        "error_core": error_core,
        "full_error": (output or "").strip(),
        "count": 1,
        "first_seen": now,
        "last_seen": now,
    }


def _load_tool_error_index() -> Dict[str, Any]:
    if not _TOOL_ERROR_INDEX_PATH.exists():
        return {"entries": []}
    try:
        return json.loads(_TOOL_ERROR_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取工具错误索引失败: {e}")
        return {"entries": []}


def _render_tool_error_markdown(entries: List[Dict[str, Any]]) -> str:
    lines = [
        "# 工具调用错误记忆库",
        "",
        "用于记录全局范围内出现过的 tool / skill / 脚本调用错误，帮助后续任务避免重复走弯路。",
        "",
    ]
    for entry in sorted(entries, key=lambda item: item.get("last_seen", ""), reverse=True):
        lines.extend([
            f"## {entry.get('tool_name', 'unknown')} | {entry.get('target', 'unknown') or 'unknown'}",
            "",
            f"- 签名: `{entry.get('signature', '')}`",
            f"- 首次记录: `{entry.get('first_seen', '')}`",
            f"- 最近出现: `{entry.get('last_seen', '')}`",
            f"- 累计次数: `{entry.get('count', 1)}`",
            f"- 错误摘要: `{entry.get('error_core', '')}`",
            "- 输入字段:",
        ])
        input_fields = entry.get("input_fields", {}) or {}
        if input_fields:
            for key, value in input_fields.items():
                lines.append(f"  - `{key}`: `{value}`")
        else:
            lines.append("  - 无")
        lines.extend([
            "- 完整错误:",
            "```text",
            entry.get("full_error", ""),
            "```",
            "",
        ])
    return "\n".join(lines)


def _save_tool_error_index(entries: List[Dict[str, Any]]) -> None:
    _TOOL_ERROR_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOOL_ERROR_INDEX_PATH.write_text(json.dumps({"entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    _TOOL_ERROR_MEMORY_PATH.write_text(_render_tool_error_markdown(entries), encoding="utf-8")


def _record_tool_error_memory(tool_name: str, output: str, **signature_parts: Any) -> None:
    if not _looks_like_error_output(output):
        return
    entry = _build_tool_error_entry(tool_name, output, signature_parts)
    with _TOOL_ERROR_MEMORY_LOCK:
        data = _load_tool_error_index()
        entries = data.get("entries", [])
        for existing in entries:
            if existing.get("signature") == entry["signature"]:
                existing["count"] = int(existing.get("count", 1)) + 1
                existing["last_seen"] = datetime.now().isoformat()
                if len(output or "") > len(existing.get("full_error", "")):
                    existing["full_error"] = (output or "").strip()
                    existing["error_core"] = entry["error_core"]
                _save_tool_error_index(entries)
                return
        entries.append(entry)
        _save_tool_error_index(entries)


def _impl_get_skill(skill_name: str = "") -> str:
    parsed = _parse_json_if_needed(skill_name)
    if parsed:
        skill_name = parsed.get('skill_name', skill_name)
    
    skill_name = str(skill_name).strip().strip('"').strip("'")
    return _get_skill_cached(skill_name)


get_skill = build_agent_tool(
    name="get_skill",
    description=(
        "获取技能的完整说明和执行方法。"
        "在执行任务前，先调用此工具了解技能的功能、参数和使用方法。"
        "返回内容包括：技能描述、使用说明、可用脚本、参考文档。"
    ),
    args_schema=GetSkillInput,
    func=_impl_get_skill,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


@lru_cache(maxsize=128)
def _get_skill_cached(skill_name: str) -> str:
    skill_name = str(skill_name).strip()
    skill = _find_skill(skill_name)
    
    if not skill:
        available = [s.name for s in _SKILL_REGISTRY]
        return _finalize_tool_output(
            "get_skill",
            f"未找到技能 '{skill_name}'。可用技能：{available}",
            skill_name=skill_name,
        )
    
    lines = [
        f"=== 技能【{skill_name}】完整说明 ===",
        "",
        "【触发条件】",
        skill.description,
        "",
        "【使用说明】",
        skill.prompt,
    ]
    
    if skill.scripts:
        lines.extend([
            "",
            "【可执行脚本】",
            "使用 run_script 工具执行以下脚本：",
        ])
        for script in skill.scripts:
            lines.append(f"  - {script}")
        lines.append("")
        lines.append("示例：")
        lines.append(f"  run_script(skill_name='{skill_name}', script_name='{skill.scripts[0]}', args='[]')")
    
    if skill.references:
        lines.extend([
            "",
            "【参考文档】",
            "使用 read_artifact 工具读取以下文档：",
        ])
        for ref in skill.references:
            lines.append(f"  - {ref}")
    
    if skill.is_knowledge_only:
        lines.extend([
            "",
            "【说明】",
            "这是知识型技能，提供专业知识和指导。",
            "请根据上述说明直接回答用户问题，无需执行脚本。",
        ])
    
    return _finalize_tool_output("get_skill", "\n".join(lines), skill_name=skill_name)


def _impl_search_tool_error_memory(query: str = "", limit: int = 10) -> str:
    query = (query or "").strip()
    if not query:
        return _finalize_tool_output("search_tool_error_memory", "错误：搜索关键词不能为空", query=query, limit=limit)

    data = _load_tool_error_index()
    entries = data.get("entries", [])
    if not entries:
        return _finalize_tool_output("search_tool_error_memory", "工具错误记忆库为空", query=query, limit=limit)

    tokens = [token for token in query.lower().split() if token]
    matches = []
    for entry in entries:
        searchable = " ".join([
            str(entry.get("tool_name", "")),
            str(entry.get("target", "")),
            str(entry.get("error_core", "")),
            json.dumps(entry.get("input_fields", {}), ensure_ascii=False),
        ]).lower()
        if all(token in searchable for token in tokens):
            matches.append(entry)

    if not matches:
        return _finalize_tool_output("search_tool_error_memory", f"未找到与 '{query}' 相关的历史错误", query=query, limit=limit)

    lines = [f"找到 {min(len(matches), limit)} 条相关历史错误：", ""]
    for index, entry in enumerate(matches[: max(1, limit)], start=1):
        lines.extend([
            f"{index}. 工具: {entry.get('tool_name', '')}",
            f"   目标: {entry.get('target', '') or 'unknown'}",
            f"   错误摘要: {entry.get('error_core', '')}",
            f"   累计次数: {entry.get('count', 1)}",
            f"   最近出现: {entry.get('last_seen', '')}",
            "",
        ])
    lines.append(f"完整记录文件: {_TOOL_ERROR_MEMORY_PATH}")
    return _finalize_tool_output("search_tool_error_memory", "\n".join(lines), query=query, limit=limit)


search_tool_error_memory = build_agent_tool(
    name="search_tool_error_memory",
    description="搜索全局工具错误记忆库，帮助避免重复踩坑。",
    args_schema=SearchToolErrorMemoryInput,
    func=_impl_search_tool_error_memory,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


def _impl_run_script(skill_name: str = "", script_name: str = "", args: Union[str, List] = "", env: str = "{}") -> str:
    parsed = _parse_json_if_needed(skill_name)
    if parsed and 'script_name' in parsed:
        skill_name = parsed.get('skill_name', skill_name)
        script_name = parsed.get('script_name', script_name)
        args = parsed.get('args', args)
        env = parsed.get('env', env)
    
    skill_name = str(skill_name).strip().strip('"').strip("'")
    script_name = str(script_name).strip().strip('"').strip("'")
    
    _retry_block = _check_retry_guard_before_exec("run_script", skill_name=skill_name, script_name=script_name, args=args)
    if _retry_block:
        return _retry_block
    
    if not skill_name:
        return _finalize_tool_output("run_script", "错误：skill_name 参数不能为空", skill_name=skill_name, script_name=script_name, args=args)

    if not script_name:
        return _finalize_tool_output("run_script", "错误：script_name 参数不能为空", skill_name=skill_name, script_name=script_name, args=args)
    
    skill = _find_skill(skill_name)
    if not skill:
        available = [s.name for s in _SKILL_REGISTRY]
        return _finalize_tool_output(
            "run_script",
            f"未找到技能 '{skill_name}'。可用技能：{available}",
            skill_name=skill_name,
            script_name=script_name,
            args=args,
        )
    
    script_path = skill.get_script_path(script_name)
    if not script_path:
        return _finalize_tool_output(
            "run_script",
            f"技能 '{skill_name}' 中未找到脚本 '{script_name}'。可用脚本：{skill.scripts}",
            skill_name=skill_name,
            script_name=script_name,
            args=args,
        )
    
    args_list = _normalize_args(args)
    
    try:
        env_dict = json.loads(env) if env else {}
    except json.JSONDecodeError:
        env_dict = {}
    
    try:
        cmd = [sys.executable, str(script_path)] + args_list
        
        run_env = os.environ.copy()
        run_env.update(env_dict)
        run_env['PYTHONIOENCODING'] = 'utf-8'
        
        session_output_dir = _SESSION_CONTEXT.get("output_dir")
        session_id = _SESSION_CONTEXT.get("session_id")
        if session_id:
            run_env['SESSION_ID'] = str(session_id)
        if session_output_dir:
            run_env['SESSION_OUTPUT_DIR'] = str(session_output_dir)
            exec_cwd = str(session_output_dir)
        else:
            exec_cwd = str(script_path.parent)
        
        logger.info(f"执行脚本: {' '.join(cmd)}, cwd={exec_cwd}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=run_env,
            cwd=exec_cwd,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
        
        stdout_lines = []
        stderr_lines = []
        
        import threading
        
        def read_stream(stream, lines, log_level):
            for line in iter(stream.readline, ''):
                if line:
                    lines.append(line)
                    if log_level == 'INFO':
                        logger.info(f"[脚本输出] {line.rstrip()}")
                    else:
                        logger.warning(f"[脚本错误] {line.rstrip()}")
        
        stdout_thread = threading.Thread(
            target=read_stream, 
            args=(process.stdout, stdout_lines, 'INFO'),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=read_stream, 
            args=(process.stderr, stderr_lines, 'WARNING'),
            daemon=True
        )
        
        stdout_thread.start()
        stderr_thread.start()
        
        try:
            returncode = process.wait(timeout=900)
        except subprocess.TimeoutExpired:
            process.kill()
            return _finalize_tool_output("run_script", "脚本执行超时（>900秒）", skill_name=skill_name, script_name=script_name, args=args_list)
        
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        
        stdout = ''.join(stdout_lines)
        stderr = ''.join(stderr_lines)
        
        if returncode != 0:
            return _finalize_tool_output(
                "run_script",
                f"脚本执行失败（退出码 {returncode}）：\n{stderr}",
                skill_name=skill_name,
                script_name=script_name,
                args=args_list,
            )

        return _finalize_tool_output(
            "run_script",
            stdout or "脚本执行成功（无输出）",
            skill_name=skill_name,
            script_name=script_name,
            args=args_list,
        )
        
    except subprocess.TimeoutExpired:
        return _finalize_tool_output("run_script", "脚本执行超时（>900秒）", skill_name=skill_name, script_name=script_name, args=args_list)
    except Exception as e:
        logger.error(f"脚本执行失败: {e}", exc_info=True)
        return _finalize_tool_output(
            "run_script",
            f"脚本执行失败：{str(e)}",
            skill_name=skill_name,
            script_name=script_name,
            args=args_list,
        )


run_script = build_agent_tool(
    name="run_script",
    description=(
        "执行技能中的 Python 脚本。"
        "在调用此工具前，必须先调用 get_skill 了解脚本用法。"
        "脚本的工作目录已自动设为当前会话的输出目录，因此输出文件参数只写文件名（如 result.json），不要加任何目录前缀。"
        "脚本的标准输出将作为结果返回。"
    ),
    args_schema=RunScriptInput,
    func=_impl_run_script,
    is_readonly=False,
    is_destructive=True,
    is_concurrency_safe=False,
    check_permissions_fn=make_skill_script_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="skill_script"),
)


def _impl_exec_bash(command: str = "", timeout: int = 120) -> str:
    parsed = _parse_json_if_needed(command)
    if parsed:
        command = parsed.get('command', command)
        timeout = parsed.get('timeout', timeout)
    
    command = str(command).strip()
    
    _retry_block = _check_retry_guard_before_exec("exec_bash", command=command, timeout=timeout)
    if _retry_block:
        return _retry_block
    
    if not command:
        return _finalize_tool_output("exec_bash", "错误：命令不能为空", command=command, timeout=timeout)

    normalized_command = command.lower()
    if normalized_command.startswith("powershell ") or normalized_command.startswith("powershell.exe ") or normalized_command.startswith("pwsh ") or normalized_command.startswith("pwsh.exe ") or normalized_command.startswith("bash ") or normalized_command.startswith("sh "):
        return _finalize_tool_output(
            "exec_bash",
            "错误：`exec_bash` 已经在内部自动选择 shell 执行命令。不要再在 command 中嵌套 `powershell -Command`、`pwsh -Command`、`bash -lc` 或 `sh -lc`；请直接传入命令语句本体。",
            command=command,
            timeout=timeout,
        )
    
    try:
        logger.info(f"执行命令: {command}")
        run_env = _build_exec_env()
        Path(run_env['MPLCONFIGDIR']).mkdir(parents=True, exist_ok=True)
        shell_prefix, shell_name = _detect_shell_command()
        shell_cmd = shell_prefix + [command]
        
        result = subprocess.run(
            shell_cmd,
            capture_output=True,
            timeout=timeout,
            cwd=str(_PROJECT_ROOT),
            env=run_env,
        )
        
        stdout = result.stdout.decode('utf-8', errors='replace') if result.stdout else ""
        stderr = result.stderr.decode('utf-8', errors='replace') if result.stderr else ""
        
        output = stdout
        if stderr:
            output += f"\n[stderr]: {stderr}"
        if output:
            output = f"[shell={shell_name}]\n{output}"
        
        if result.returncode != 0:
            return _finalize_tool_output(
                "exec_bash",
                f"命令执行失败（退出码 {result.returncode}）：\n{output}",
                command=command,
                timeout=timeout,
            )

        return _finalize_tool_output("exec_bash", output or "命令执行成功（无输出）", command=command, timeout=timeout)
        
    except subprocess.TimeoutExpired:
        return _finalize_tool_output("exec_bash", f"命令执行超时（>{timeout}秒）", command=command, timeout=timeout)
    except Exception as e:
        logger.error(f"命令执行失败: {e}", exc_info=True)
        return _finalize_tool_output("exec_bash", f"命令执行失败：{str(e)}", command=command, timeout=timeout)


exec_bash = build_agent_tool(
    name="exec_bash",
    description=(
        "通过当前环境可用的 shell 执行命令。"
        "用于执行系统命令，如文件操作、网络请求等。"
        "注意：此工具会直接执行命令，请谨慎使用。"
        "**环境信息：**"
        "- 执行环境：自动选择可用 shell（优先 powershell/pwsh，其次 bash/sh）"
        "- 当前 shell：由工具自动检测，不要假设固定是 PowerShell"
        "- Python 命令：使用 `python` 或 `python3`"
        "- 路径格式：跟随当前运行环境；Windows 用 Windows 路径，容器/Linux 用 POSIX 路径"
        "- 不要在 command 中再嵌套 `powershell -Command`、`pwsh -Command`、`bash -lc` 或 `sh -lc`"
        "- 复杂脚本优先写入 `.py` 文件，再用 `exec_python_file` 执行"
    ),
    args_schema=ExecBashInput,
    func=_impl_exec_bash,
    is_readonly=False,
    is_destructive=True,
    is_concurrency_safe=False,
    check_permissions_fn=make_exec_permission_fn("command"),
    permission_policy=ToolPermissionPolicy(policy_type="exec", command_field="command"),
)


def _impl_exec_python_file(
    script_path: str = "",
    args: Union[str, List] = "",
    env: str = "{}",
    timeout: int = 900,
    workdir: str = "",
) -> str:
    parsed = _parse_json_if_needed(script_path)
    if parsed and 'script_path' in parsed:
        script_path = parsed.get('script_path', script_path)
        args = parsed.get('args', args)
        env = parsed.get('env', env)
        timeout = parsed.get('timeout', timeout)
        workdir = parsed.get('workdir', workdir)

    script_path = str(script_path).strip().strip('"').strip("'")
    workdir = str(workdir).strip().strip('"').strip("'")
    args_list = _normalize_args(args)

    if not script_path:
        return _finalize_tool_output(
            "exec_python_file",
            "错误：script_path 参数不能为空",
            script_path=script_path,
            args=args_list,
            timeout=timeout,
            workdir=workdir,
        )

    script_file = resolve_tool_path(script_path, access="exec").resolved

    if not script_file.exists() or not script_file.is_file():
        return _finalize_tool_output(
            "exec_python_file",
            f"错误：Python 文件不存在: {script_file}",
            script_path=str(script_file),
            args=args_list,
            timeout=timeout,
            workdir=workdir,
        )

    if script_file.suffix.lower() != '.py':
        return _finalize_tool_output(
            "exec_python_file",
            f"错误：仅支持执行 .py 文件，当前文件: {script_file.name}",
            script_path=str(script_file),
            args=args_list,
            timeout=timeout,
            workdir=workdir,
        )

    try:
        env_dict = json.loads(env) if env else {}
    except json.JSONDecodeError:
        env_dict = {}

    exec_cwd_path = None
    if workdir:
        from agent.runtime.services.path_service import get_path_service
        cwd_result = get_path_service().resolve_simple(workdir, access="cwd")
        exec_cwd_path = cwd_result.resolved
    if exec_cwd_path is None:
        session_output_dir = _SESSION_CONTEXT.get("output_dir")
        if session_output_dir:
            exec_cwd_path = Path(session_output_dir)
        else:
            exec_cwd_path = script_file.parent
    exec_cwd = str(exec_cwd_path)

    try:
        cmd = [sys.executable, str(script_file)] + args_list
        run_env = _build_exec_env()
        run_env.update({str(k): str(v) for k, v in env_dict.items()})

        logger.info(f"执行 Python 文件: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=run_env,
            cwd=str(exec_cwd),
            text=True,
            encoding='utf-8',
            errors='replace',
        )

        stdout_lines = []
        stderr_lines = []

        def read_stream(stream, lines, log_level):
            for line in iter(stream.readline, ''):
                if line:
                    lines.append(line)
                    if log_level == 'INFO':
                        logger.info(f"[Python文件输出] {line.rstrip()}")
                    else:
                        logger.warning(f"[Python文件错误] {line.rstrip()}")

        stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines, 'INFO'), daemon=True)
        stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines, 'WARNING'), daemon=True)

        stdout_thread.start()
        stderr_thread.start()

        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            return _finalize_tool_output(
                "exec_python_file",
                f"Python 文件执行超时（>{timeout}秒）",
                script_path=str(script_file),
                args=args_list,
                timeout=timeout,
                workdir=str(exec_cwd),
            )

        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

        stdout = ''.join(stdout_lines)
        stderr = ''.join(stderr_lines)

        if returncode != 0:
            return _finalize_tool_output(
                "exec_python_file",
                f"Python 文件执行失败（退出码 {returncode}）：\n{stderr}",
                script_path=str(script_file),
                args=args_list,
                timeout=timeout,
                workdir=str(exec_cwd),
            )

        return _finalize_tool_output(
            "exec_python_file",
            stdout or "Python 文件执行成功（无输出）",
            script_path=str(script_file),
            args=args_list,
            timeout=timeout,
            workdir=str(exec_cwd),
        )
    except Exception as e:
        logger.error(f"Python 文件执行失败: {e}", exc_info=True)
        return _finalize_tool_output(
            "exec_python_file",
            f"Python 文件执行失败：{str(e)}",
            script_path=str(script_file),
            args=args_list,
            timeout=timeout,
            workdir=str(exec_cwd),
        )


exec_python_file = build_agent_tool(
    name="exec_python_file",
    description=(
        "执行一个本地 Python 文件。"
        "适用于先通过 `write_text_file` 写出临时 `.py` 脚本，再稳定执行该脚本。"
        "相比 `python -c \"...\"` 更适合多行逻辑、文件转换和 JSON 生成场景。"
        "脚本的工作目录已自动设为当前对话的输出目录，因此输出文件参数只写文件名（如 result.json），不要加任何目录前缀。"
    ),
    args_schema=ExecPythonFileInput,
    func=_impl_exec_python_file,
    is_readonly=False,
    is_destructive=True,
    is_concurrency_safe=False,
    check_permissions_fn=make_exec_permission_fn("command", ["script_path", "workdir"]),
    permission_policy=ToolPermissionPolicy(policy_type="exec", command_field="command", path_fields=["script_path", "workdir"]),
)


def _impl_write_text_file(file_path: str = "", content: str = "", encoding: str = "utf-8") -> str:
    parsed = _parse_json_if_needed(file_path)
    if parsed and 'file_path' in parsed:
        file_path = parsed.get('file_path', file_path)
        content = parsed.get('content', content)
        encoding = parsed.get('encoding', encoding)

    file_path = str(file_path).strip().strip('"').strip("'")
    encoding = str(encoding).strip() or 'utf-8'

    if not file_path:
        _retry_block = _check_retry_guard_before_exec("write_text_file", file_path=file_path)
        if _retry_block:
            return _finalize_tool_output(
                "write_text_file",
                _retry_block,
                file_path=file_path,
                encoding=encoding,
            )
        return _finalize_tool_output(
            "write_text_file",
            "错误：file_path 参数不能为空",
            file_path=file_path,
            encoding=encoding,
        )

    path_result = resolve_tool_path(file_path, access="write")
    if path_result.source == "no_context_rejected":
        return _finalize_tool_output(
            "write_text_file",
            "错误：无会话上下文时相对路径写入被拒绝。正确做法：只写文件名（如 result.py），系统会自动写入当前对话输出目录。不要传 data/sessions/... 等目录前缀。",
            file_path=file_path,
            encoding=encoding,
        )
    target_file = path_result.resolved

    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(str(content), encoding=encoding)
        return _finalize_tool_output(
            "write_text_file",
            f"文本文件写入成功：{target_file}",
            file_path=str(target_file),
            encoding=encoding,
        )
    except Exception as e:
        logger.error(f"写入文本文件失败: {e}", exc_info=True)
        return _finalize_tool_output(
            "write_text_file",
            f"写入文本文件失败：{str(e)}",
            file_path=str(target_file),
            encoding=encoding,
        )


write_text_file = build_agent_tool(
    name="write_text_file",
    description=(
        "直接写入文本文件。"
        "适用于生成临时 Python 脚本、JSON 文件、CSV 文件或其他文本文件，"
        "可避免通过 PowerShell here-string 或复杂转义来写文件。"
        "file_path 只写文件名（如 generate_data.py），系统会自动写入当前对话的输出目录。"
        "不要加任何目录前缀（不要写 data/sessions/xxx.py，否则路径嵌套出错）。"
        "不要传目录路径，只传文件名。"
    ),
    args_schema=WriteTextFileInput,
    func=_impl_write_text_file,
    is_readonly=False,
    is_destructive=True,
    is_concurrency_safe=False,
    check_permissions_fn=make_write_permission_fn("file_path"),
    permission_policy=ToolPermissionPolicy(policy_type="write", path_field="file_path"),
)


def _impl_search_artifacts(query: str = "", scope: str = "current", path_filter: str = "", limit: int = 10) -> str:
    parsed = _parse_json_if_needed(query)
    if parsed and 'query' in parsed:
        query = parsed.get('query', query)
        scope = parsed.get('scope', scope)
        path_filter = parsed.get('path_filter', path_filter)
        limit = parsed.get('limit', limit)

    scope = str(scope).strip().lower() or "current"
    path_filter = str(path_filter).strip()
    try:
        limit = max(1, min(int(limit), 20))
    except Exception:
        limit = 10

    if scope not in {"current", "reusable"}:
        return _finalize_tool_output(
            "search_artifacts",
            "错误：scope 仅支持 `current` 或 `reusable`",
            query=query,
            scope=scope,
            path_filter=path_filter,
            limit=limit,
        )

    candidates = _iter_candidate_artifacts(scope)
    if path_filter:
        pf_lower = path_filter.lower()
        candidates = [p for p in candidates if pf_lower in str(p).lower()]

    records = [_build_artifact_record(path) for path in candidates]
    matched = [record for record in records if _artifact_matches_query(record, query)]
    matched = matched[:limit]

    if not matched:
        return _finalize_tool_output(
            "search_artifacts",
            f"未找到匹配产物。scope={scope}, query={query!r}, path_filter={path_filter!r}",
            query=query,
            scope=scope,
            path_filter=path_filter,
            limit=limit,
        )

    lines = [f"找到 {len(matched)} 个匹配产物（scope={scope}）：", ""]
    for idx, record in enumerate(matched, start=1):
        lines.extend([
            f"{idx}. {record['name']}",
            f"   - 类型: {record['artifact_type']}",
            f"   - 会话: {record['session_id'] or '-'}",
            f"   - 路径: {record['path']}",
            f"   - 大小: {record['size_bytes']} bytes",
            f"   - 更新时间: {record['updated_at']}",
            f"   - 可复用: {record['reusable']}",
        ])

    return _finalize_tool_output(
        "search_artifacts",
        "\n".join(lines),
        query=query,
        scope=scope,
        path_filter=path_filter,
        limit=limit,
    )


search_artifacts = build_agent_tool(
    name="search_artifacts",
    description=(
        "搜索当前会话或历史可复用产物。"
        "scope=current 搜索当前会话所有文件（包括 outputs 和 uploads 目录下的脚本、数据文件、图片等）。"
        "scope=reusable 跨会话搜索可复用脚本。"
        "path_filter 可按路径子串过滤（如会话ID、目录名）。"
        "query 支持按文件名、路径、类型关键词搜索。"
    ),
    args_schema=SearchArtifactsInput,
    func=_impl_search_artifacts,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


def _impl_check_artifact_exists(artifact_path: str = "", scope: str = "current") -> str:
    parsed = _parse_json_if_needed(artifact_path)
    if parsed and 'artifact_path' in parsed:
        artifact_path = parsed.get('artifact_path', artifact_path)
        scope = parsed.get('scope', scope)

    artifact_path = str(artifact_path).strip().strip('"').strip("'")
    scope = str(scope).strip().lower() or "current"
    if not artifact_path:
        return _finalize_tool_output(
            "check_artifact_exists",
            "错误：artifact_path 参数不能为空",
            artifact_path=artifact_path,
            scope=scope,
        )

    if scope not in {"current", "reusable"}:
        return _finalize_tool_output(
            "check_artifact_exists",
            "错误：scope 仅支持 `current` 或 `reusable`",
            artifact_path=artifact_path,
            scope=scope,
        )

    candidates = _resolve_artifact_candidates(artifact_path, scope)
    if not candidates:
        return _finalize_tool_output(
            "check_artifact_exists",
            f"未找到目标产物：{artifact_path}",
            artifact_path=artifact_path,
            scope=scope,
        )

    lines = [f"确认找到 {len(candidates)} 个匹配产物：", ""]
    for idx, path in enumerate(candidates, start=1):
        lines.extend([
            f"{idx}. {path.name}",
            f"   - 路径: {path}",
            f"   - 大小: {path.stat().st_size} bytes",
            f"   - 更新时间: {datetime.fromtimestamp(path.stat().st_mtime).isoformat()}",
        ])

    return _finalize_tool_output(
        "check_artifact_exists",
        "\n".join(lines),
        artifact_path=artifact_path,
        scope=scope,
    )


check_artifact_exists = build_agent_tool(
    name="check_artifact_exists",
    description=(
        "检查目标产物是否真实存在。"
        "优先按给定路径直接判断；如果传入的是文件名或相对路径，则在当前会话 outputs"
        "或 reusable 范围内做文件名/后缀匹配，适合校验 `.xlsx`、`.docx`、`.pdf`、图片等二进制产物。"
    ),
    args_schema=CheckArtifactExistsInput,
    func=_impl_check_artifact_exists,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


def _impl_read_artifact(artifact_path: str = "", max_chars: int = 12000) -> str:
    parsed = _parse_json_if_needed(artifact_path)
    if parsed and 'artifact_path' in parsed:
        artifact_path = parsed.get('artifact_path', artifact_path)
        max_chars = parsed.get('max_chars', max_chars)

    artifact_path = str(artifact_path).strip().strip('"').strip("'")
    if not artifact_path:
        return _finalize_tool_output(
            "read_artifact",
            "错误：artifact_path 参数不能为空",
            artifact_path=artifact_path,
            max_chars=max_chars,
        )

    path = resolve_tool_path(artifact_path, access="read").resolved

    if not path.exists() or not path.is_file():
        return _finalize_tool_output(
            "read_artifact",
            f"错误：产物文件不存在: {path}",
            artifact_path=str(path),
            max_chars=max_chars,
        )

    ext = path.suffix.lower()
    if ext not in _READABLE_ARTIFACT_EXTENSIONS:
        return _finalize_tool_output(
            "read_artifact",
            f"错误：当前仅支持读取 .py、.md、.txt、.json、.csv 文件，文件类型 {ext} 不支持直接读取",
            artifact_path=str(path),
            max_chars=max_chars,
        )

    try:
        max_chars = max(1000, min(int(max_chars), 50000))
    except Exception:
        max_chars = 12000

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n[已截断，仅读取前部内容]"
        header = f"=== 产物内容: {path.name} ===\n路径: {path}\n"
        return _finalize_tool_output(
            "read_artifact",
            header + "\n" + content,
            artifact_path=str(path),
            max_chars=max_chars,
        )
    except Exception as e:
        logger.error(f"读取产物失败: {e}", exc_info=True)
        return _finalize_tool_output(
            "read_artifact",
            f"读取产物失败：{str(e)}",
            artifact_path=str(path),
            max_chars=max_chars,
        )


read_artifact = build_agent_tool(
    name="read_artifact",
    description=(
        "读取文本类产物内容。"
        "支持读取 `.py`、`.md`、`.txt`、`.json`、`.csv` 文本文件。"
        "不支持 Excel、`.docx` 等二进制文件，避免内存占用过大或上下文超限。"
    ),
    args_schema=ReadArtifactInput,
    func=_impl_read_artifact,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_read_path_permission_fn("artifact_path"),
    permission_policy=ToolPermissionPolicy(policy_type="read_path", path_field="artifact_path"),
)


class _RAGConfigManager:
    _RETRIEVER_KEYS = ("persist_dir", "embedding_model", "top_k")

    def __init__(self):
        self._config: Dict[str, Any] = {
            "enabled": False,
            "persist_dir": "./data/vector_store",
            "embedding_model": "BAAI/bge-base-zh-v1.5",
            "top_k": 5,
            "session_id": None,
        }
        self._retriever: Optional[Any] = None

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def update(self, **kwargs) -> None:
        retriever_changed = any(
            kwargs.get(k) != self._config.get(k) for k in self._RETRIEVER_KEYS
        )
        self._config.update(kwargs)
        if retriever_changed:
            self._retriever = None
            logger.info(f"RAG 核心配置已变更，retriever 将重建: {kwargs}")
        else:
            logger.info(f"RAG 会话级配置已更新（retriever 保持复用）: {kwargs}")

    @property
    def retriever(self) -> Optional[Any]:
        return self._retriever

    @retriever.setter
    def retriever(self, value: Optional[Any]) -> None:
        self._retriever = value


_RAG_CONFIG = _RAGConfigManager()
_rag_cfg_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "rag_config",
    default={},
)

_session_ctx_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "session_context",
)


class _SessionContextProxy:
    def get(self, key: str, default: Any = None) -> Any:
        return _session_ctx_var.get({}).get(key, default)

    def __getitem__(self, key: str) -> Any:
        return _session_ctx_var.get({}).get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        ctx = dict(_session_ctx_var.get({}))
        ctx[key] = value
        _session_ctx_var.set(ctx)


_SESSION_CONTEXT = _SessionContextProxy()


def set_session_context(session_id: str, output_dir: Optional[str] = None):
    ctx = {
        "session_id": session_id,
        "output_dir": None,
    }
    if output_dir:
        ctx["output_dir"] = output_dir
        os.makedirs(output_dir, exist_ok=True)
    else:
        ctx["output_dir"] = str(_SESSION_ROOT / session_id / "outputs")
        os.makedirs(ctx["output_dir"], exist_ok=True)
    _session_ctx_var.set(ctx)


def get_current_session_output_dir() -> Optional[str]:
    return _session_ctx_var.get({}).get("output_dir")


def get_current_session_id() -> Optional[str]:
    return _session_ctx_var.get({}).get("session_id")


def set_rag_config(
    enabled: bool = True,
    persist_dir: str = "./data/vector_store",
    embedding_model: str = "BAAI/bge-base-zh-v1.5",
    top_k: int = 5,
    session_id: Optional[str] = None,
):
    cfg = {
        "enabled": enabled,
        "persist_dir": persist_dir,
        "embedding_model": embedding_model,
        "top_k": top_k,
        "session_id": session_id,
    }
    _rag_cfg_var.set(cfg)
    _RAG_CONFIG.update(
        enabled=enabled,
        persist_dir=persist_dir,
        embedding_model=embedding_model,
        top_k=top_k,
        session_id=session_id,
    )


_retriever_lock = threading.Lock()


def _get_retriever():
    active_cfg = {**_RAG_CONFIG._config, **_rag_cfg_var.get({})}
    if not active_cfg.get("enabled", False):
        logger.info("RAG 未启用")
        return None

    retriever_key = (
        active_cfg.get("persist_dir", "./data/vector_store"),
        active_cfg.get("embedding_model", "BAAI/bge-base-zh-v1.5"),
        int(active_cfg.get("top_k", 5) or 5),
    )

    if getattr(_RAG_CONFIG, "_retriever_key", None) == retriever_key and _RAG_CONFIG.retriever is not None:
        return _RAG_CONFIG.retriever

    with _retriever_lock:
        if getattr(_RAG_CONFIG, "_retriever_key", None) == retriever_key and _RAG_CONFIG.retriever is not None:
            return _RAG_CONFIG.retriever
        try:
            from rag.vector_store import VectorStoreManager
            from rag.embeddings import EmbeddingManager
            persist_dir, embedding_model, top_k = retriever_key

            logger.info(f"初始化检索器: persist_dir={persist_dir}, embedding_model={embedding_model}, top_k={top_k}")

            import os
            permanent_dir = os.path.join(persist_dir, "permanent")
            if os.path.exists(permanent_dir):
                logger.info(f"永久知识库目录存在: {permanent_dir}")
                files = os.listdir(permanent_dir)
                logger.info(f"永久知识库目录内容: {files}")
            else:
                logger.warning(f"永久知识库目录不存在: {permanent_dir}")

            EmbeddingManager.reset()
            embedding_mgr = EmbeddingManager(model_name=embedding_model)
            store = VectorStoreManager(
                persist_dir=persist_dir,
                embedding_manager=embedding_mgr,
            )

            doc_count = store.get_document_count()
            logger.info(f"永久知识库文档数: {doc_count}")

            _RAG_CONFIG.retriever = store
            _RAG_CONFIG._retriever_key = retriever_key
            
        except Exception as e:
            logger.error(f"初始化检索器失败: {e}", exc_info=True)
            return None
    
    return _RAG_CONFIG.retriever


class KnowledgeSearchInput(BaseModel):
    """知识检索的输入参数"""
    query: str = Field(default="", description="检索查询文本")
    top_k: int = Field(default=5, description="返回结果数量")
    asset_kind: str = Field(default="", description="可选过滤：text_document / excel_asset / gis_asset / image_asset")
    index_mode: str = Field(default="", description="可选过滤：content_chunk / file_summary")
    folder_level_1: str = Field(default="", description="可选过滤：一级目录名")
    folder_level_2: str = Field(default="", description="可选过滤：二级目录名")
    folder_level_3: str = Field(default="", description="可选过滤：三级目录名")
    filename: str = Field(default="", description="可选过滤：文件名")


class AddKnowledgeInput(BaseModel):
    """添加知识的输入参数"""
    content: str = Field(default="", description="文档内容（文本）")
    file_path: str = Field(default="", description="文件路径（可选，与content二选一）")
    doc_name: str = Field(default="", description="文档名称（可选）")
    force_method: Optional[str] = Field(default=None, description="强制处理方式: 'context' 或 'vector'")


def _impl_knowledge_search(
    query: str = "",
    top_k: int = 5,
    asset_kind: str = "",
    index_mode: str = "",
    folder_level_1: str = "",
    folder_level_2: str = "",
    folder_level_3: str = "",
    filename: str = "",
) -> str:
    parsed = _parse_json_if_needed(query)
    if parsed:
        query = parsed.get('query', query)
        top_k = parsed.get('top_k', top_k)
        asset_kind = parsed.get('asset_kind', asset_kind)
        index_mode = parsed.get('index_mode', index_mode)
        folder_level_1 = parsed.get('folder_level_1', folder_level_1)
        folder_level_2 = parsed.get('folder_level_2', folder_level_2)
        folder_level_3 = parsed.get('folder_level_3', folder_level_3)
        filename = parsed.get('filename', filename)
    
    query = str(query).strip().strip('"').strip("'")
    
    if not query:
        return _finalize_tool_output("knowledge_search", "错误：检索查询不能为空", query=query, top_k=top_k)

    metadata_filter = {
        "asset_kind": asset_kind,
        "index_mode": index_mode,
        "folder_level_1": folder_level_1,
        "folder_level_2": folder_level_2,
        "folder_level_3": folder_level_3,
        "filename": filename,
    }
    metadata_filter = {k: str(v).strip() for k, v in metadata_filter.items() if str(v).strip()}
    
    retriever = _get_retriever()
    if retriever is None:
        return _finalize_tool_output(
            "knowledge_search",
            "知识库暂未启用。您可以：\n1. 提供具体文本内容让我学习\n2. 或者我直接基于已有知识回答您的问题",
            query=query,
            top_k=top_k,
        )
    
    try:
        session_id = _rag_cfg_var.get({}).get("session_id") or get_current_session_id()
        documents = retriever.search(
            query=query,
            k=top_k,
            filter=metadata_filter if metadata_filter else None,
        )

        if not documents:
            return _finalize_tool_output(
                "knowledge_search",
                f"知识库中暂未找到与 '{query}' 相关的内容。您可以：\n1. 提供相关资料让我学习\n2. 或者我直接基于已有知识回答您的问题",
                query=query,
                top_k=top_k,
            )

        context_text = "\n\n".join(
            f"[{i+1}] {doc.page_content}"
            for i, doc in enumerate(documents)
        )

        filter_text = ""
        if metadata_filter:
            filter_text = f"\n生效过滤条件: {json.dumps(metadata_filter, ensure_ascii=False)}\n"

        response = f"找到 {len(documents)} 条相关知识：{filter_text}\n{context_text}"

        return _finalize_tool_output(
            "knowledge_search",
            response,
            query=query,
            top_k=top_k,
            asset_kind=asset_kind,
            index_mode=index_mode,
            folder_level_1=folder_level_1,
            folder_level_2=folder_level_2,
            folder_level_3=folder_level_3,
            filename=filename,
        )
        
    except Exception as e:
        logger.error(f"知识检索失败: {e}")
        return _finalize_tool_output(
            "knowledge_search",
            f"知识检索遇到问题: {str(e)}。您可以提供相关资料，或让我直接基于已有知识回答。",
            query=query,
            top_k=top_k,
        )


knowledge_search = build_agent_tool(
    name="knowledge_search",
    description=(
        "从知识库中检索相关参考资料。"
        "用于查找专业知识、历史案例、技术文档等。"
    ),
    args_schema=KnowledgeSearchInput,
    func=_impl_knowledge_search,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


def _impl_add_knowledge(
    content: str = "",
    file_path: str = "",
    doc_name: str = "",
    force_method: Optional[str] = None,
) -> str:
    parsed = _parse_json_if_needed(content)
    if parsed and 'content' in parsed:
        content = parsed.get('content', content)
        file_path = parsed.get('file_path', file_path)
        doc_name = parsed.get('doc_name', doc_name)
        force_method = parsed.get('force_method', force_method)
    
    content = str(content).strip().strip('"').strip("'") if content else ""
    file_path = str(file_path).strip().strip('"').strip("'") if file_path else ""
    doc_name = str(doc_name).strip().strip('"').strip("'") if doc_name else ""
    
    if not content and not file_path:
        return _finalize_tool_output("add_knowledge", "错误：必须提供 content 或 file_path 参数", content=content, file_path=file_path, doc_name=doc_name)
    
    retriever = _get_retriever()
    if retriever is None:
        return _finalize_tool_output("add_knowledge", "RAG 功能未启用。请在配置中启用 RAG。", content=content, file_path=file_path, doc_name=doc_name)
    
    try:
        session_id = _rag_cfg_var.get({}).get("session_id") or get_current_session_id()
        
        metadata = {}
        if doc_name:
            metadata["doc_name"] = doc_name
        
        if file_path:
            resolved_file_path = resolve_tool_path(file_path, access="read").resolved
            ids = retriever.add_file(
                file_path=str(resolved_file_path),
                metadata=metadata,
            )
        else:
            ids = retriever.add_text(
                text=content,
                metadata=metadata,
            )

        if ids:
            return _finalize_tool_output(
                "add_knowledge",
                (
                f"文档添加成功！\n"
                f"- 处理方式: 向量库\n"
                f"- 分块数量: {len(ids)}\n"
                ),
                content=content,
                file_path=file_path,
                doc_name=doc_name,
            )
        else:
            return _finalize_tool_output(
                "add_knowledge",
                "文档添加失败：未生成任何分块",
                content=content,
                file_path=file_path,
                doc_name=doc_name,
            )
            
    except Exception as e:
        logger.error(f"添加知识失败: {e}")
        return _finalize_tool_output(
            "add_knowledge",
            f"添加知识失败: {str(e)}",
            content=content,
            file_path=file_path,
            doc_name=doc_name,
        )


add_knowledge = build_agent_tool(
    name="add_knowledge",
    description=(
        "将文档添加到知识库。"
        "小文档（<10KB）会作为临时上下文注入对话，"
        "大文档会存入向量库供后续检索。"
    ),
    args_schema=AddKnowledgeInput,
    func=_impl_add_knowledge,
    is_readonly=False,
    is_destructive=False,
    is_concurrency_safe=False,
    check_permissions_fn=make_read_path_permission_fn("file_path"),
    permission_policy=ToolPermissionPolicy(policy_type="state_write"),
)


class WebSearchInput(BaseModel):
    """网络搜索的输入参数"""
    query: str = Field(default="", description="搜索关键词")
    count: int = Field(default=10, description="返回结果数量 (1-50)")
    freshness: str = Field(default="py", description="时间范围筛选: pd(24小时), pw(7天), pm(31天), py(365天), 或 YYYY-MM-DDtoYYYY-MM-DD")
    search_types: str = Field(default="web", description="搜索类型: web, video, image (多个用逗号分隔)")
    site: str = Field(default="", description="指定站点搜索，如 baidu.com")


class FetchWebpageInput(BaseModel):
    """抓取网页正文的输入参数"""
    url: str = Field(default="", description="要抓取的网页 URL")
    max_chars: int = Field(default=12000, description="返回正文的最大字符数")
    include_links: bool = Field(default=False, description="是否附带页面中的部分链接")


def _impl_web_search(
    query: str = "",
    count: int = 10,
    freshness: str = "py",
    search_types: str = "web",
    site: str = "",
) -> str:
    parsed = _parse_json_if_needed(query)
    if parsed and 'query' in parsed:
        query = parsed.get('query', query)
        count = parsed.get('count', count)
        freshness = parsed.get('freshness', freshness)
        search_types = parsed.get('search_types', search_types)
        site = parsed.get('site', site)
    
    query = str(query).strip().strip('"').strip("'")
    
    if not query:
        return _finalize_tool_output("web_search", "错误：搜索关键词不能为空", query=query, count=count, freshness=freshness, search_types=search_types, site=site)
    
    api_key = os.getenv("BAIDU_API_KEY")
    if not api_key:
        return _finalize_tool_output(
            "web_search",
            "错误：未配置 BAIDU_API_KEY 环境变量，请在 .env 文件中配置",
            query=query,
            count=count,
            freshness=freshness,
            search_types=search_types,
            site=site,
        )
    
    try:
        import requests
        from datetime import datetime, timedelta
        import re
    except ImportError:
        return _finalize_tool_output(
            "web_search",
            "错误：缺少必要的依赖库 (requests)",
            query=query,
            count=count,
            freshness=freshness,
            search_types=search_types,
            site=site,
        )
    
    def parse_freshness(freshness_str: str, current_time: datetime) -> Optional[Dict[str, Any]]:
        if not freshness_str:
            return None
        
        pattern = r'\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2}'
        
        mapping = {
            "pd": 1,
            "pw": 6,
            "pm": 30,
            "py": 364
        }
        
        end_date = (current_time + timedelta(days=1)).strftime("%Y-%m-%d")
        
        if freshness_str in mapping:
            start_date = (current_time - timedelta(days=mapping[freshness_str])).strftime("%Y-%m-%d")
            return {"range": {"page_time": {"gte": start_date, "lt": end_date}}}
        elif re.match(pattern, freshness_str):
            start_date = freshness_str.split("to")[0]
            end_date = freshness_str.split("to")[1]
            return {"range": {"page_time": {"gte": start_date, "lt": end_date}}}
        else:
            return None
    
    def build_resource_type_filter() -> List[Dict[str, Any]]:
        resource_filter = []
        count_val = min(max(1, count), 50)
        types_list = [t.strip() for t in search_types.split(",")]
        
        for stype in types_list:
            if stype in ["web", "video", "image"]:
                resource_filter.append({"type": stype, "top_k": count_val})
        
        if not resource_filter:
            resource_filter = [{"type": "web", "top_k": count_val}]
        
        return resource_filter
    
    current_time = datetime.now()
    
    request_body = {
        "messages": [
            {
                "content": query,
                "role": "user"
            }
        ],
        "search_source": "baidu_search_v2",
    }
    
    resource_filter = build_resource_type_filter()
    request_body["resource_type_filter"] = resource_filter
    
    search_filter = {}
    
    if freshness:
        time_filter = parse_freshness(freshness, current_time)
        if time_filter:
            search_filter.update(time_filter)
    
    if site:
        search_filter.setdefault("match", {})["site"] = site
    
    if search_filter:
        request_body["search_filter"] = search_filter
    
    try:
        url = "https://qianfan.baidubce.com/v2/ai_search/web_search"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(url, json=request_body, headers=headers, timeout=30)
        response.raise_for_status()
        results = response.json()
        
        if "code" in results and results.get("code") != 200:
            return _finalize_tool_output(
                "web_search",
                f"搜索失败: {results.get('message', '未知错误')}",
                query=query,
                count=count,
                freshness=freshness,
                search_types=search_types,
                site=site,
            )
        
        datas = results.get("references", [])
        
        formatted_results = []
        for i, item in enumerate(datas, 1):
            result_item = {
                "id": i,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "date": item.get("page_time", ""),
                "content": item.get("content", "")[:500] if item.get("content") else "",
                "source": item.get("website", ""),
            }
            formatted_results.append(result_item)
        
        if not formatted_results:
            return _finalize_tool_output(
                "web_search",
                f"未找到与 '{query}' 相关的搜索结果",
                query=query,
                count=count,
                freshness=freshness,
                search_types=search_types,
                site=site,
            )

        return _finalize_tool_output(
            "web_search",
            json.dumps(formatted_results, ensure_ascii=False, indent=2),
            query=query,
            count=count,
            freshness=freshness,
            search_types=search_types,
            site=site,
        )
        
    except requests.exceptions.Timeout:
        return _finalize_tool_output("web_search", "搜索超时，请稍后重试", query=query, count=count, freshness=freshness, search_types=search_types, site=site)
    except requests.exceptions.RequestException as e:
        return _finalize_tool_output("web_search", f"搜索请求失败: {str(e)}", query=query, count=count, freshness=freshness, search_types=search_types, site=site)
    except Exception as e:
        logger.error(f"网络搜索失败: {e}")
        return _finalize_tool_output("web_search", f"搜索失败: {str(e)}", query=query, count=count, freshness=freshness, search_types=search_types, site=site)


web_search = build_agent_tool(
    name="web_search",
    description=(
        "网络搜索工具，用于获取实时网络信息。"
        "当用户需要搜索最新新闻、实时信息、网络资料时使用此工具。"
        "支持时间范围筛选和站点限定搜索。"
    ),
    args_schema=WebSearchInput,
    func=_impl_web_search,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="network"),
)


def _impl_fetch_webpage(
    url: str = "",
    max_chars: int = 12000,
    include_links: bool = False,
) -> str:
    parsed = _parse_json_if_needed(url)
    if parsed and 'url' in parsed:
        url = parsed.get('url', url)
        max_chars = parsed.get('max_chars', max_chars)
        include_links = parsed.get('include_links', include_links)

    url = str(url).strip().strip('"').strip("'")
    if not url:
        return _finalize_tool_output("fetch_webpage", "错误：url 参数不能为空", url=url, max_chars=max_chars, include_links=include_links)

    if not re.match(r'^https?://', url, re.IGNORECASE):
        url = f"https://{url}"

    try:
        max_chars = max(1000, min(int(max_chars), 50000))
    except Exception:
        max_chars = 12000

    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as e:
        return _finalize_tool_output(
            "fetch_webpage",
            f"错误：缺少必要依赖 ({e})",
            url=url,
            max_chars=max_chars,
            include_links=include_links,
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = response.encoding or response.apparent_encoding or 'utf-8'
        html = response.text

        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg']):
            tag.decompose()

        title = ''
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        main_node = None
        for selector in ['main', 'article', '[role="main"]', '.article', '.content', '.post', '.entry-content']:
            main_node = soup.select_one(selector)
            if main_node:
                break
        if main_node is None:
            main_node = soup.body or soup

        lines: List[str] = []
        for el in main_node.find_all(['h1', 'h2', 'h3', 'p', 'li']):
            text = el.get_text(' ', strip=True)
            if not text:
                continue
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) < 2:
                continue
            lines.append(text)

        content = '\n'.join(lines)
        if not content:
            content = re.sub(r'\s+', ' ', main_node.get_text(' ', strip=True)).strip()

        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars] + "\n...[内容已截断]"
            truncated = True

        result: Dict[str, Any] = {
            "url": url,
            "title": title,
            "status_code": response.status_code,
            "content": content,
            "truncated": truncated,
        }

        if include_links:
            links: List[Dict[str, str]] = []
            for a in main_node.find_all('a', href=True):
                text = re.sub(r'\s+', ' ', a.get_text(' ', strip=True)).strip()
                href = a.get('href', '').strip()
                if not href:
                    continue
                if len(links) >= 20:
                    break
                links.append({"text": text[:120], "href": href[:500]})
            result["links"] = links

        return _finalize_tool_output(
            "fetch_webpage",
            json.dumps(result, ensure_ascii=False, indent=2),
            url=url,
            max_chars=max_chars,
            include_links=include_links,
        )
    except requests.exceptions.Timeout:
        return _finalize_tool_output("fetch_webpage", "抓取网页超时，请稍后重试", url=url, max_chars=max_chars, include_links=include_links)
    except requests.exceptions.RequestException as e:
        return _finalize_tool_output("fetch_webpage", f"抓取网页失败: {str(e)}", url=url, max_chars=max_chars, include_links=include_links)
    except Exception as e:
        logger.error("抓取网页失败: %s", e, exc_info=True)
        return _finalize_tool_output("fetch_webpage", f"抓取网页失败: {str(e)}", url=url, max_chars=max_chars, include_links=include_links)


fetch_webpage = build_agent_tool(
    name="fetch_webpage",
    description=(
        "抓取指定网页 URL 的正文内容。"
        "适用于先通过 web_search 找到候选链接，再进入具体网页抽取标题、正文和部分链接。"
        "当搜索摘要不够详细时，优先使用此工具读取目标页面。"
    ),
    args_schema=FetchWebpageInput,
    func=_impl_fetch_webpage,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="network"),
)


_memory_var: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "memory_instance",
    default=None,
)


def set_memory_instance(memory: Any):
    """设置记忆系统实例（由 flood_agent 调用）"""
    _memory_var.set(memory)
    logger.info("记忆系统实例已设置")


def get_memory_instance() -> Optional[Any]:
    return _memory_var.get()


class AddMemoryInput(BaseModel):
    """添加长期记忆的输入参数"""
    content: str = Field(default="", description="要记录的内容")
    entry_type: str = Field(default="note", description="记忆类型: note(备注), preference(偏好), decision(决策), rule(规则)")


def _impl_add_memory(content: str = "", entry_type: str = "note") -> str:
    parsed = _parse_json_if_needed(content)
    if parsed and 'content' in parsed:
        content = parsed.get('content', content)
        entry_type = parsed.get('entry_type', entry_type)
    
    content = str(content).strip().strip('"').strip("'")
    entry_type = str(entry_type).strip().strip('"').strip("'")
    
    if not content:
        return _finalize_tool_output("add_memory", "错误：记录内容不能为空", content=content, entry_type=entry_type)
    
    valid_types = {"note", "preference", "decision", "rule"}
    if entry_type not in valid_types:
        entry_type = "note"
    
    memory_instance = get_memory_instance()
    if memory_instance is None:
        return _finalize_tool_output("add_memory", "错误：记忆系统未初始化", content=content, entry_type=entry_type)
    
    try:
        if hasattr(memory_instance, 'add_long_term_memory'):
            success = memory_instance.add_long_term_memory(content, entry_type)
            if success:
                return _finalize_tool_output("add_memory", f"已记录到长期记忆：{content}", content=content, entry_type=entry_type)
            else:
                return _finalize_tool_output("add_memory", "该内容已存在于长期记忆中", content=content, entry_type=entry_type)
        else:
            return _finalize_tool_output("add_memory", "错误：记忆系统不支持此操作", content=content, entry_type=entry_type)
    except Exception as e:
        logger.error(f"添加长期记忆失败: {e}")
        return _finalize_tool_output("add_memory", f"添加长期记忆失败: {str(e)}", content=content, entry_type=entry_type)


add_memory = build_agent_tool(
    name="add_memory",
    description=(
        "将重要内容添加到长期记忆。"
        "当用户明确要求记住某事，或识别到重要的决策、偏好、规则时使用此工具。"
        "长期记忆会在后续对话中持续保留。"
    ),
    args_schema=AddMemoryInput,
    func=_impl_add_memory,
    is_readonly=False,
    is_destructive=False,
    is_concurrency_safe=False,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="state_write"),
)


class SearchMemoryInput(BaseModel):
    """搜索记忆的输入参数"""
    keywords: Union[str, List[str]] = Field(default="", description="搜索关键词或正则表达式")
    search_type: str = Field(default="conversation", description="搜索类型: conversation=只搜索对话历史, global=搜索对话历史+Skills")
    max_results: int = Field(default=5, description="最大返回结果数")


def _impl_search_memory(
    keywords: Union[str, List[str]] = "",
    search_type: str = "conversation",
    max_results: int = 5,
) -> str:
    parsed = _parse_json_if_needed(keywords)
    if parsed and isinstance(parsed, (str, list)):
        keywords = parsed

    if isinstance(keywords, str):
        keywords = keywords.strip().strip('"').strip("'")
    elif isinstance(keywords, list):
        keywords = [str(k).strip().strip('"').strip("'") for k in keywords]

    if not keywords:
        return _finalize_tool_output("search_memory", "错误：搜索关键词不能为空", keywords=keywords, search_type=search_type, max_results=max_results)

    memory_instance = get_memory_instance()
    if memory_instance is None:
        return _finalize_tool_output("search_memory", "错误：记忆系统未初始化", keywords=keywords, search_type=search_type, max_results=max_results)

    try:
        if not hasattr(memory_instance, 'search_history'):
            return _finalize_tool_output("search_memory", "错误：记忆系统不支持搜索功能", keywords=keywords, search_type=search_type, max_results=max_results)

        if search_type == "global":
            results = memory_instance.global_search(keywords, max_results)
        else:
            results = memory_instance.search_history(keywords, max_results)

        if not results or "未找到" in results:
            return _finalize_tool_output("search_memory", f"未找到与 '{keywords}' 相关的内容", keywords=keywords, search_type=search_type, max_results=max_results)

        return _finalize_tool_output("search_memory", results, keywords=keywords, search_type=search_type, max_results=max_results)

    except Exception as e:
        logger.error(f"搜索记忆失败: {e}")
        return _finalize_tool_output("search_memory", f"搜索记忆失败: {str(e)}", keywords=keywords, search_type=search_type, max_results=max_results)


search_memory = build_agent_tool(
    name="search_memory",
    description=(
        "在记忆系统中搜索内容。"
        "当需要查找之前对话中的具体内容、或搜索Skills文档中的信息时使用此工具。"
    ),
    args_schema=SearchMemoryInput,
    func=_impl_search_memory,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
)


def _backup_agents_md(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        return True
    except Exception as e:
        logger.warning(f"备份 {path} 失败: {e}")
        return False


def _parse_agents_md_sections(content: str) -> List[Dict[str, Any]]:
    sections = []
    current_title = ""
    current_lines: List[str] = []
    for line in content.splitlines():
        if line.startswith("## "):
            if current_title or current_lines:
                sections.append({"title": current_title, "lines": current_lines[:], "start_h2": True})
            current_title = line[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_title or current_lines:
        sections.append({"title": current_title, "lines": current_lines[:], "start_h2": True})
    return sections


def _rebuild_agents_md(sections: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for i, sec in enumerate(sections):
        body = "\n".join(sec["lines"]).strip()
        if body:
            parts.append(body)
    return "\n\n".join(parts) + "\n"


def _impl_update_project_instructions(
    action: str = "append",
    content: str = "",
    section_title: str = "",
    scope: str = "project",
) -> str:
    from tools.agent_tool import get_agents_md_path

    parsed = _parse_json_if_needed(action)
    if parsed and isinstance(parsed, dict):
        action = parsed.get("action", action)
        content = parsed.get("content", content)
        section_title = parsed.get("section_title", section_title)
        scope = parsed.get("scope", scope)

    action = str(action).strip().lower()
    scope = str(scope).strip().lower()
    content = str(content).strip()
    section_title = str(section_title).strip()

    if action not in ("append", "replace_section", "remove_section"):
        return _finalize_tool_output(
            "update_project_instructions",
            "错误：action 仅支持 append、replace_section、remove_section",
            action=action, scope=scope,
        )

    if scope not in ("project", "global"):
        return _finalize_tool_output(
            "update_project_instructions",
            "错误：scope 仅支持 project 或 global",
            action=action, scope=scope,
        )

    if action in ("replace_section", "remove_section") and not section_title:
        return _finalize_tool_output(
            "update_project_instructions",
            "错误：replace_section 和 remove_section 需要提供 section_title",
            action=action, scope=scope,
        )

    if action in ("append", "replace_section") and not content:
        return _finalize_tool_output(
            "update_project_instructions",
            "错误：append 和 replace_section 需要提供 content",
            action=action, scope=scope,
        )

    target_path = get_agents_md_path(scope)

    if not _backup_agents_md(target_path):
        return _finalize_tool_output(
            "update_project_instructions",
            f"备份失败，中止写入: {target_path}",
            action=action, scope=scope,
        )

    existing = ""
    if target_path.exists():
        try:
            existing = target_path.read_text(encoding="utf-8")
        except Exception as e:
            return _finalize_tool_output(
                "update_project_instructions",
                f"读取 {target_path} 失败: {e}",
                action=action, scope=scope,
            )

    if action == "append":
        new_section = f"\n## 用户偏好\n\n{content}\n"
        sections = _parse_agents_md_sections(existing)
        user_pref_idx = None
        for i, sec in enumerate(sections):
            if sec["title"].strip() == "用户偏好":
                user_pref_idx = i
                break

        if user_pref_idx is not None:
            body_lines = sections[user_pref_idx]["lines"]
            last_line = body_lines[-1] if body_lines else ""
            if not last_line.endswith("\n"):
                body_lines.append("")
            body_lines.append(content)
            body_lines.append("")
        else:
            sections.append({"title": "用户偏好", "lines": [f"## 用户偏好", "", content, ""], "start_h2": True})

        new_content = _rebuild_agents_md(sections)

    elif action == "replace_section":
        sections = _parse_agents_md_sections(existing)
        found = False
        for i, sec in enumerate(sections):
            if sec["title"].strip() == section_title:
                sections[i] = {"title": section_title, "lines": [f"## {section_title}", "", content, ""], "start_h2": True}
                found = True
                break
        if not found:
            sections.append({"title": section_title, "lines": [f"## {section_title}", "", content, ""], "start_h2": True})
        new_content = _rebuild_agents_md(sections)

    elif action == "remove_section":
        sections = _parse_agents_md_sections(existing)
        original_count = len(sections)
        sections = [s for s in sections if s["title"].strip() != section_title]
        if len(sections) == original_count:
            return _finalize_tool_output(
                "update_project_instructions",
                f"未找到章节 '{section_title}'，文件未修改",
                action=action, scope=scope, section_title=section_title,
            )
        new_content = _rebuild_agents_md(sections)

    else:
        return _finalize_tool_output(
            "update_project_instructions",
            f"未知操作: {action}",
            action=action, scope=scope,
        )

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_content, encoding="utf-8")
        scope_label = "全局" if scope == "global" else "项目"
        return _finalize_tool_output(
            "update_project_instructions",
            f"已更新{scope_label}级指令文件: {target_path}\n操作: {action}\n请告知用户：此偏好将在后续所有对话中生效。",
            action=action, scope=scope, section_title=section_title,
        )
    except Exception as e:
        logger.error(f"写入 {target_path} 失败: {e}", exc_info=True)
        bak = target_path.with_suffix(target_path.suffix + ".bak")
        if bak.exists():
            try:
                shutil.copy2(bak, target_path)
                return _finalize_tool_output(
                    "update_project_instructions",
                    f"写入失败已自动回滚: {e}",
                    action=action, scope=scope,
                )
            except Exception:
                pass
        return _finalize_tool_output(
            "update_project_instructions",
            f"写入失败: {e}（备份文件: {bak}）",
            action=action, scope=scope,
        )


update_project_instructions = build_agent_tool(
    name="update_project_instructions",
    description=(
        "修改项目级或全局 AGENTS.md 指令文件，用于持久化用户偏好和规则。"
        "写入前会自动备份原文件。此工具影响所有后续对话，请务必先向用户确认。"
    ),
    args_schema=UpdateProjectInstructionsInput,
    func=_impl_update_project_instructions,
    is_readonly=False,
    is_destructive=True,
    is_concurrency_safe=False,
    check_permissions_fn=make_ask_permission_fn("修改 AGENTS.md 指令文件会影响所有后续对话，需要用户确认"),
)


def _impl_create_scheduled_task(
    command: str = "",
    repeat: str = "none",
    run_time: str = "",
    scheduled_at: str = "",
    timezone: str = "Asia/Shanghai",
    enabled: bool = True,
) -> str:
    try:
        from agent.scheduled_task_runtime import get_scheduled_task_runtime

        session_id = get_current_session_id() or "default"
        task = get_scheduled_task_runtime().create_task(
            session_id=session_id,
            command=command,
            repeat=repeat,
            run_time=run_time,
            scheduled_at=scheduled_at,
            timezone=timezone,
            enabled=enabled,
        )
        payload = {
            "message": "定时任务已创建",
            "task": task,
        }
        return _finalize_tool_output(
            "create_scheduled_task",
            json.dumps(payload, ensure_ascii=False, indent=2),
            command=command,
            repeat=repeat,
            run_time=run_time,
            scheduled_at=scheduled_at,
        )
    except Exception as e:
        logger.error(f"创建定时任务失败: {e}", exc_info=True)
        return _finalize_tool_output(
            "create_scheduled_task",
            f"创建定时任务失败: {e}",
            command=command,
            repeat=repeat,
            run_time=run_time,
            scheduled_at=scheduled_at,
        )


create_scheduled_task = build_agent_tool(
    name="create_scheduled_task",
    description=(
        "创建后台定时任务。当用户要求在未来某个时间、每天、定时、自动执行某项任务时使用。"
        "command 只能填写未来真正要执行的业务任务，不要包含'每天/定时/明天几点'等调度表达；"
        "每日任务使用 repeat=daily 和 run_time=HH:MM，一次性任务使用 repeat=none 和 scheduled_at。"
    ),
    args_schema=CreateScheduledTaskInput,
    func=_impl_create_scheduled_task,
    is_readonly=False,
    is_destructive=False,
    is_concurrency_safe=False,
    check_permissions_fn=make_readonly_permission_fn(),
)


def _impl_list_scheduled_tasks(include_all_sessions: bool = True) -> str:
    try:
        from agent.scheduled_task_runtime import get_scheduled_task_runtime

        session_id = "" if include_all_sessions else (get_current_session_id() or "default")
        tasks = get_scheduled_task_runtime().list_tasks(session_id=session_id)
        payload = {
            "message": "已查询定时任务",
            "count": len(tasks),
            "tasks": tasks,
        }
        return _finalize_tool_output(
            "list_scheduled_tasks",
            json.dumps(payload, ensure_ascii=False, indent=2),
            include_all_sessions=include_all_sessions,
        )
    except Exception as e:
        logger.error(f"查询定时任务失败: {e}", exc_info=True)
        return _finalize_tool_output(
            "list_scheduled_tasks",
            f"查询定时任务失败: {e}",
            include_all_sessions=include_all_sessions,
        )


list_scheduled_tasks = build_agent_tool(
    name="list_scheduled_tasks",
    description="查询定时任务列表。默认查询所有会话的定时任务。",
    args_schema=ListScheduledTasksInput,
    func=_impl_list_scheduled_tasks,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
)


def _impl_cancel_scheduled_task(task_id: str = "") -> str:
    try:
        from agent.scheduled_task_runtime import get_scheduled_task_runtime

        if not str(task_id or "").strip():
            return _finalize_tool_output("cancel_scheduled_task", "取消定时任务失败: task_id 不能为空", task_id=task_id)
        task = get_scheduled_task_runtime().cancel_task(task_id)
        payload = {
            "message": "定时任务已取消",
            "task": task,
        }
        return _finalize_tool_output(
            "cancel_scheduled_task",
            json.dumps(payload, ensure_ascii=False, indent=2),
            task_id=task_id,
        )
    except Exception as e:
        logger.error(f"取消定时任务失败: {e}", exc_info=True)
        return _finalize_tool_output("cancel_scheduled_task", f"取消定时任务失败: {e}", task_id=task_id)


cancel_scheduled_task = build_agent_tool(
    name="cancel_scheduled_task",
    description="取消或停用一个后台定时任务。用户要求取消、停用定时任务时使用。",
    args_schema=CancelScheduledTaskInput,
    func=_impl_cancel_scheduled_task,
    is_readonly=False,
    is_destructive=False,
    is_concurrency_safe=False,
    check_permissions_fn=make_readonly_permission_fn(),
)


def _register_all_tools():
    ToolRegistry.clear()
    ToolRegistry.register(get_skill)
    ToolRegistry.register(run_script)
    ToolRegistry.register(search_tool_error_memory)
    ToolRegistry.register(exec_bash)
    ToolRegistry.register(exec_python_file)
    ToolRegistry.register(write_text_file)
    ToolRegistry.register(search_artifacts)
    ToolRegistry.register(check_artifact_exists)
    ToolRegistry.register(read_artifact)
    ToolRegistry.register(knowledge_search)
    ToolRegistry.register(add_knowledge)
    ToolRegistry.register(web_search)
    ToolRegistry.register(fetch_webpage)
    ToolRegistry.register(add_memory)
    ToolRegistry.register(search_memory)
    ToolRegistry.register(update_project_instructions)
    ToolRegistry.register(create_scheduled_task)
    ToolRegistry.register(list_scheduled_tasks)
    ToolRegistry.register(cancel_scheduled_task)

    # ── 任务经验工具 ──────────────────────────────────────────
    from config.settings import settings as _settings
    if _settings.task_experience.enabled:
        from memory.task_experience import get_task_experience_store
        from memory.experience_tree import ExperienceLeaf

        class _SearchTaskExperienceInput(BaseModel):
            query: str = Field(default="", description="搜索查询，描述要查找的任务经验")
            path: str = Field(default="", description="可选树路径过滤，如 '水文预报/敖江流域'")
            top_k: int = Field(default=5, description="返回结果数量")

        def _search_task_experience(query: str = "", path: str = "", top_k: int = 5) -> str:
            try:
                store = get_task_experience_store()
                if not store.has_experiences():
                    return "当前没有积累的任务执行经验。随着任务执行，经验会自动积累。"
                leaves = store.search_keywords(query, path_filter=path, top_k=top_k)
                store.bump_hotness(query, leaves)
                return store.render_experience_markdown(leaves)
            except Exception as e:
                return f"检索任务经验时出错: {e}"

        ToolRegistry.register(build_agent_tool(
            name="search_task_experience",
            description="检索历史任务执行经验，避免重复踩坑。可指定树路径缩小检索范围。搜索命中会自动提升经验热度。",
            args_schema=_SearchTaskExperienceInput,
            func=_search_task_experience,
            is_readonly=True, is_destructive=False, is_concurrency_safe=True,
            check_permissions_fn=make_readonly_permission_fn(),
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
        ))

        class _BrowseExperienceTreeInput(BaseModel):
            path: str = Field(default="", description="可选路径过滤，如 '水文预报'，留空浏览整棵树")

        def _browse_experience_tree(path: str = "") -> str:
            try:
                store = get_task_experience_store()
                return store.browse_tree(path)
            except Exception as e:
                return f"浏览经验树时出错: {e}"

        ToolRegistry.register(build_agent_tool(
            name="browse_experience_tree",
            description="按路径浏览经验树结构，查看摘要概览。不返回叶子详情，需要详情时使用 drill_down_experience。",
            args_schema=_BrowseExperienceTreeInput,
            func=_browse_experience_tree,
            is_readonly=True, is_destructive=False, is_concurrency_safe=True,
            check_permissions_fn=make_readonly_permission_fn(),
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
        ))

        class _DrillDownExperienceInput(BaseModel):
            summary_node_id: str = Field(default="", description="摘要节点 ID 或路径 key（如 '水文预报/敖江流域预报'）")

        def _drill_down_experience(summary_node_id: str = "") -> str:
            try:
                store = get_task_experience_store()
                return store.drill_down(summary_node_id)
            except Exception as e:
                return f"下钻经验详情时出错: {e}"

        ToolRegistry.register(build_agent_tool(
            name="drill_down_experience",
            description="从摘要节点展开到叶子详情，查看具体的坑点、解决方案和步骤。",
            args_schema=_DrillDownExperienceInput,
            func=_drill_down_experience,
            is_readonly=True, is_destructive=False, is_concurrency_safe=True,
            check_permissions_fn=make_readonly_permission_fn(),
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
        ))

        class _AddTaskExperienceInput(BaseModel):
            path: str = Field(default="", description="经验树路径，如 '水文预报/敖江流域预报/霍口水库预报'")
            description: str = Field(default="", description="任务描述")
            pitfalls: str = Field(default="", description="遇到的坑点，分号分隔")
            solutions: str = Field(default="", description="解决方案，分号分隔")
            steps_summary: str = Field(default="", description="关键步骤摘要")
            code_snippets: str = Field(default="", description="可复用的代码片段，分号分隔")
            outcome: str = Field(default="success", description="最终结果: success/partial/failed")

        def _add_task_experience(
            path: str = "", description: str = "", pitfalls: str = "",
            solutions: str = "", steps_summary: str = "",
            code_snippets: str = "", outcome: str = "success",
        ) -> str:
            try:
                path_parts = [p.strip() for p in path.split("/") if p.strip()]
                if not path_parts:
                    return "路径不能为空"
                pitfalls_list = [p.strip() for p in pitfalls.split(";") if p.strip()] if pitfalls else []
                solutions_list = [s.strip() for s in solutions.split(";") if s.strip()] if solutions else []
                code_list = [c.strip() for c in code_snippets.split(";") if c.strip()] if code_snippets else []
                leaf = ExperienceLeaf(
                    node_id="", experience_id="",
                    path=path_parts + [description[:30]], label=description[:30],
                    node_type="case", task_description=description,
                    domain_keywords=[], skill_used="", steps_summary=steps_summary,
                    pitfalls=pitfalls_list, solutions=solutions_list,
                    code_snippets=code_list, final_outcome=outcome,
                    session_id="manual", created_at=datetime.now().isoformat(),
                    importance=0.7 if pitfalls_list else 0.4,
                )
                store = get_task_experience_store()
                store.record_experience(leaf, path_parts)
                return f"经验已添加到: {'/'.join(path_parts)}\n描述: {description}\n坑点: {len(pitfalls_list)}个\n解决方案: {len(solutions_list)}个\n代码片段: {len(code_list)}个"
            except Exception as e:
                return f"添加任务经验时出错: {e}"

        ToolRegistry.register(build_agent_tool(
            name="add_task_experience",
            description="手动添加任务执行经验到经验树，供未来类似任务参考。包括坑点、解决方案、步骤摘要和可复用代码片段。",
            args_schema=_AddTaskExperienceInput,
            func=_add_task_experience,
            is_readonly=False, is_destructive=False, is_concurrency_safe=False,
            check_permissions_fn=make_write_permission_fn(),
            permission_policy=ToolPermissionPolicy(policy_type="state_write"),
        ))


_register_all_tools()


__all__ = [
    'get_skill',
    'run_script',
    'exec_bash',
    'exec_python_file',
    'write_text_file',
    'search_artifacts',
    'check_artifact_exists',
    'read_artifact',
    'knowledge_search',
    'add_knowledge',
    'web_search',
    'fetch_webpage',
    'add_memory',
    'search_memory',
    'search_tool_error_memory',
    'update_project_instructions',
    'create_scheduled_task',
    'list_scheduled_tasks',
    'cancel_scheduled_task',
    'reset_retry_guard',
    'set_skill_registry',
    'set_rag_config',
    'set_memory_instance',
    'set_session_context',
    'get_current_session_output_dir',
    '_register_all_tools',
]
