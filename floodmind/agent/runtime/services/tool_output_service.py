"""G-08：工具输出上限与卸载（阈值 + 有界摘要 + 引用 + Read 读取）。

单次工具输出超过阈值时：
1. 全文原子写入 ``<state_dir>/tool_outputs/<tool>-<call_id>.txt``；
2. 返回给模型的内容只保留头尾，中间以显式标记省略；
3. 返回 ``full_ref``（卸载文件绝对路径），模型可用 Read 工具按需读取。

缺少可写 ``state_dir`` 时仍返回有界摘要，但显式说明全文不可用——绝不把超大
结果原样放进上下文，也不静默丢弃而不告知。
"""

import logging
import os
import re
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 30_000
_OFFLOAD_DIRNAME = "tool_outputs"
_MAX_FILENAME_STEM = 120


def _safe_filename_stem(tool_name: str, call_id: str) -> str:
    raw = f"{tool_name}-{call_id}"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return (safe or "tool")[:_MAX_FILENAME_STEM]


def _offload(content: str, tool_name: str, call_id: str, state_dir: str) -> str:
    """原子写卸载文件并返回绝对路径；失败返回空字符串。"""
    if not state_dir:
        return ""
    path = Path(state_dir) / _OFFLOAD_DIRNAME / f"{_safe_filename_stem(tool_name, call_id)}.txt"
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)
        return str(path.resolve())
    except OSError as exc:
        logger.error("工具输出卸载失败 (%s): %s", path, exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return ""


def bound_tool_output(
    content: str,
    *,
    tool_name: str,
    call_id: str,
    state_dir: str = "",
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Tuple[str, str]:
    """返回 ``(bounded_content, full_ref)``。

    ``len(content) <= max_chars`` 时原样返回、``full_ref`` 为空；
    超限时全文卸载（若可）并返回严格不超过 ``max_chars`` 的头尾摘要。
    """
    if not isinstance(content, str):
        content = str(content)
    if max_chars <= 0 or len(content) <= max_chars:
        return content, ""

    full_ref = _offload(content, tool_name, call_id, state_dir)
    if full_ref:
        notice = (
            f"\n\n... [工具输出共 {len(content)} 字符，超过 {max_chars} 字符上限；"
            f"中间部分已省略，全文已卸载至 {full_ref}，可用 Read 工具读取] ...\n\n"
        )
    else:
        notice = (
            f"\n\n... [工具输出共 {len(content)} 字符，超过 {max_chars} 字符上限；"
            "中间部分已省略且无法卸载（缺少可写 state_dir），"
            "请缩小查询范围后重试] ...\n\n"
        )

    budget = max_chars - len(notice)
    if budget <= 2:
        # 阈值小于提示本身：只保留提示，保证输出仍然有界。
        return notice[:max_chars], full_ref
    head_len = budget // 2
    tail_len = budget - head_len
    return f"{content[:head_len]}{notice}{content[-tail_len:]}", full_ref
