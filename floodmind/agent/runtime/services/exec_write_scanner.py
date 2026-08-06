"""exec 命令写目标静态扫描。

背景
----
``exec_bash`` 等工具在命令体内执行的写操作（shell ``>``/``>>`` 重定向、PowerShell
``Set-Content``/``Out-File``/``New-Item``/``Copy-Item``/``Move-Item``/``Remove-Item``/
``Set-Item`` 等）的目标路径，此前完全不受 PathService 约束——宿主对某目录只给了
"只读授权"时，命令体仍可绕过写入。本模块提供保守的静态扫描：提取命令中高置信的写
目标路径，逐个按 write 权限解析；不在允许目录内的写目标即拒绝。

保守原则
--------
- 只认"像绝对/限定路径"的写目标（含路径分隔符 ``/``\\ ``\\`` ``~``，或以 ``.``/``$``
  开头，或盘符前缀）；相对工作区内的文件名自然落在可写目录内，无需拦截。
- 引号感知：字符串字面量里的 ``>``（如 ``echo "x > y"``）、被 echo 出来的 cmdlet
  文本都不会被误判为真实写操作。
- 无法解析的写目标（如 ``$变量`` 持绝对路径）静态层面无从判断 → fail-open，宿主
  可经 ``permission_decision_hook`` 收紧。
"""

import re
from typing import Callable, List, Optional

_WRITE_PS_CMDLETS = (
    "set-content",
    "add-content",
    "out-file",
    "new-item",
    "copy-item",
    "move-item",
    "remove-item",
    "set-item",
)
_WRITE_PS_FLAGS = {"-path", "-filepath", "-destination", "-literalpath"}
# 写入这些目标无害（丢弃流）
_NULL_TARGETS = {"/dev/null", "nul", "nul:", "nul1", "con", "$null", "$env:null"}


def _looks_like_path(token: str) -> bool:
    """是否像绝对/限定路径（用于过滤字符串字面量）。"""
    t = token.strip().strip('"').strip("'").strip()
    if not t or t.lower() in _NULL_TARGETS:
        return False
    if any(c in t for c in ("/", "\\", "~")):
        return True
    if t.startswith((".", "$")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", t):
        return True
    return False


def _split_statements(command: str) -> List[str]:
    """引号感知地把命令切成语句（按 ``;`` / ``|`` / ``&`` / 换行），串内不切。"""
    stmts: List[str] = []
    cur: List[str] = []
    quote: Optional[str] = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote is not None:
            cur.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
            i += 1
            continue
        if ch in ";|\n&":
            if cur:
                stmts.append("".join(cur))
                cur = []
            while i < n and (command[i] in ";|\n&" or command[i].isspace()):
                i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        stmts.append("".join(cur))
    return [s for s in stmts if s.strip()]


def _extract_redirect_targets(stmt: str) -> List[str]:
    """提取一条语句里的 shell 重定向写目标（引号感知）。"""
    targets: List[str] = []
    n = len(stmt)
    i = 0
    quote: Optional[str] = None
    while i < n:
        ch = stmt[i]
        if quote is not None:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == ">":
            # 消费连续 >（>>、2>> 等；前缀数字/& 由主循环自然跨过）
            j = i
            while j < n and stmt[j] == ">":
                j += 1
            k = j
            while k < n and stmt[k].isspace():
                k += 1
            if k < n and stmt[k] in "\"'":
                q = stmt[k]
                end = stmt.find(q, k + 1)
                if end != -1:
                    targets.append(stmt[k + 1:end])
                    i = end + 1
                    continue
            else:
                tok: List[str] = []
                while k < n and not stmt[k].isspace() and stmt[k] not in "|&;()":
                    tok.append(stmt[k])
                    k += 1
                targets.append("".join(tok))
                i = k
                continue
        i += 1
    return targets


def _tokenize(s: str) -> List[str]:
    """轻量 token 化（引号感知），供 PowerShell cmdlet 参数解析。"""
    tokens: List[str] = []
    cur: List[str] = []
    quote: Optional[str] = None
    for ch in s:
        if quote is not None:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
            continue
        if ch.isspace():
            if cur:
                tokens.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return tokens


def _extract_ps_cmdlet_targets(cmdlet: str, tokens: List[str]) -> List[str]:
    """从 PowerShell cmdlet 语句 token 中提取写目标（优先命名参数，其次位置参数）。"""
    flag_targets: List[str] = []
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if low in _WRITE_PS_FLAGS:
            if i + 1 < len(tokens):
                flag_targets.append(tokens[i + 1])
        elif low.startswith("-path:") or low.startswith("-filepath:") or low.startswith("-destination:") or low.startswith("-literalpath:"):
            flag_targets.append(tok.split(":", 1)[1])
    if flag_targets:
        return [t.strip().strip('"').strip("'") for t in flag_targets]
    # 位置参数：Copy-Item/Move-Item 的目标是最后一个路径参数；其余取第一个
    positional = [
        t.strip().strip('"').strip("'")
        for t in tokens
        if t.lower() not in _WRITE_PS_FLAGS and not t.startswith("-")
    ]
    if not positional:
        return []
    if cmdlet in ("copy-item", "move-item"):
        return [positional[-1]]
    return [positional[0]]


def extract_write_targets(command: str) -> List[str]:
    """提取命令中高置信的写目标路径（去重、过路径形态过滤）。"""
    targets: List[str] = []
    for stmt in _split_statements(command):
        targets.extend(_extract_redirect_targets(stmt))
        low = stmt.lstrip().lower()
        for cmdlet in _WRITE_PS_CMDLETS:
            if low.startswith(cmdlet):
                tail = stmt.lstrip()[len(cmdlet):]
                targets.extend(_extract_ps_cmdlet_targets(cmdlet, _tokenize(tail)))
                break
    seen: set = set()
    out: List[str] = []
    for t in targets:
        t = t.strip().strip('"').strip("'").strip()
        if _looks_like_path(t) and t not in seen:
            seen.add(t)
            out.append(t)
    return out


WriteTargetResolver = Callable[[str], object]


def check_exec_write_targets(
    command: str,
    *,
    resolver: WriteTargetResolver,
) -> Optional[str]:
    """扫描命令写目标，返回拒绝原因（``None`` = 通过）。

    ``resolver`` 负责把写目标路径解析为带 ``allowed``/``reason`` 的结果对象
    （PathService 的 ``resolve_simple(..., access="write")`` 或 ``resolve_tool_path``）。
    任一写目标不在允许写目录内 → 返回拒绝原因；无写目标或全部允许 → ``None``。
    """
    if not command or not command.strip():
        return None
    targets = extract_write_targets(command)
    if not targets:
        return None
    denied = []
    for t in targets:
        try:
            result = resolver(t)
        except Exception:
            continue  # 解析异常 fail-open，宿主可经 permission_decision_hook 收紧
        allowed = getattr(result, "allowed", None)
        if allowed is False:
            reason = getattr(result, "reason", "") or "不在允许写目录内"
            denied.append(f"{t}（{reason}）")
    if not denied:
        return None
    return (
        "命令体内的写目标不在允许写目录内，已拒绝执行: " + "; ".join(denied)
        + "。如需写入工作区外路径，请配置 writable_roots，或先在工作区中引用该文件以完成授权。"
    )
