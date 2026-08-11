"""Conservative static analysis for filesystem writes embedded in exec commands.

The scanner distinguishes three states: no known write, resolved write targets, and a
recognized write whose target cannot be resolved statically.  The last state must not
be treated as safe: callers with an interactive permission route may ask the user;
direct execution callers fail closed.
"""

import contextvars
from dataclasses import dataclass
import re
from typing import Callable, Dict, List, Optional, Tuple

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
_NULL_TARGETS = {"/dev/null", "nul", "nul:", "nul1", "con", "$null", "$env:null"}
_PS_WRAPPER_RE = re.compile(r"^\s*(?:powershell|powershell\.exe|pwsh|pwsh\.exe)\b", re.IGNORECASE)
_PS_VARIABLE_RE = re.compile(r"^\$(?:\{(?P<braced>[A-Za-z_][\w:]*)\}|(?P<plain>[A-Za-z_][\w:]*))$")
_PYTHON_INLINE_RE = re.compile(r"^\s*(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?\s+(?:-[^\s]+\s+)*-c\s+", re.IGNORECASE)
_PY_OPEN_WRITE_RE = re.compile(
    r"\bopen\s*\(\s*(?P<target>[^,]+?)\s*,\s*['\"](?P<mode>[^'\"]*[wax+][^'\"]*)['\"]",
    re.IGNORECASE,
)
_PY_PATH_WRITE_RE = re.compile(
    r"\bPath\s*\(\s*(?P<target>[^)]+?)\s*\)\s*\.\s*(?:write_text|write_bytes|touch|unlink|rename|replace)\s*\(",
    re.IGNORECASE,
)
_PS_ASSIGNMENT_RE = re.compile(
    r"^\s*\$(?:\{(?P<braced>[A-Za-z_]\w*)\}|(?P<plain>[A-Za-z_]\w*))\s*=\s*(?P<value>.+?)\s*$",
    re.DOTALL,
)

# Permission approval and execution happen in separate layers.  Carry only the exact
# approved command through the current context so the execution handler can accept
# unresolved static targets without weakening direct/future calls.
_APPROVED_UNRESOLVED_COMMANDS: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "floodmind_approved_unresolved_exec_writes", default=frozenset()
)

# One strict union shared by permission adjudication and the Bash execution handler.
_DANGEROUS_COMMAND_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\brm\s+-rf\b", r"\brm\s+-r\b", r"\brmdir\s+/[sS]", r"\brd\s+/[sS]",
        r"\brd\s+/[qQ]", r"\bdel\s+/[sS]", r"\bdel\s+/[fF]", r"\bdel\s+/[qQ]",
        r"\bformat\s+[A-Za-z]:", r"\bshred\b", r"\bdd\s+if=", r"\bmkfs\b",
        r">\s*/dev/sd", r"\bchmod\s+-R\s+777\b", r"\bchown\s+-R\b",
        r"\bgit\s+push\s+--force\b", r"\bgit\s+reset\s+--hard\b",
        r"\bdocker\s+system\s+prune", r"\bdocker\s+rm\s+-f\b",
        r"\bRemove-Item\s+.*-Recurse", r"\bRemove-Item\s+.*-Force",
        r"\bpip\s+uninstall\b", r"\bconda\s+remove\b", r"\bnpm\s+uninstall\b",
        r"\btaskkill\s+/[fF]", r"\bnet\s+user\b", r"\bnet\s+localgroup\b",
        r"\bdiskpart\b", r"\breg\s+delete\b", r"\bregedit\b", r"\bmsiexec\b",
        r"\bcertutil\b", r"\bicacls\b", r"\bcacls\b", r"\bwbadmin\b",
        r"\bpowershell\s+-enc", r"\bpwsh\s+-enc\b", r"\bcmd\s+/c\s+del\b",
    )
)


@dataclass(frozen=True)
class ExecWriteScan:
    targets: Tuple[str, ...] = ()
    unresolved: Tuple[str, ...] = ()

    @property
    def has_writes(self) -> bool:
        return bool(self.targets or self.unresolved)


def _clean_token(token: str) -> str:
    return token.strip().strip('"').strip("'").strip()


def _is_null_target(token: str) -> bool:
    return _clean_token(token).lower() in _NULL_TARGETS


def _split_statements(command: str) -> List[str]:
    """Split on shell statement operators while preserving quoted content."""
    stmts: List[str] = []
    cur: List[str] = []
    quote: Optional[str] = None
    i = 0
    while i < len(command):
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
        if ch == "&" and cur and cur[-1] == ">" and i + 1 < len(command) and command[i + 1].isdigit():
            # File-descriptor duplication (2>&1) is not a filesystem write target.
            cur.append(ch)
            i += 1
            while i < len(command) and command[i].isdigit():
                cur.append(command[i])
                i += 1
            continue
        if ch in ";|\n&":
            if cur:
                stmts.append("".join(cur))
                cur = []
            while i < len(command) and (command[i] in ";|\n&" or command[i].isspace()):
                i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        stmts.append("".join(cur))
    return [stmt for stmt in stmts if stmt.strip()]


