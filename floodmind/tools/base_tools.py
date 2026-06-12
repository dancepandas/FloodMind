"""
基础工具模块

提供Agent执行技能所需的核心工具，所有工具统一使用 build_agent_tool 构建，
具备完整的行为元数据（readonly/destructive/concurrency_safe/interrupt_behavior）。

工具分类：
- 只读工具: GetSkill, MemorySearch, WebSearch, WebFetch
- 写入工具: UpdateProjectInstructions, MemoryAdd
- 执行工具: Bash
- 网络工具: WebSearch, WebFetch
- 调度工具: CreateScheduledTask, ListScheduledTasks, CancelScheduledTask
"""

import os
import sys
import json
import logging
import re
import shutil
import subprocess
import threading
import contextvars
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Union

from pydantic import BaseModel, Field

from floodmind.tools.agent_tool import (
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
from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
from floodmind.tools.session_context import (
    SESSION_CONTEXT,
    set_session_context,
    get_current_session_output_dir,
    get_current_session_id,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path.cwd()
_RETRY_GUARD_LOCK = threading.Lock()
_RETRY_GUARD_STATES: Dict[str, dict] = {}


def _get_retry_guard_state() -> dict:
    session_id = SESSION_CONTEXT.get("session_id", "") or "default"
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
    output_dir = SESSION_CONTEXT.get("output_dir")
    session_id = SESSION_CONTEXT.get("session_id")
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
    skill_name: str = Field(description="[必填] 技能名称，如 'aojiang-hydro'、'docx'")



class ExecBashInput(BaseModel):
    """执行 Bash 命令的输入参数"""
    command: str = Field(description="[必填] 要执行的 shell 命令（不要嵌套 powershell/bash 前缀）")
    workdir: str = Field(default="", description="[可选] 工作目录（绝对路径），默认当前会话输出目录")
    timeout: int = Field(default=120, description="[可选] 超时时间（秒），默认 120")
    env: str = Field(default="{}", description="[可选] 额外环境变量，JSON 对象格式")


def _strip_session_prefix(path_str: str) -> str:
    from floodmind.tools.agent_tool import _strip_session_prefix as _agent_strip
    return _agent_strip(path_str)


def _resolve_path(path_str: str, *, access: str = "read") -> Path:
    return resolve_tool_path(path_str, access=access).resolved


class CreateScheduledTaskInput(BaseModel):
    """创建定时任务的输入参数"""
    command: str = Field(description="[必填] 未来到点后交给Agent执行的自然语言任务，不要包含定时表达")
    repeat: str = Field(default="none", description="[可选] 重复规则：none（一次性）或 daily（每日），默认 none")
    run_time: str = Field(default="", description="[可选] 每日任务执行时间，格式 HH:MM")
    scheduled_at: str = Field(default="", description="[可选] 一次性任务执行时间，ISO格式或 YYYY-MM-DD HH:MM:SS")
    timezone: str = Field(default="Asia/Shanghai", description="[可选] 时区标识，默认 Asia/Shanghai")
    enabled: bool = Field(default=True, description="[可选] 是否启用任务，默认 True")


class ListScheduledTasksInput(BaseModel):
    """查询定时任务的输入参数"""
    include_all_sessions: bool = Field(default=False, description="是否查询所有会话任务，默认只查当前会话")


class CancelScheduledTaskInput(BaseModel):
    """取消定时任务的输入参数"""
    task_id: str = Field(description="[必填] 要取消的定时任务 ID")


_SKILL_REGISTRY: List[Any] = []
_SESSION_ROOT = _PROJECT_ROOT / "data" / "sessions"
_REUSABLE_SCRIPT_EXTENSIONS = {".py"}


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





def _impl_get_skill(skill_name: str = "") -> str:
    parsed = _parse_json_if_needed(skill_name)
    if parsed:
        skill_name = parsed.get('skill_name', skill_name)
    
    skill_name = str(skill_name).strip().strip('"').strip("'")
    return _get_skill_cached(skill_name)


get_skill = build_agent_tool(
    name="GetSkill",
    description=(
        "获取技能的完整说明和执行方法。[必填] skill_name: 技能名称，如 'aojiang-hydro'、'docx'。"
        "返回内容包含：技能描述、使用说明、可用脚本（含完整路径）、参考文档。"
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
    ]

    if skill.compatibility:
        lines.extend(["", "【依赖环境】", skill.compatibility])

    lines.extend(["", "【使用说明】", skill.prompt])

    if skill.scripts:
        lines.extend([
            "",
            "【可执行脚本】",
            "使用 Bash 工具执行以下脚本：",
        ])
        for script in skill.scripts:
            if skill.skill_dir:
                script_full = str(skill.skill_dir / "scripts" / script)
            else:
                script_full = script
            lines.append(f"  - {script}  (完整路径: {script_full})")
        lines.append("")
        lines.append("示例：")
        if skill.scripts:
            first_script = skill.scripts[0]
            if skill.skill_dir:
                first_path = str(skill.skill_dir / "scripts" / first_script)
            else:
                first_path = first_script
            lines.append(f"  Bash(command=\"python {first_path} --arg1 value1\")")

    if skill.references:
        lines.extend([
            "",
            "【参考文档】",
            "使用 Read 工具读取以下文档：",
        ])
        for ref in skill.references:
            if skill.skill_dir:
                full_path = str(skill.skill_dir / ref)
            else:
                full_path = ref
            lines.append(f"  - {ref}  (完整路径: {full_path})")

    if skill.assets:
        lines.extend([
            "",
            "【资源文件】",
        ])
        for asset in skill.assets:
            if skill.skill_dir:
                full_path = str(skill.skill_dir / asset)
            else:
                full_path = asset
            lines.append(f"  - {asset}  (完整路径: {full_path})")

    if skill.is_knowledge_only:
        lines.extend([
            "",
            "【说明】",
            "这是知识型技能，提供专业知识和指导。",
            "请根据上述说明直接回答用户问题，无需执行脚本。",
        ])

    return _finalize_tool_output("get_skill", "\n".join(lines), skill_name=skill_name)






_DANGEROUS_COMMAND_PATTERNS = [
    re.compile(r'\brm\s+-rf\b', re.IGNORECASE),
    re.compile(r'\brm\s+-r\b', re.IGNORECASE),
    re.compile(r'\brmdir\s+/[sS]', re.IGNORECASE),
    re.compile(r'\bdel\s+/[sS]', re.IGNORECASE),
    re.compile(r'\bdel\s+/[fF]', re.IGNORECASE),
    re.compile(r'\bdel\s+/[qQ]', re.IGNORECASE),
    re.compile(r'\bformat\s+[A-Za-z]:', re.IGNORECASE),
    re.compile(r'\bshred\b', re.IGNORECASE),
    re.compile(r'\bdd\s+if=', re.IGNORECASE),
    re.compile(r'\bmkfs\b', re.IGNORECASE),
    re.compile(r'>\s*/dev/sd', re.IGNORECASE),
    re.compile(r'\bchmod\s+-R\s+777\b', re.IGNORECASE),
    re.compile(r'\bchown\s+-R\b', re.IGNORECASE),
    re.compile(r'\bgit\s+push\s+--force\b', re.IGNORECASE),
    re.compile(r'\bgit\s+reset\s+--hard\b', re.IGNORECASE),
    re.compile(r'\bdocker\s+system\s+prune', re.IGNORECASE),
    re.compile(r'\bdocker\s+rm\s+-f\b', re.IGNORECASE),
    re.compile(r'\bRemove-Item\s+.*-Recurse', re.IGNORECASE),
    re.compile(r'\bRemove-Item\s+.*-Force', re.IGNORECASE),
    re.compile(r'\brd\s+/[sS]', re.IGNORECASE),
    re.compile(r'\brd\s+/[qQ]', re.IGNORECASE),
    re.compile(r'\bnet\s+user\b', re.IGNORECASE),
    re.compile(r'\bnet\s+localgroup\b', re.IGNORECASE),
    re.compile(r'\bpip\s+uninstall\b', re.IGNORECASE),
    re.compile(r'\bconda\s+remove\b', re.IGNORECASE),
    re.compile(r'\bnpm\s+uninstall\b', re.IGNORECASE),
    re.compile(r'\btaskkill\s+/[fF]', re.IGNORECASE),
    re.compile(r'\breg\s+delete\b', re.IGNORECASE),
    re.compile(r'\bregedit\b', re.IGNORECASE),
    re.compile(r'\bmsiexec\b', re.IGNORECASE),
    re.compile(r'\bcertutil\b', re.IGNORECASE),
    re.compile(r'\bpowershell\s+-enc', re.IGNORECASE),
    re.compile(r'\bpwsh\s+-enc', re.IGNORECASE),
    re.compile(r'\bcmd\s+/c\s+del\b', re.IGNORECASE),
    re.compile(r'\bicacls\b', re.IGNORECASE),
    re.compile(r'\bcacls\b', re.IGNORECASE),
    re.compile(r'\bwbadmin\b', re.IGNORECASE),
    re.compile(r'\bdiskpart\b', re.IGNORECASE),
]


def _check_dangerous_command(command: str) -> str:
    for pattern in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            return f"检测到危险命令模式，已拦截: {pattern.pattern}"
    return ""


def _impl_exec_bash(command: str = "", workdir: str = "", timeout: int = 120, env: str = "{}") -> str:
    parsed = _parse_json_if_needed(command)
    if parsed:
        command = parsed.get('command', command)
        workdir = parsed.get('workdir', workdir)
        timeout = parsed.get('timeout', timeout)
        env = parsed.get('env', env)

    command = str(command).strip()

    _retry_block = _check_retry_guard_before_exec("exec_bash", command=command, timeout=timeout)
    if _retry_block:
        return _retry_block

    if not command:
        return _finalize_tool_output("exec_bash", "错误：命令不能为空", command=command, timeout=timeout)

    _danger = _check_dangerous_command(command)
    if _danger:
        return _finalize_tool_output("exec_bash", _danger, command=command, timeout=timeout)

    normalized_command = command.lower()
    if normalized_command.startswith("powershell ") or normalized_command.startswith("powershell.exe ") or normalized_command.startswith("pwsh ") or normalized_command.startswith("pwsh.exe ") or normalized_command.startswith("bash ") or normalized_command.startswith("sh "):
        return _finalize_tool_output(
            "exec_bash",
            "错误：`exec_bash` 已经在内部自动选择 shell 执行命令。不要再在 command 中嵌套 `powershell -Command`、`pwsh -Command`、`bash -lc` 或 `sh -lc`；请直接传入命令语句本体。",
            command=command,
            timeout=timeout,
        )

    try:
        env_dict = json.loads(env) if env else {}
    except json.JSONDecodeError:
        env_dict = {}

    try:
        logger.info(f"执行命令: {command}")
        _FORBIDDEN_ENV_KEYS = {"PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "BASH_ENV", "ENV", "HOME", "USERPROFILE",
                               "PATHEXT", "COMSPEC", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP",
                               "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"}
        run_env = _build_exec_env()
        for k, v in env_dict.items():
            if str(k).upper() in _FORBIDDEN_ENV_KEYS:
                return _finalize_tool_output("exec_bash", f"错误：禁止覆盖环境变量: {k}", command=command, timeout=timeout)
            run_env[str(k)] = str(v)
        Path(run_env['MPLCONFIGDIR']).mkdir(parents=True, exist_ok=True)
        shell_prefix, shell_name = _detect_shell_command()
        shell_cmd = shell_prefix + [command]

        if workdir and workdir.strip():
            cwd = resolve_tool_path(workdir.strip(), access="read").resolved
            if not cwd.is_dir():
                cwd = Path(SESSION_CONTEXT.get("output_dir", str(_PROJECT_ROOT)))
        else:
            cwd = Path(SESSION_CONTEXT.get("output_dir", str(_PROJECT_ROOT)))

        process = subprocess.Popen(
            shell_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=run_env,
            cwd=str(cwd),
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
                        logger.info(f"[命令输出] {line.rstrip()}")
                    else:
                        logger.warning(f"[命令错误] {line.rstrip()}")

        stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines, 'INFO'), daemon=True)
        stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines, 'WARNING'), daemon=True)

        stdout_thread.start()
        stderr_thread.start()

        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
            partial = ''.join(stdout_lines)
            if stderr_lines:
                partial += f"\n[stderr]: {''.join(stderr_lines)}"
            prefix = f"命令执行超时（>{timeout}秒）\n[部分输出]:\n{partial}" if partial.strip() else f"命令执行超时（>{timeout}秒，无输出）"
            return _finalize_tool_output("exec_bash", prefix, command=command, timeout=timeout)

        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

        stdout = ''.join(stdout_lines)
        stderr = ''.join(stderr_lines)

        output = stdout
        if stderr:
            output += f"\n[stderr]: {stderr}"
        if output:
            output = f"[shell={shell_name}]\n{output}"

        if returncode != 0:
            return _finalize_tool_output(
                "exec_bash",
                f"命令执行失败（退出码 {returncode}）：\n{output}",
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
    name="Bash",
    description=(
        "[必填] command: 要执行的 shell 命令。"
        "不要在 command 中嵌套 powershell -Command、pwsh -Command、bash -lc 或 sh -lc。"
        "[可选] workdir: 工作目录（绝对路径），默认当前会话输出目录。"
        "[可选] timeout: 超时秒数，默认 120。[可选] env: 额外环境变量（JSON格式）。"
        "自动选择可用 shell。Python 用 python，Node.js 用 node 或 npm。"
    ),
    args_schema=ExecBashInput,
    func=_impl_exec_bash,
    is_readonly=False,
    is_destructive=True,
    is_concurrency_safe=False,
    check_permissions_fn=make_exec_permission_fn("command"),
    permission_policy=ToolPermissionPolicy(policy_type="exec", command_field="command"),
)













