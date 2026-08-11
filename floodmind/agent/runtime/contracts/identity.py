"""Canonical identity contract (target §3)."""

from dataclasses import dataclass
from typing import Dict, Optional
import uuid

ID_PREFIXES: Dict[str, str] = {
    "conversation": "conv_",
    "task": "task_",
    "run": "run_",
    "thread": "thread_",
    "turn": "turn_",
    "attempt": "attempt_",
    "call": "call_",
    "transaction": "ttx_",
    "artifact": "art_",
    "checkpoint": "chk_",
}


def new_id(kind: str) -> str:
    prefix = ID_PREFIXES.get(kind)
    if prefix is None:
        raise ValueError(f"unknown id kind: {kind!r}")
    return f"{prefix}{uuid.uuid4().hex}"


def is_valid_id(kind: str, value: str) -> bool:
    prefix = ID_PREFIXES.get(kind)
    if prefix is None:
        return False
    return value.startswith(prefix) and len(value) > len(prefix)


@dataclass(frozen=True)
class Identity:
    """Identity scoping for a single run (target §3.1/§3.2)."""

    conversation_id: str
    task_id: str
    run_id: str
    thread_id: str
    turn_id: str
    attempt_id: Optional[str] = None
    call_id: Optional[str] = None