def _tokenize(value: str) -> List[str]:
    """Small quote-aware tokenizer suitable for PowerShell command arguments."""
    tokens: List[str] = []
    cur: List[str] = []
    quote: Optional[str] = None
    for ch in value:
        if quote is not None:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch.isspace():
            if cur:
                tokens.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return tokens


def _unwrap_powershell_command(command: str) -> str:
    """Return the script passed to a PowerShell ``-Command`` wrapper, if present."""
    if not _PS_WRAPPER_RE.match(command):
        return command
    tokens = _tokenize(command)
    for index, token in enumerate(tokens[1:], start=1):
        low = token.lower()
        if low in {"-command", "-c"}:
            if index + 1 >= len(tokens):
                return ""
            return _clean_token(" ".join(tokens[index + 1 :]))
        if low.startswith("-command:"):
            return _clean_token(token.split(":", 1)[1])
    return command


def _extract_redirect_targets(stmt: str) -> Tuple[List[str], List[str]]:
    targets: List[str] = []
    unresolved: List[str] = []
    i = 0
    quote: Optional[str] = None
    while i < len(stmt):
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
        if ch != ">":
            i += 1
            continue
        j = i
        while j < len(stmt) and stmt[j] == ">":
            j += 1
        k = j
        while k < len(stmt) and stmt[k].isspace():
            k += 1
        if k < len(stmt) and stmt[k] == "&":
            fd_end = k + 1
            while fd_end < len(stmt) and stmt[fd_end].isdigit():
                fd_end += 1
            if fd_end > k + 1:
                i = fd_end
                continue
        if k >= len(stmt):
            unresolved.append("redirection target is missing")
            break
        if stmt[k] in "\"'":
            q = stmt[k]
            end = stmt.find(q, k + 1)
            if end == -1:
                unresolved.append("redirection target has an unterminated quote")
                break
            token = stmt[k + 1 : end]
            i = end + 1
        else:
            start = k
            while k < len(stmt) and not stmt[k].isspace() and stmt[k] not in "|&;()":
                k += 1
            token = stmt[start:k]
            i = k
        if token:
            targets.append(token)
        else:
            unresolved.append("redirection target is empty")
    return targets, unresolved


def _extract_ps_cmdlet_targets(cmdlet: str, tokens: List[str]) -> Tuple[List[str], List[str]]:
    flag_targets: List[str] = []
    unresolved: List[str] = []
    for index, token in enumerate(tokens):
        low = token.lower()
        if low in _WRITE_PS_FLAGS:
            if index + 1 < len(tokens):
                flag_targets.append(tokens[index + 1])
            else:
                unresolved.append(f"{cmdlet} {token} has no target")
        elif any(low.startswith(flag + ":") for flag in _WRITE_PS_FLAGS):
            value = token.split(":", 1)[1]
            if value:
                flag_targets.append(value)
            else:
                unresolved.append(f"{cmdlet} target is empty")
    if flag_targets or unresolved:
        return [_clean_token(target) for target in flag_targets], unresolved

    positional = [_clean_token(token) for token in tokens if not token.startswith("-")]
    if not positional:
        return [], [f"{cmdlet} target cannot be determined"]
    if cmdlet in {"copy-item", "move-item"}:
        if len(positional) < 2:
            return [], [f"{cmdlet} destination cannot be determined"]
        return [positional[-1]], []
    return [positional[0]], []


def _literal_assignment_value(value: str) -> Optional[str]:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    # Bare path-like values are deterministic too; expressions/cmdlets are not.
    if value and not re.search(r"[\s()|;&]", value) and not value.startswith("$"):
        return value
    return None


def _resolve_target(target: str, variables: Dict[str, Optional[str]]) -> Tuple[Optional[str], Optional[str]]:
    clean = _clean_token(target)
    if not clean or _is_null_target(clean):
        return None, None
    match = _PS_VARIABLE_RE.match(clean)
    if not match:
        # Embedded expansion and subexpressions cannot be canonicalized safely.
        if "$" in clean or clean.startswith(("@(", "$(")):
            return None, f"dynamic write target cannot be resolved: {clean}"
        return clean, None
    name = (match.group("braced") or match.group("plain")).lower()
    if name in {"null", "env:null"}:
        return None, None
    value = variables.get(name)
    if value is None:
        return None, f"PowerShell variable write target cannot be resolved: {clean}"
    return value, None


def _literal_python_target(expression: str) -> Optional[str]:
    value = expression.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return None