class WebSearchInput(BaseModel):
    """网络搜索的输入参数"""
    query: str = Field(description="[必填] 搜索关键词")
    count: int = Field(default=10, description="[可选] 返回结果数量 (1-50)，默认 10")
    freshness: str = Field(default="py", description="[可选] 时间范围：pd(24h), pw(7d), pm(31d), py(365d), 或 YYYY-MM-DDtoYYYY-MM-DD")
    search_types: str = Field(default="web", description="[可选] 搜索类型: web, video, image（逗号分隔）")
    site: str = Field(default="", description="[可选] 指定站点搜索，如 baidu.com")


class FetchWebpageInput(BaseModel):
    """抓取网页正文的输入参数"""
    url: str = Field(description="[必填] 要抓取的网页 URL，如 https://example.com")
    max_chars: int = Field(default=12000, description="[可选] 返回正文的最大字符数，默认 12000")
    include_links: bool = Field(default=False, description="[可选] 是否附带页面中的部分链接")


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
    
    api_key = os.getenv("BAIDU_API_KEY") or os.getenv("FLOODMIND_SEARCH_API_KEY")
    search_url = "https://qianfan.baidubce.com/v2/ai_search/web_search"

    # 优先从 search.json 配置读取
    try:
        from floodmind.config.search_config import get_search_config
        search_cfg = get_search_config()
        if search_cfg.get("api_key"):
            api_key = search_cfg["api_key"]
        if search_cfg.get("url"):
            search_url = search_cfg["url"]
    except Exception:
        pass

    if not api_key:
        return _finalize_tool_output(
            "web_search",
            "错误：未配置搜索 API Key。请编辑 ~/.floodmind/search.json 或设置 BAIDU_API_KEY 环境变量",
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
        url = search_url
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
    name="WebSearch",
    description=(
        "网络搜索。[必填] query: 搜索关键词。[可选] count/freshness/search_types/site: 过滤条件。"
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

    _SSRF_BLOCKED_PREFIXES = (
        "127.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
        "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
        "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
        "172.30.", "172.31.", "192.168.", "169.254.", "0.",
        "localhost", "127.0.0.1", "[::1]", "::1",
        "fc00:", "fd00:", "fe80:",
    )
    from urllib.parse import urlparse
    parsed_url = urlparse(url)
    hostname = (parsed_url.hostname or "").lower()
    if hostname.startswith(_SSRF_BLOCKED_PREFIXES):
        return _finalize_tool_output("fetch_webpage", f"错误：禁止访问内网地址: {hostname}", url=url, max_chars=max_chars, include_links=include_links)

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
    name="WebFetch",
    description=(
        "抓取网页正文。[必填] url: 网页 URL，如 https://example.com。[可选] max_chars: 最大字符数。[可选] include_links: 是否附带链接。"
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
    name="MemoryAdd",
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
    name="MemorySearch",
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
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
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
    from floodmind.tools.agent_tool import get_agents_md_path

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
    name="UpdateProjectInstructions",
    description=(
        "修改项目级或全局 AGENTS.md 指令文件。[必填] action: 操作类型 append/replace_section/remove_section。[必填] content: 要写入的内容。"
        "[可选] section_title: 章节标题。[可选] scope: 范围 project 或 global。写入前会自动备份，请先向用户确认。"
    ),
    args_schema=UpdateProjectInstructionsInput,
    func=_impl_update_project_instructions,
    is_readonly=False,
    is_destructive=True,
    is_concurrency_safe=False,
    check_permissions_fn=make_ask_permission_fn("修改 AGENTS.md 指令文件会影响所有后续对话，需要用户确认"),
    permission_policy=ToolPermissionPolicy(policy_type="ask", reason="修改 AGENTS.md 指令文件会影响所有后续对话"),
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
        from floodmind.agent.scheduled_task_runtime import get_scheduled_task_runtime

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
    name="CreateScheduledTask",
    description=(
        "创建后台定时任务。[必填] command: 未来执行的业务任务描述，不要包含定时表达。"
        "每日任务用 repeat=daily + run_time=HH:MM，一次性任务用 repeat=none + scheduled_at。[可选] timezone/enabled。"
    ),
    args_schema=CreateScheduledTaskInput,
    func=_impl_create_scheduled_task,
    is_readonly=False,
    is_destructive=False,
    is_concurrency_safe=False,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="state_write"),
)


def _impl_list_scheduled_tasks(include_all_sessions: bool = True) -> str:
    try:
        from floodmind.agent.scheduled_task_runtime import get_scheduled_task_runtime

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
    name="ListScheduledTasks",
    description="查询定时任务列表。默认查询所有会话的定时任务。",
    args_schema=ListScheduledTasksInput,
    func=_impl_list_scheduled_tasks,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


def _impl_cancel_scheduled_task(task_id: str = "") -> str:
    try:
        from floodmind.agent.scheduled_task_runtime import get_scheduled_task_runtime

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
    name="CancelScheduledTask",
    description="取消定时任务。[必填] task_id: 要取消的任务 ID，通过 ListScheduledTasks 获取。",
    args_schema=CancelScheduledTaskInput,
    func=_impl_cancel_scheduled_task,
    is_readonly=False,
    is_destructive=False,
    is_concurrency_safe=False,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="state_write"),
)


