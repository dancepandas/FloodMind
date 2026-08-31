"""
文件操作工具模块

提供 Glob、Grep、Read、Write、Edit 五个文件操作工具，
支持文件搜索、内容检索、文件读取、文件写入和字符串替换编辑。
"""

import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

from floodmind.tools.agent_tool import (
    ToolRegistry,
    build_agent_tool,
    make_readonly_permission_fn,
    make_write_permission_fn,
    make_read_path_permission_fn,
    resolve_tool_path,
)
from floodmind.agent.runtime.contracts.permissions import PermissionBehavior, PermissionDecision, ToolPermissionPolicy

from floodmind.tools.base_tools import (
    _finalize_tool_output,
    _check_retry_guard_before_exec,
    _parse_json_if_needed,
    SESSION_CONTEXT,
)
from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT as _PROJECT_ROOT

logger = logging.getLogger(__name__)

_BINARY_EXTENSIONS = frozenset({
    ".xlsx", ".xls", ".xlsm",
    ".docx", ".doc",
    ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
    ".exe", ".dll",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".pkl", ".pyc", ".pyd", ".so", ".dylib",
})


def _decode_file_bytes(raw: bytes) -> Tuple[str, str]:
    """把文件 bytes 解码为文本并记录实际编码：UTF-8 严格 → GBK（errors=replace）。

    GBK 回退使用 replace 解码（保持旧行为），调用方写回前必须先
    ``content.encode(encoding)`` 试编码；无法无损写回时拒绝写盘，
    绝不允许先截断文件再抛编码异常（旧实现会把文件截成 0 字节）。
    """
    try:
        return raw.decode("utf-8"), "utf-8"
    except (UnicodeDecodeError, UnicodeError):
        return raw.decode("gbk", errors="replace"), "gbk"


def _decode_text_strict(raw: bytes) -> Tuple[str, str]:
    """无损解码：UTF-8 严格 → GBK 严格，两者都失败时抛 UnicodeDecodeError。

    用于补丁应用等必须保证字节级无损的读写回路（避免 errors=replace 引入
    U+FFFD 后写回永久损坏非 UTF-8 文件）。
    """
    try:
        return raw.decode("utf-8"), "utf-8"
    except (UnicodeDecodeError, UnicodeError):
        pass
    return raw.decode("gbk"), "gbk"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """同目录临时文件 + os.replace 原子写：崩溃不会留下半截的目标文件。"""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise


def _get_search_root(path: str) -> tuple[Optional[Path], str]:
    if path and path.strip():
        path_result = resolve_tool_path(path.strip(), access="read")
        if not path_result.allowed:
            return None, path_result.reason or f"搜索根目录不允许访问: {path}"
        resolved = path_result.resolved
        if not resolved.exists():
            return None, f"搜索根目录不存在: {resolved}"
        if not resolved.is_dir():
            return None, f"搜索根路径不是目录: {resolved}"
        return resolved, ""

    cwd = SESSION_CONTEXT.get("cwd")
    if cwd:
        p = Path(cwd).resolve()
        if p.exists() and p.is_dir():
            return p, ""
        return None, f"当前 workspace cwd 不存在或不是目录: {p}"

    workspace_dir = SESSION_CONTEXT.get("workspace_dir")
    if workspace_dir:
        p = Path(workspace_dir).resolve()
        if p.exists() and p.is_dir():
            return p, ""
        return None, f"当前 workspace_dir 不存在或不是目录: {p}"

    return None, "缺少 workspace cwd，无法确定默认搜索根。请在工作区内启动 SDK，或显式传入已授权的 path。"


# ── Glob ──────────────────────────────────────────────────────────────────

class GlobInput(BaseModel):
    pattern: str = Field(description="[必填] Glob 模式，如 **/*.xlsx, output_*.json")
    path: str = Field(default="", description="[可选] 搜索根目录：相对当前 workspace 的目录，或已授权 roots 内的绝对目录；默认 workspace cwd")


