"""幂等键派生与 Journal 幂等查询（目标 §6.5）。

- ``derive_idempotency_key``：对非 read 工具基于 (tool_id, canonical_arguments,
  side_effect_class) 派生确定性 SHA256 键；read 类返回空串（读可重试，不做幂等去重）。
- ``find_committed_result``：在 authority journal 中从后往前找同幂等键的
  ``tool.execution.completed``（succeeded），命中即复用已提交结果，不重执行。
  纯读，不写。
"""

import hashlib
from typing import Dict, Optional

from floodmind.agent.runtime.services.journal_authority import JournalAuthority


def derive_idempotency_key(*, tool_id: str, canonical_arguments: str, side_effect_class: str) -> str:
    if side_effect_class == "read":
        return ""  # 纯读可按资源版本重试，不做幂等去重
    return hashlib.sha256(
        (tool_id + canonical_arguments + side_effect_class).encode("utf-8")
    ).hexdigest()


def find_committed_result(authority: JournalAuthority, idempotency_key: str) -> Optional[Dict]:
    """从后往前找同幂等键的已提交 succeeded 结果；命中即复用，不重执行。"""
    if not idempotency_key:
        return None
    for event in reversed(authority.read_after(0)):
        if event.event_type != "tool.execution.completed":
            continue
        # 防御：失败结果归属 tool.execution.failed，不信任畸形的 completed 事件作为
        # 可复用成功（status 必须为 succeeded 才允许重放）。
        if event.payload.get("status") != "succeeded":
            continue
        if event.payload.get("idempotency_key") != idempotency_key:
            continue
        return {
            "result_summary": event.payload.get("result_summary", ""),
            "full_ref": event.payload.get("full_ref", ""),
            "artifacts": list(event.payload.get("artifacts", []) or []),
        }
    return None


def side_effect_class_for_spec(spec) -> str:
    """由 ToolSpec.is_readonly/is_destructive 推断 side_effect_class（§6.5）。

    readonly→read；destructive→irreversible；否则 reversible_write。
    非 ToolSpec 占位对象（None / MagicMock / 未设置 bool 字段）保守按 read 处理，
    不产生幂等键，保证只读安全与兼容既有测试注入的假对象。
    """
    if spec is None:
        return "read"
    is_readonly = getattr(spec, "is_readonly", False)
    is_destructive = getattr(spec, "is_destructive", False)
    if not isinstance(is_readonly, bool) or not isinstance(is_destructive, bool):
        return "read"
    if is_readonly:
        return "read"
    if is_destructive:
        return "irreversible"
    return "reversible_write"