def _register_all_tools():
    ToolRegistry.clear()

    # ── 文件操作工具（Glob/Grep/Read/Write/Edit）──────────────────
    from floodmind.tools.file_tools import register_file_tools
    register_file_tools()

    # ── 核心工具（PascalCase 命名）─────────────────────────────────
    ToolRegistry.register(get_skill)
    ToolRegistry.register(exec_bash)
    ToolRegistry.register(web_search)
    ToolRegistry.register(fetch_webpage)
    ToolRegistry.register(add_memory)
    ToolRegistry.register(search_memory)
    ToolRegistry.register(update_project_instructions)
    ToolRegistry.register(create_scheduled_task)
    ToolRegistry.register(list_scheduled_tasks)
    ToolRegistry.register(cancel_scheduled_task)

    # ── 别名注册（向后兼容）─────────────────────────────────────────
    ToolRegistry.register_alias("get_skill", "GetSkill")
    ToolRegistry.register_alias("exec_bash", "Bash")
    ToolRegistry.register_alias("web_search", "WebSearch")
    ToolRegistry.register_alias("fetch_webpage", "WebFetch")
    ToolRegistry.register_alias("add_memory", "MemoryAdd")
    ToolRegistry.register_alias("search_memory", "MemorySearch")
    ToolRegistry.register_alias("update_project_instructions", "UpdateProjectInstructions")
    ToolRegistry.register_alias("create_scheduled_task", "CreateScheduledTask")
    ToolRegistry.register_alias("list_scheduled_tasks", "ListScheduledTasks")
    ToolRegistry.register_alias("cancel_scheduled_task", "CancelScheduledTask")

    # ── 任务经验工具 ──────────────────────────────────────────
    from floodmind.config.settings import settings as _settings
    if _settings.task_experience.enabled:
        from floodmind.memory.task_experience import get_task_experience_store
        from floodmind.memory.experience_tree import ExperienceLeaf

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
            name="SearchTaskExperience",
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
            name="BrowseExperienceTree",
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
            name="DrillDownExperience",
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
            name="AddTaskExperience",
            description="手动添加任务执行经验到经验树，供未来类似任务参考。包括坑点、解决方案、步骤摘要和可复用代码片段。",
            args_schema=_AddTaskExperienceInput,
            func=_add_task_experience,
            is_readonly=False, is_destructive=False, is_concurrency_safe=False,
            check_permissions_fn=make_write_permission_fn(),
            permission_policy=ToolPermissionPolicy(policy_type="state_write"),
        ))

        ToolRegistry.register_alias("search_task_experience", "SearchTaskExperience")
        ToolRegistry.register_alias("browse_experience_tree", "BrowseExperienceTree")
        ToolRegistry.register_alias("drill_down_experience", "DrillDownExperience")
        ToolRegistry.register_alias("add_task_experience", "AddTaskExperience")

    # ── Todo 任务管理工具 ──────────────────────────────────────────
    from floodmind.tools.todo_tools import todo_write, todo_list
    ToolRegistry.register(todo_write)
    ToolRegistry.register(todo_list)
    ToolRegistry.register_alias("todo_write", "TodoWrite")
    ToolRegistry.register_alias("todo_list", "TodoList")


_register_all_tools()


__all__ = [
    'get_skill',
    'exec_bash',
    'web_search',
    'fetch_webpage',
    'add_memory',
    'search_memory',
    'update_project_instructions',
    'create_scheduled_task',
    'list_scheduled_tasks',
    'cancel_scheduled_task',
    'reset_retry_guard',
    'set_skill_registry',
    'set_memory_instance',
    'set_session_context',
    'get_current_session_output_dir',
    '_register_all_tools',
]