def _extract_obvious_writer_targets(stmt: str) -> Tuple[List[str], List[str]]:
    """Recognize common writers without pretending to be a complete shell parser."""
    tokens = _tokenize(stmt.strip())
    if not tokens:
        return [], []
    command = _clean_token(tokens[0]).lower()
    if command in {"touch", "truncate"}:
        positional = [_clean_token(t) for t in tokens[1:] if not t.startswith("-")]
        return (positional, []) if positional else ([], [f"{command} target cannot be determined"])
    if command == "tee":
        positional = [_clean_token(t) for t in tokens[1:] if not t.startswith("-")]
        return (positional, []) if positional else ([], ["tee target cannot be determined"])
    if _PYTHON_INLINE_RE.match(stmt):
        targets: List[str] = []
        recognized = False
        for pattern in (_PY_OPEN_WRITE_RE, _PY_PATH_WRITE_RE):
            for match in pattern.finditer(stmt):
                recognized = True
                target = _literal_python_target(match.group("target"))
                if target is None:
                    return [], ["Python inline write target cannot be determined"]
                targets.append(target)
        # Inline Python with an obvious write-capable API must fail closed when its
        # argument is dynamic. Other inline code remains outside this bounded scanner.
        if recognized:
            return targets, []
    return [], []


def scan_exec_writes(command: str) -> ExecWriteScan:
    """Classify statically resolved and unresolved filesystem write targets."""
    script = _unwrap_powershell_command(command)
    if not script.strip() and _PS_WRAPPER_RE.match(command):
        return ExecWriteScan(unresolved=("PowerShell -Command script is missing",))

    variables: Dict[str, Optional[str]] = {}
    targets: List[str] = []
    unresolved: List[str] = []
    for stmt in _split_statements(script):
        assignment = _PS_ASSIGNMENT_RE.match(stmt)
        if assignment:
            name = (assignment.group("braced") or assignment.group("plain")).lower()
            variables[name] = _literal_assignment_value(assignment.group("value"))
            continue

        redirect_targets, redirect_unresolved = _extract_redirect_targets(stmt)
        unresolved.extend(redirect_unresolved)
        writer_targets, writer_unresolved = _extract_obvious_writer_targets(stmt)
        unresolved.extend(writer_unresolved)
        raw_targets = [*redirect_targets, *writer_targets]
        low = stmt.lstrip().lower()
        for cmdlet in _WRITE_PS_CMDLETS:
            if low.startswith(cmdlet):
                cmd_targets, cmd_unresolved = _extract_ps_cmdlet_targets(
                    cmdlet, _tokenize(stmt.lstrip()[len(cmdlet) :])
                )
                raw_targets.extend(cmd_targets)
                unresolved.extend(cmd_unresolved)
                break
        for target in raw_targets:
            resolved, problem = _resolve_target(target, variables)
            if resolved is not None and not _is_null_target(resolved):
                targets.append(resolved)
            if problem:
                unresolved.append(problem)

    return ExecWriteScan(
        targets=tuple(dict.fromkeys(targets)),
        unresolved=tuple(dict.fromkeys(unresolved)),
    )


def extract_write_targets(command: str) -> List[str]:
    """Compatibility helper returning only statically resolved targets."""
    return list(scan_exec_writes(command).targets)


WriteTargetResolver = Callable[[str], object]


def dangerous_command_reason(command: str) -> Optional[str]:
    """Return the shared strict-union denial reason for a dangerous command."""
    for pattern in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command or ""):
            return f"检测到危险命令模式: {pattern.pattern}"
    return None


def approve_unresolved_exec_writes(command: str) -> None:
    """Record user approval for this exact unresolved command in the current context."""
    approved = set(_APPROVED_UNRESOLVED_COMMANDS.get())
    approved.add(command.strip())
    _APPROVED_UNRESOLVED_COMMANDS.set(frozenset(approved))


def consume_unresolved_exec_write_approval(command: str) -> bool:
    """Consume a prior approval, preventing reuse by another execution attempt."""
    normalized = command.strip()
    approved = set(_APPROVED_UNRESOLVED_COMMANDS.get())
    if normalized not in approved:
        return False
    approved.remove(normalized)
    _APPROVED_UNRESOLVED_COMMANDS.set(frozenset(approved))
    return True


def check_exec_write_targets(
    command: str,
    *,
    resolver: WriteTargetResolver,
    allow_approved_unresolved: bool = False,
) -> Optional[str]:
    """Return a fail-closed denial reason, or ``None`` when all writes are allowed."""
    if not command or not command.strip():
        return None
    scan = scan_exec_writes(command)
    if scan.unresolved:
        if allow_approved_unresolved and consume_unresolved_exec_write_approval(command):
            return None
        return "命令包含无法静态解析的写目标，已拒绝执行: " + "; ".join(scan.unresolved)

    denied: List[str] = []
    for target in scan.targets:
        try:
            result = resolver(target)
        except Exception as exc:
            denied.append(f"{target}（路径解析失败: {exc}）")
            continue
        if getattr(result, "allowed", None) is not True:
            reason = getattr(result, "reason", "") or "写入权限无法确认"
            denied.append(f"{target}（{reason}）")
    if not denied:
        return None
    return (
        "命令体内的写目标不在允许写目录内，已拒绝执行: " + "; ".join(denied)
        + "。如需写入工作区外路径，请配置 writable_roots，或先在工作区中引用该文件以完成授权。"
    )