def _glob_with_rg(search_root: Path, pattern: str) -> List[Path]:
    try:
        result = subprocess.run(
            ["rg", "--files", str(search_root)],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0 and not result.stdout:
            return []
        lines = result.stdout.strip().splitlines()
        all_files = [Path(line) for line in lines if line.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    matched = []
    for fp in all_files:
        try:
            rel = fp.relative_to(search_root)
        except ValueError:
            rel = fp
        if fnmatch.fnmatch(str(rel).replace("\\", "/"), pattern.replace("\\", "/")):
            matched.append(fp)
        elif fp.match(pattern):
            matched.append(fp)

    matched.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matched


def _glob_with_python(search_root: Path, pattern: str) -> List[Path]:
    matched = []
    pattern_posix = pattern.replace("\\", "/")
    for fp in search_root.rglob("*"):
        if not fp.is_file():
            continue
        try:
            rel = fp.relative_to(search_root)
        except ValueError:
            continue
        if fnmatch.fnmatch(str(rel).replace("\\", "/"), pattern_posix):
            matched.append(fp)
        elif fp.match(pattern):
            matched.append(fp)

    matched.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matched


def _impl_glob(pattern: str, path: str = "") -> str:
    parsed = _parse_json_if_needed(pattern)
    if parsed and "pattern" in parsed:
        pattern = parsed.get("pattern", pattern)
        path = parsed.get("path", path)

    pattern = str(pattern).strip() or "**/*"
    search_root, root_error = _get_search_root(path)
    if root_error or search_root is None:
        return _finalize_tool_output("Glob", f"搜索文件失败：{root_error}", pattern=pattern, path=path)

    try:
        matched = _glob_with_rg(search_root, pattern)
    except Exception:
        matched = []

    if not matched:
        try:
            matched = _glob_with_python(search_root, pattern)
        except Exception as e:
            return _finalize_tool_output(
                "Glob",
                f"搜索文件失败：{str(e)}",
                pattern=pattern,
                path=str(search_root),
            )

    matched = matched[:100]

    if not matched:
        return _finalize_tool_output(
            "Glob",
            f"未找到匹配文件。pattern={pattern}, path={search_root}",
            pattern=pattern,
            path=str(search_root),
        )

    lines = [f"找到 {len(matched)} 个匹配文件（搜索目录: {search_root}）：", ""]
    for idx, fp in enumerate(matched, start=1):
        try:
            rel = fp.relative_to(search_root)
        except ValueError:
            rel = fp
        mtime = datetime.fromtimestamp(fp.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{idx}. {rel}  ({fp}, {mtime})")

    return _finalize_tool_output("Glob", "\n".join(lines), pattern=pattern, path=str(search_root))


Glob_tool = build_agent_tool(
    name="Glob",
    description=(
        "搜索文件。[必填] pattern: Glob 模式匹配文件名，如 **/*.xlsx。"
        "[可选] path: 搜索根目录，相对当前 workspace，或已授权 roots 内的绝对路径；默认 workspace cwd。"
        "结果按修改时间倒序排列，最多返回 100 条。"
    ),
    args_schema=GlobInput,
    func=_impl_glob,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_read_path_permission_fn("path"),
    permission_policy=ToolPermissionPolicy(policy_type="read_path", path_field="path"),
)


# ── Grep ──────────────────────────────────────────────────────────────────

class GrepInput(BaseModel):
    pattern: str = Field(description="[必填] 正则表达式模式，用于搜索文件内容")
    path: str = Field(default="", description="[可选] 搜索根目录：相对当前 workspace 的目录，或已授权 roots 内的绝对目录；默认 workspace cwd")
    include: str = Field(default="", description="[可选] 文件过滤模式，如 *.{py,md,json}")
    context: int = Field(default=0, description="[可选] 匹配行前后上下文行数")
    max_results: int = Field(default=50, description="[可选] 最大返回结果数量，默认 50，最大 200")


def _grep_with_rg(pattern: str, search_root: Path, include: str, context: int, max_results: int) -> List[Dict[str, Any]]:
    cmd = ["rg", "--json", "--max-count", str(max_results), pattern, str(search_root)]
    if include:
        cmd.extend(["--glob", include])
    if context and context > 0:
        cmd.extend(["--context", str(context)])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if context and context > 0:
        sub_matches: Dict[tuple, List[Dict[str, Any]]] = {}
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type", "")
            if entry_type not in ("match", "context"):
                continue

            data = entry.get("data", {})
            file_path = data.get("path", {}).get("text", "")
            line_number = data.get("line_number", 0)
            text = data.get("lines", {}).get("text", "").rstrip()

            if not file_path or not line_number:
                continue

            key = (file_path, -1)
            if entry_type == "match":
                last_match_line = line_number
                key = (file_path, line_number)
                if key not in sub_matches:
                    sub_matches[key] = []
                sub_matches[key].append({"type": "match", "line_number": line_number, "text": text})
            elif entry_type == "context" and sub_matches:
                last_key = max(sub_matches.keys(), key=lambda k: k[1])
                if last_key[0] == file_path:
                    sub_matches[last_key].append({"type": "context", "line_number": line_number, "text": text})

        matches = []
        for (fp, _), lines_data in sub_matches.items():
            ctx_lines = []
            for ld in lines_data:
                prefix = ">" if ld["type"] == "match" else " "
                ctx_lines.append(f"{prefix}{ld['line_number']}: {ld['text'][:500]}")
            matches.append({
                "file_path": fp,
                "line_number": lines_data[0]["line_number"] if lines_data else 0,
                "text": "\n".join(ctx_lines),
            })
        return matches

    matches = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") != "match":
            continue

        data = entry.get("data", {})
        file_path = data.get("path", {}).get("text", "")
        line_number = data.get("line_number", 0)
        text = data.get("lines", {}).get("text", "").rstrip()

        if file_path and line_number:
            matches.append({
                "file_path": file_path,
                "line_number": line_number,
                "text": text,
            })

    return matches


def _grep_with_python(pattern: str, search_root: Path, include: str, context: int, max_results: int) -> List[Dict[str, Any]]:
    matches = []
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return [{"file_path": "", "line_number": 0, "text": f"正则表达式错误: {e}"}]

    for fp in search_root.rglob("*"):
        if not fp.is_file():
            continue
        if include:
            rel_name = fp.name
            if not fnmatch.fnmatch(rel_name, include):
                continue
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        all_lines = content.splitlines()
        for line_idx, line in enumerate(all_lines):
            if regex.search(line):
                if context and context > 0:
                    start = max(0, line_idx - context)
                    end = min(len(all_lines), line_idx + context + 1)
                    ctx_lines = []
                    for ci in range(start, end):
                        prefix = ">" if ci == line_idx else " "
                        ctx_lines.append(f"{prefix}{ci + 1}: {all_lines[ci][:500]}")
                    matches.append({
                        "file_path": str(fp),
                        "line_number": line_idx + 1,
                        "text": "\n".join(ctx_lines),
                    })
                else:
                    matches.append({
                        "file_path": str(fp),
                        "line_number": line_idx + 1,
                        "text": line.rstrip()[:500],
                    })
                if len(matches) >= max_results:
                    return matches

    return matches


def _impl_grep(pattern: str = "", path: str = "", include: str = "", context: int = 0, max_results: int = 50) -> str:
    parsed = _parse_json_if_needed(pattern)
    if parsed and "pattern" in parsed:
        pattern = parsed.get("pattern", pattern)
        path = parsed.get("path", path)
        include = parsed.get("include", include)
        context = parsed.get("context", context)
        max_results = parsed.get("max_results", max_results)

    pattern = str(pattern).strip()
    if not pattern:
        return _finalize_tool_output("Grep", "错误：搜索模式不能为空", pattern=pattern)

    try:
        max_results = max(1, min(int(max_results), 200))
    except (TypeError, ValueError):
        max_results = 50

    try:
        context = max(0, min(int(context), 10))
    except (TypeError, ValueError):
        context = 0

    search_root, root_error = _get_search_root(path)
    if root_error or search_root is None:
        return _finalize_tool_output("Grep", f"搜索内容失败：{root_error}", pattern=pattern, path=path)

    try:
        matches = _grep_with_rg(pattern, search_root, include, context, max_results)
    except Exception:
        matches = []

    if not matches:
        try:
            matches = _grep_with_python(pattern, search_root, include, context, max_results)
        except Exception as e:
            return _finalize_tool_output(
                "Grep",
                f"搜索内容失败：{str(e)}",
                pattern=pattern,
                path=str(search_root),
            )

    if not matches:
        return _finalize_tool_output(
            "Grep",
            f"未找到匹配内容。pattern={pattern}, path={search_root}",
            pattern=pattern,
            path=str(search_root),
        )

    if len(matches) == 1 and not matches[0].get("file_path"):
        return _finalize_tool_output("Grep", matches[0]["text"], pattern=pattern)

    lines = [f"找到 {len(matches)} 个匹配（搜索目录: {search_root}）：", ""]
    for m in matches[:max_results]:
        fp = m["file_path"]
        try:
            rel = str(Path(fp).relative_to(search_root))
        except (ValueError, TypeError):
            rel = fp
        if context and context > 0 and "\n" in m["text"]:
            lines.append(f"--- {rel}:{m['line_number']} ---")
            lines.append(m["text"][:800])
        else:
            lines.append(f"{rel}:{m['line_number']}: {m['text'][:300]}")

    return _finalize_tool_output("Grep", "\n".join(lines), pattern=pattern, path=str(search_root))


Grep_tool = build_agent_tool(
    name="Grep",
    description=(
        "搜索文件内容。[必填] pattern: 正则表达式模式。"
        "[可选] path: 搜索根目录，相对当前 workspace，或已授权 roots 内的绝对路径；默认 workspace cwd。"
        "[可选] include: 文件过滤模式如 *.{py,md,json}。[可选] context: 上下文行数。[可选] max_results: 最大返回数量。"
    ),
    args_schema=GrepInput,
    func=_impl_grep,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_read_path_permission_fn("path"),
    permission_policy=ToolPermissionPolicy(policy_type="read_path", path_field="path"),
)


# ── Read ──────────────────────────────────────────────────────────────────

class ReadInput(BaseModel):
    file_path: str = Field(description="[必填] 文件路径：相对当前 workspace，或已授权 roots 内的绝对路径")
    offset: int = Field(default=1, description="[可选] 起始行号（从 1 开始），默认 1")
    limit: int = Field(default=2000, description="[可选] 最大读取行数，默认 2000，最大 10000")


def _impl_read(file_path: str = "", offset: int = 1, limit: int = 2000) -> str:
    parsed = _parse_json_if_needed(file_path)
    if parsed and "file_path" in parsed:
        file_path = parsed.get("file_path", file_path)
        offset = parsed.get("offset", offset)
        limit = parsed.get("limit", limit)

    file_path = str(file_path).strip().strip('"').strip("'")
    if not file_path:
        return _finalize_tool_output("Read", "错误：file_path 参数不能为空", file_path=file_path)

    path_result = resolve_tool_path(file_path, access="read")
    if not path_result.allowed:
        return _finalize_tool_output(
            "Read",
            f"错误：{path_result.reason or '路径不允许读取'}",
            file_path=file_path,
        )
    resolved = path_result.resolved

    if not resolved.exists():
        return _finalize_tool_output(
            "Read",
            f"错误：文件不存在: {resolved}",
            file_path=str(resolved),
        )

    if not resolved.is_file():
        return _finalize_tool_output(
            "Read",
            f"错误：路径不是文件: {resolved}",
            file_path=str(resolved),
        )

    ext = resolved.suffix.lower()
    if ext in _BINARY_EXTENSIONS:
        size = resolved.stat().st_size
        return _finalize_tool_output(
            "Read",
            f"二进制文件（类型: {ext}，大小: {size} bytes）。请使用相应的 skill 处理。",
            file_path=str(resolved),
        )

    try:
        offset = max(1, int(offset))
        limit = max(1, min(int(limit), 10000))
    except (TypeError, ValueError):
        offset = 1
        limit = 2000

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _finalize_tool_output(
            "Read",
            f"读取文件失败：{str(e)}",
            file_path=str(resolved),
        )

    all_lines = content.splitlines()
    start = offset - 1
    end = min(start + limit, len(all_lines))
    selected = all_lines[start:end]

    output_lines = []
    for i, line in enumerate(selected, start=offset):
        if len(line) > 2000:
            line = line[:2000] + "...[行过长已截断]"
        output_lines.append(f"{i}: {line}")

    if not output_lines:
        return _finalize_tool_output(
            "Read",
            f"文件 {resolved.name} 在第 {offset} 行之后无内容",
            file_path=str(resolved),
            offset=offset,
            limit=limit,
        )

    header = f"=== 文件: {resolved.name} ===\n路径: {resolved}\n"
    if offset > 1 or end < len(all_lines):
        header += f"行范围: {offset}-{end} / 共 {len(all_lines)} 行\n"
    header += "\n"

    return _finalize_tool_output(
        "Read",
        header + "\n".join(output_lines),
        file_path=str(resolved),
        offset=offset,
        limit=limit,
    )


Read_tool = build_agent_tool(
    name="Read",
    description=(
        "读取文本文件。[必填] file_path: 文件路径，相对当前 workspace，或已授权 roots 内的绝对路径。[可选] offset: 起始行号（从1开始）。[可选] limit: 最大读取行数。"
        "二进制文件（.xlsx, .docx, .pdf, .png 等）会返回提示信息。"
    ),
    args_schema=ReadInput,
    func=_impl_read,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_read_path_permission_fn("file_path"),
    permission_policy=ToolPermissionPolicy(policy_type="read_path", path_field="file_path"),
)


# ── Write ─────────────────────────────────────────────────────────────────

class WriteInput(BaseModel):
    file_path: str = Field(description="[必填] 文件路径：相对当前 workspace，或已授权 roots 内的绝对路径")
    content: str = Field(description="[必填] 文件内容")
    mode: str = Field(default="overwrite", description="[可选] 写入模式：overwrite（覆盖）或 append（追加），默认 overwrite")
    encoding: str = Field(default="utf-8", description="[可选] 文件编码，默认 utf-8")


def _impl_write(file_path: str = "", content: str = "", mode: str = "overwrite", encoding: str = "utf-8") -> str:
    parsed = _parse_json_if_needed(file_path)
    if parsed and "file_path" in parsed:
        file_path = parsed.get("file_path", file_path)
        content = parsed.get("content", content)
        mode = parsed.get("mode", mode)
        encoding = parsed.get("encoding", encoding)

    file_path = str(file_path).strip().strip('"').strip("'")
    mode = str(mode).strip().lower() or "overwrite"
    encoding = str(encoding).strip() or "utf-8"

    if not file_path:
        _retry_block = _check_retry_guard_before_exec("Write", file_path=file_path)
        if _retry_block:
            return _finalize_tool_output("Write", _retry_block, file_path=file_path, mode=mode)
        return _finalize_tool_output("Write", "错误：file_path 参数不能为空", file_path=file_path, mode=mode)

    if mode not in ("overwrite", "append"):
        return _finalize_tool_output(
            "Write",
            "错误：mode 仅支持 'overwrite' 或 'append'",
            file_path=file_path,
            mode=mode,
        )

    path_result = resolve_tool_path(file_path, access="write")
    if not path_result.allowed:
        return _finalize_tool_output(
            "Write",
            f"错误：{path_result.reason or '路径不允许写入'}",
            file_path=file_path,
            mode=mode,
        )
    target_file = path_result.resolved

    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)

        # 以 bytes 写（os.replace 原子替换），不做任何换行翻译：
        # 内容中的 \n / \r\n 原样落盘，避免 write_text 的默认 newline=None
        # 把 LF 文件整体改写成 CRLF。
        data = str(content).encode(encoding)
        if mode == "append" and target_file.exists():
            # 追加不截断原文件，直接二进制追加（同样不做换行翻译）
            with open(str(target_file), "ab") as f:
                f.write(data)
        else:
            _atomic_write_bytes(target_file, data)

        action = "追加" if mode == "append" else "写入"
        return _finalize_tool_output(
            "Write",
            f"文件{action}成功：{target_file}",
            file_path=str(target_file),
            mode=mode,
            encoding=encoding,
        )
    except Exception as e:
        logger.error(f"写入文件失败: {e}", exc_info=True)
        return _finalize_tool_output(
            "Write",
            f"写入文件失败：{str(e)}",
            file_path=str(target_file),
            mode=mode,
            encoding=encoding,
        )


Write_tool = build_agent_tool(
    name="Write",
    description=(
        "写入文本文件。[必填] file_path: 文件路径，相对当前 workspace，或已授权 roots 内的绝对路径。[必填] content: 文件内容。"
        "[可选] mode: 写入模式，overwrite（覆盖）或 append（追加），默认 overwrite。[可选] encoding: 文件编码，默认 utf-8。"
        "自动创建父目录。"
    ),
    args_schema=WriteInput,
    func=_impl_write,
    is_readonly=False,
    is_destructive=True,
    is_concurrency_safe=False,
    check_permissions_fn=make_write_permission_fn("file_path"),
    permission_policy=ToolPermissionPolicy(policy_type="write", path_field="file_path"),
)


# ── Edit ──────────────────────────────────────────────────────────────────

class EditInput(BaseModel):
    file_path: str = Field(description="[必填] 文件路径：相对当前 workspace，或已授权 roots 内的绝对路径")
    old_string: str = Field(description="[必填] 要查找并替换的精确字符串")
    new_string: str = Field(description="[必填] 替换后的字符串")
    replace_all: bool = Field(default=False, description="[可选] 是否替换所有匹配，默认 False（只替换第一个）")


def _impl_edit(file_path: str = "", old_string: str = "", new_string: str = "", replace_all: bool = False) -> str:
    parsed = _parse_json_if_needed(file_path)
    if parsed and "file_path" in parsed:
        file_path = parsed.get("file_path", file_path)
        old_string = parsed.get("old_string", old_string)
        new_string = parsed.get("new_string", new_string)
        replace_all = parsed.get("replace_all", replace_all)

    file_path = str(file_path).strip().strip('"').strip("'")
    old_string = str(old_string)
    new_string = str(new_string)

    if not file_path:
        _retry_block = _check_retry_guard_before_exec("Edit", file_path=file_path)
        if _retry_block:
            return _finalize_tool_output("Edit", _retry_block, file_path=file_path)
        return _finalize_tool_output("Edit", "错误：file_path 参数不能为空", file_path=file_path)

    if not old_string:
        return _finalize_tool_output("Edit", "错误：old_string 参数不能为空", file_path=file_path)

    path_result = resolve_tool_path(file_path, access="write")
    if not path_result.allowed:
        return _finalize_tool_output(
            "Edit",
            f"错误：{path_result.reason or '路径不允许写入'}",
            file_path=file_path,
        )
    target_file = path_result.resolved

    if not target_file.exists() or not target_file.is_file():
        return _finalize_tool_output(
            "Edit",
            f"错误：文件不存在: {target_file}",
            file_path=str(target_file),
        )

    ext = target_file.suffix.lower()
    if ext in _BINARY_EXTENSIONS:
        return _finalize_tool_output(
            "Edit",
            f"错误：不支持编辑二进制文件（类型: {ext}）",
            file_path=str(target_file),
        )

    try:
        # 二进制读入自行解码（不做换行翻译，保留文件原始 \n / \r\n）；
        # 记录实际使用的编码，写回时用同一编码（先试编码再写盘，见下）。
        raw = target_file.read_bytes()
        content, file_encoding = _decode_file_bytes(raw)
    except Exception as e:
        return _finalize_tool_output(
            "Edit",
            f"读取文件失败：{str(e)}",
            file_path=str(target_file),
        )

    count = content.count(old_string)
    search_old, replace_new = old_string, new_string
    if count == 0 and "\r\n" in content and "\r\n" not in old_string:
        # CRLF 文件兼容：Read 展示的是 \n 文本，模型常以 \n 拼 old_string；
        # 精确匹配失败时按文件实际行尾还原再匹配（写回经 bytes 原样输出，行尾不变）。
        search_old = old_string.replace("\n", "\r\n")
        replace_new = new_string.replace("\n", "\r\n")
        count = content.count(search_old)

    if count == 0:
        return _finalize_tool_output(
            "Edit",
            "old_string not found in file",
            file_path=str(target_file),
        )

    if count > 1 and not replace_all:
        return _finalize_tool_output(
            "Edit",
            f"Found multiple matches for old_string ({count} occurrences). Provide more surrounding lines in oldString to identify the correct match, or set replace_all=True.",
            file_path=str(target_file),
        )

    if replace_all:
        new_content = content.replace(search_old, replace_new)
    else:
        new_content = content.replace(search_old, replace_new, 1)

    try:
        # 先试编码：GBK 等编码若无法无损写回（如 errors=replace 读入引入的 U+FFFD），
        # 拒绝写入并保留原文件。旧实现 open('w') 先截断再抛编码异常，会把文件截成 0 字节。
        encoded = new_content.encode(file_encoding)
    except (UnicodeEncodeError, UnicodeError):
        return _finalize_tool_output(
            "Edit",
            "错误：文件编码无法无损写回，请人工处理",
            file_path=str(target_file),
        )

    try:
        # 同目录临时文件 + os.replace 原子写，且不做换行翻译（保持原行尾不变）
        _atomic_write_bytes(target_file, encoded)
    except Exception as e:
        logger.error(f"编辑文件写入失败: {e}", exc_info=True)
        return _finalize_tool_output(
            "Edit",
            f"编辑文件写入失败：{str(e)}",
            file_path=str(target_file),
        )

    replaced_count = count if replace_all else 1
    desc = f"替换了 {replaced_count} 处" if replaced_count > 1 else "替换了 1 处"
    return _finalize_tool_output(
        "Edit",
        f"文件编辑成功：{target_file}\n{desc}",
        file_path=str(target_file),
        replace_all=replace_all,
    )


Edit_tool = build_agent_tool(
    name="Edit",
    description=(
        "字符串替换编辑。[必填] file_path: 文件路径，相对当前 workspace，或已授权 roots 内的绝对路径。[必填] old_string: 要查找的精确字符串。[必填] new_string: 替换字符串。"
        "[可选] replace_all: 是否替换所有匹配，默认 False（只替换第一个）。"
    ),
    args_schema=EditInput,
    func=_impl_edit,
    is_readonly=False,
    is_destructive=True,
    is_concurrency_safe=False,
    check_permissions_fn=make_write_permission_fn("file_path"),
    permission_policy=ToolPermissionPolicy(policy_type="write", path_field="file_path"),
)


# ── 注册 ──────────────────────────────────────────────────────────────────

def register_file_tools():
    ToolRegistry.register(Glob_tool)
    ToolRegistry.register(Grep_tool)
    ToolRegistry.register(Read_tool)
    ToolRegistry.register(Write_tool)
    ToolRegistry.register(Edit_tool)
    ToolRegistry.register(ApplyPatch_tool)


# ── ApplyPatch ─────────────────────────────────────────────────────────────

class ApplyPatchInput(BaseModel):
    file_path: str = Field(default="", description="文件路径（Add/Update/Delete 时指定）")
    patch: str = Field(description="[必填] 补丁内容，格式：*** Begin Patch ... *** End Patch")


def _impl_apply_patch(file_path: str = "", patch: str = "") -> str:
    """Apply a patch in OpenCode format."""
    parsed = _parse_json_if_needed(patch)
    if parsed and "patch" in parsed:
        patch = parsed.get("patch", patch)
    patch = str(patch)

    if not patch.strip():
        return _finalize_tool_output("ApplyPatch", "错误：patch 参数不能为空")

    # Parse and preflight all patch sections before mutating anything.
    sections = _parse_patch(patch)
    if not sections:
        return _finalize_tool_output("ApplyPatch",
            "错误：无法解析补丁格式。请使用 *** Begin Patch ... *** End Patch 格式")

    preflight = _check_patch_permissions(sections)
    if preflight.behavior != PermissionBehavior.ALLOW:
        return _finalize_tool_output("ApplyPatch", f"权限拒绝：{preflight.reason}", patch=patch[:200])

    results = []
    for sec in sections:
        try:
            result = _apply_section(sec)
            results.append(result)
        except Exception as e:
            results.append(f"错误 ({sec.get('action', '?')} {sec.get('file_path', '?')}): {e}")

    return _finalize_tool_output("ApplyPatch", "\n".join(results), patch=patch[:200])


def _parse_patch(patch_text: str) -> List[Dict[str, Any]]:
    """Parse *** Begin Patch ... *** End Patch format into sections."""
    # Extract content between Begin and End
    m = re.search(r'\*{3}\s*Begin Patch\s*\n(.*?)\*{3}\s*End Patch', patch_text, re.DOTALL)
    if not m:
        return []
    body = m.group(1)
    sections = []
    current = None
    in_diff = False

    for line in body.splitlines():
        add_match = re.match(r'\*{3}\s*Add File:\s*(.+)', line)
        del_match = re.match(r'\*{3}\s*Delete File:\s*(.+)', line)
        upd_match = re.match(r'\*{3}\s*Update File:\s*(.+)', line)
        move_match = re.match(r'\*{3}\s*Move to:\s*(.+)', line)

        if add_match:
            if current:
                sections.append(current)
            current = {"action": "add", "file_path": add_match.group(1).strip(), "lines": [], "move_to": ""}
            in_diff = False
        elif del_match:
            if current:
                sections.append(current)
            current = {"action": "delete", "file_path": del_match.group(1).strip(), "lines": [], "move_to": ""}
            in_diff = False
        elif upd_match:
            if current:
                sections.append(current)
            current = {"action": "update", "file_path": upd_match.group(1).strip(), "lines": [], "move_to": ""}
            in_diff = True
        elif move_match and current:
            current["move_to"] = move_match.group(1).strip()
        elif current is not None and line.strip():
            current["lines"].append(line)
        elif current is not None and in_diff:
            # Update 段内的空行必须保留原始行：统一 diff 的空上下文行（空行或单个
            # 空格）strip 后为空，丢弃会让 hunk 行计数错位、应用补丁时损坏文件。
            # （应用阶段会校验上下文行与原文一致，不匹配即中止，见 _apply_hunks。）
            current["lines"].append(line)

    if current:
        sections.append(current)
    return sections


def _check_patch_permissions(sections: List[Dict[str, Any]]) -> PermissionDecision:
    for sec in sections:
        action = sec.get("action", "")
        raw_path = str(sec.get("file_path", "")).strip()
        if not raw_path:
            return PermissionDecision(behavior=PermissionBehavior.DENY, reason="patch section 缺少 file_path")
        path_result = resolve_tool_path(raw_path, access="write")
        if not path_result.allowed:
            return PermissionDecision(behavior=PermissionBehavior.DENY, reason=path_result.reason or f"路径不允许: {raw_path}")
        if action in ("delete",):
            return PermissionDecision(behavior=PermissionBehavior.ASK, reason=f"ApplyPatch 包含删除操作: {raw_path}")
        move_to = str(sec.get("move_to", "")).strip()
        if move_to:
            move_result = resolve_tool_path(move_to, access="write")
            if not move_result.allowed:
                return PermissionDecision(behavior=PermissionBehavior.DENY, reason=move_result.reason or f"移动目标路径不允许: {move_to}")
            return PermissionDecision(behavior=PermissionBehavior.ASK, reason=f"ApplyPatch 包含移动操作: {raw_path} -> {move_to}")
    return PermissionDecision(behavior=PermissionBehavior.ALLOW)


def _apply_section(sec: Dict[str, Any]) -> str:
    action = sec["action"]
    raw_path = sec["file_path"]
    path_result = resolve_tool_path(raw_path, access="write")
    if not path_result.allowed:
        raise PermissionError(path_result.reason or f"路径不允许: {raw_path}")
    target = path_result.resolved
    move_to = sec.get("move_to", "")

    if action == "add":
        target.parent.mkdir(parents=True, exist_ok=True)
        # Collect lines with + prefix
        content_lines = []
        for l in sec["lines"]:
            if l.startswith("+"):
                content_lines.append(l[1:])
            elif l.startswith(" ") or not l.startswith("-"):
                content_lines.append(l)
        # 原子写 + bytes 直写（不做换行翻译）
        _atomic_write_bytes(target, ("\n".join(content_lines) + "\n").encode("utf-8"))
        return f"Created {target}"

    elif action == "delete":
        if target.exists():
            target.unlink()
            return f"Deleted {target}"
        return f"Delete skipped: {target} does not exist"

    elif action == "update":
        if not target.exists():
            return f"Update failed: {target} does not exist"

        try:
            # 无损解码（UTF-8 严格 → GBK 严格）：无法无损解码时拒绝执行，
            # 避免 errors=replace 读 + UTF-8 写回永久损坏 GBK 等非 UTF-8 文件。
            content, file_encoding = _decode_text_strict(target.read_bytes())
        except (UnicodeDecodeError, UnicodeError):
            return (
                f"Update failed: {target} 不是有效的 UTF-8/GBK 文本文件，"
                "无法无损解码，为避免损坏已拒绝执行，请人工处理"
            )
        lines = content.splitlines(keepends=True)

        # Parse unified diff hunks from sec["lines"]
        hunks = _parse_hunks(sec["lines"])
        if not hunks:
            return f"Update failed: no valid diff hunks found in patch"

        # 保持原文件行尾风格：CRLF 文件的替换行同样使用 CRLF，避免混入 LF
        newline_style = "\r\n" if "\r\n" in content else "\n"
        new_lines, apply_error = _apply_hunks(lines, hunks, newline=newline_style)
        if apply_error:
            # fail-closed：上下文与原文不一致时不写盘，绝不损坏目标文件
            return f"Update failed: {apply_error}"

        try:
            data = "".join(new_lines).encode(file_encoding)
        except (UnicodeEncodeError, UnicodeError):
            return f"Update failed: 文件编码无法无损写回，请人工处理"
        _atomic_write_bytes(target, data)

        if move_to:
            move_result = resolve_tool_path(move_to, access="write")
            if not move_result.allowed:
                raise PermissionError(move_result.reason or f"移动目标路径不允许: {move_to}")
            new_target = move_result.resolved
            new_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(new_target))
            return f"Updated and moved {target} → {new_target}"

        return f"Updated {target}"

    return f"Unknown action: {action}"


def _parse_hunks(patch_lines: List[str]) -> List[Dict[str, Any]]:
    """Parse unified diff hunks from patch lines."""
    hunks = []
    current = None
    for line in patch_lines:
        hunk_match = re.match(r'@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@(.*)', line)
        if hunk_match:
            if current:
                _trim_trailing_blank_context(current)
                hunks.append(current)
            current = {
                "old_start": int(hunk_match.group(1)),
                "old_count": int(hunk_match.group(2) or 1),
                "new_start": int(hunk_match.group(3)),
                "new_count": int(hunk_match.group(4) or 1),
                "context": hunk_match.group(5).strip(),
                "lines": [],
            }
        elif current is not None:
            current["lines"].append(line)

    if current:
        _trim_trailing_blank_context(current)
        hunks.append(current)
    return hunks


def _trim_trailing_blank_context(hunk: Dict[str, Any]) -> None:
    """去掉 hunk 末尾的空白行（patch 段落分隔符或空上下文行）。

    末尾的空上下文行是恒等操作（消耗后原样回填），去掉不会改变应用结果，
    却能避免模型补丁里常见的尾随空行被当成空上下文而触发误中止。
    """
    lines = hunk["lines"]
    while lines and not lines[-1].strip():
        lines.pop()


def _apply_hunks(
    original_lines: List[str],
    hunks: List[Dict[str, Any]],
    newline: str = "\n",
) -> Tuple[List[str], str]:
    """Apply unified diff hunks to original lines. Returns (new_lines, error).

    应用前校验上下文行与原文一致；不一致时返回 error 且不产出新内容，
    调用方必须放弃写盘（fail-closed，防止 hunk 计数错位损坏文件）。
    """
    result = list(original_lines)
    # Apply hunks in reverse order to maintain line offsets
    for hunk in reversed(hunks):
        old_start = hunk["old_start"] - 1  # 0-indexed
        old_count = hunk["old_count"]
        # Build replacement lines and verify context lines against the original
        replacement = []
        consumer = 0
        for l in hunk["lines"]:
            marker = l[0] if l else " "  # 空行按空上下文行处理
            body = l[1:] if l else ""
            if marker == "+" or marker == " ":
                if marker == " ":
                    idx = old_start + consumer
                    if idx >= len(original_lines) or \
                            original_lines[idx].rstrip("\r\n") != body.rstrip("\r\n"):
                        shown = body.rstrip("\r\n")[:50] or "<空行>"
                        return [], (
                            f"补丁上下文与文件内容不一致（原文件第 {idx + 1} 行期望 {shown!r}），"
                            "已中止且未修改文件"
                        )
                    consumer += 1
                replacement.append(body if body.endswith("\n") else body + newline)
            elif marker == "-":
                consumer += 1
            else:
                replacement.append(l + newline)

        # Remove old lines
        end = min(old_start + consumer, len(result))
        result[old_start:end] = replacement

    return result, ""


def _check_apply_patch_permissions(tool_input: dict) -> PermissionDecision:
    patch = str(tool_input.get("patch", ""))
    if not patch.strip():
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)
    sections = _parse_patch(patch)
    if not sections:
        return PermissionDecision(behavior=PermissionBehavior.DENY, reason="无法解析 ApplyPatch 补丁格式")
    return _check_patch_permissions(sections)


ApplyPatch_tool = build_agent_tool(
    name="ApplyPatch",
    description=(
        "使用补丁格式批量编辑文件。支持新增(Add File)、删除(Delete File)、更新(Update File)和移动(Move to)操作。"
        "格式：*** Begin Patch\\n*** Add File: path\\n+content\\n*** Update File: path\\n@@ ... @@\\n-old\\n+new\\n*** End Patch"
        "适用于 GPT 模型批量文件操作。"
    ),
    args_schema=ApplyPatchInput,
    func=_impl_apply_patch,
    is_readonly=False,
    is_destructive=True,
    is_concurrency_safe=False,
    check_permissions_fn=_check_apply_patch_permissions,
    permission_policy=ToolPermissionPolicy(policy_type="patch"),
)


__all__ = [
    "Glob_tool",
    "Grep_tool",
    "Read_tool",
    "Write_tool",
    "Edit_tool",
    "ApplyPatch_tool",
    "register_file_tools",
]
