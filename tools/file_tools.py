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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from tools.agent_tool import (
    ToolRegistry,
    build_agent_tool,
    make_readonly_permission_fn,
    make_write_permission_fn,
    make_read_path_permission_fn,
    resolve_tool_path,
)
from agent.runtime.contracts.permissions import ToolPermissionPolicy

from tools.base_tools import (
    _finalize_tool_output,
    _check_retry_guard_before_exec,
    _parse_json_if_needed,
    _SESSION_CONTEXT,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_BINARY_EXTENSIONS = frozenset({
    ".xlsx", ".xls", ".xlsm",
    ".docx", ".doc",
    ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
    ".exe", ".dll",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".pkl", ".pyc", ".pyd", ".so", ".dylib",
})


def _get_search_root(path: str) -> Path:
    if path and path.strip():
        resolved = resolve_tool_path(path.strip(), access="read").resolved
        if resolved.exists() and resolved.is_dir():
            return resolved
    output_dir = _SESSION_CONTEXT.get("output_dir")
    if output_dir:
        p = Path(output_dir)
        if p.exists():
            return p
    return _PROJECT_ROOT


# ── Glob ──────────────────────────────────────────────────────────────────

class GlobInput(BaseModel):
    pattern: str = Field(description="[必填] Glob 模式，如 **/*.xlsx, output_*.json")
    path: str = Field(default="", description="[可选] 搜索根目录，默认当前会话输出目录")


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
    search_root = _get_search_root(path)

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
        "搜索文件。[必填] pattern: Glob 模式匹配文件名，如 **/*.xlsx。[可选] path: 搜索根目录（绝对路径），默认当前会话输出目录。"
        "结果按修改时间倒序排列，最多返回 100 条。"
    ),
    args_schema=GlobInput,
    func=_impl_glob,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


# ── Grep ──────────────────────────────────────────────────────────────────

class GrepInput(BaseModel):
    pattern: str = Field(description="[必填] 正则表达式模式，用于搜索文件内容")
    path: str = Field(default="", description="[可选] 搜索根目录（绝对路径），默认当前会话输出目录")
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

    search_root = _get_search_root(path)

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
        "搜索文件内容。[必填] pattern: 正则表达式模式。[可选] path: 搜索根目录（绝对路径），默认当前会话输出目录。"
        "[可选] include: 文件过滤模式如 *.{py,md,json}。[可选] context: 上下文行数。[可选] max_results: 最大返回数量。"
    ),
    args_schema=GrepInput,
    func=_impl_grep,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


# ── Read ──────────────────────────────────────────────────────────────────

class ReadInput(BaseModel):
    file_path: str = Field(description="[必填] 文件绝对路径，如 D:/project/data/result.py")
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
    if path_result.source == "no_context_rejected":
        return _finalize_tool_output(
            "Read",
            "错误：无会话上下文时相对路径读取被拒绝。请提供绝对路径或文件名。",
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
        "读取文本文件。[必填] file_path: 文件绝对路径。[可选] offset: 起始行号（从1开始）。[可选] limit: 最大读取行数。"
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
    file_path: str = Field(description="[必填] 文件绝对路径，如 D:/project/data/result.py")
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
    if path_result.source == "no_context_rejected":
        return _finalize_tool_output(
            "Write",
            "错误：请传入文件绝对路径，如 D:/project/data/result.py",
            file_path=file_path,
            mode=mode,
        )
    target_file = path_result.resolved

    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)

        if mode == "append" and target_file.exists():
            with open(str(target_file), "a", encoding=encoding) as f:
                f.write(str(content))
        else:
            target_file.write_text(str(content), encoding=encoding)

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
        "写入文本文件。[必填] file_path: 文件绝对路径，如 D:/project/data/result.py。[必填] content: 文件内容。"
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
    file_path: str = Field(description="[必填] 文件绝对路径")
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
    if path_result.source == "no_context_rejected":
        return _finalize_tool_output(
            "Edit",
            "错误：file_path 请传入文件绝对路径",
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
        content = target_file.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _finalize_tool_output(
            "Edit",
            f"读取文件失败：{str(e)}",
            file_path=str(target_file),
        )

    count = content.count(old_string)

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
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    try:
        target_file.write_text(new_content, encoding="utf-8")
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
        "字符串替换编辑。[必填] file_path: 文件绝对路径。[必填] old_string: 要查找的精确字符串。[必填] new_string: 替换字符串。"
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


__all__ = [
    "Glob_tool",
    "Grep_tool",
    "Read_tool",
    "Write_tool",
    "Edit_tool",
    "register_file_tools",
]
