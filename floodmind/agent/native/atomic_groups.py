"""Atomic Groups（目标 §9.4）：Compact MUST NOT 拆分的消息集合。"""

from typing import Dict, List, Tuple

from pydantic import BaseModel


class AtomicGroup(BaseModel):
    indices: List[int]
    kind: str
    required_together: bool = True


class AtomicGroups:
    def build(self, messages: List[Dict], *, pending_transaction_ids: List[str] = []) -> List[AtomicGroup]:
        groups: List[AtomicGroup] = []
        i = 0
        while i < len(messages):
            m = messages[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                # 并行工具组 + 全部 tool results（按 tool_call_id 配对，可能跨多条）
                call_ids = [tc.get("id") for tc in m["tool_calls"] if tc.get("id")]
                j = i + 1
                while j < len(messages) and messages[j].get("role") == "tool" and messages[j].get("tool_call_id") in call_ids:
                    j += 1
                groups.append(AtomicGroup(indices=list(range(i, j)), kind="parallel_tool"))
                i = j
                continue
            if m.get("role") == "assistant" and (m.get("reasoning") or m.get("reasoning_content")):
                # reasoning + 所属 assistant block（同一条消息内已含 → 单索引组）
                groups.append(AtomicGroup(indices=[i], kind="reasoning_block"))
                i += 1
                continue
            if m.get("role") == "system" and (m.get("compaction") or "compaction" in str(m.get("content", ""))[:40]):
                groups.append(AtomicGroup(indices=[i], kind="compaction_block"))
                i += 1
                continue
            if m.get("attachment"):
                groups.append(AtomicGroup(indices=[i], kind="attachment"))
                i += 1
                continue
            if pending_transaction_ids and str(m.get("transaction_id", "")) in pending_transaction_ids:
                groups.append(AtomicGroup(indices=[i], kind="pending_txn"))
                i += 1
                continue
            i += 1
        return groups

    def _member_indices(self, messages, groups) -> Dict[int, str]:
        member: Dict[int, str] = {}
        for g in groups:
            for idx in g.indices:
                member[idx] = g.kind
        return member

    def aligned_ranges(self, messages, *, pending_transaction_ids: List[str] = []) -> List[Tuple[int, int]]:
        """按 §9.4 构造不拆任何 Atomic Group 的可压缩段（连续段，边界落在组间隙）。"""
        groups = self.build(messages, pending_transaction_ids=pending_transaction_ids)
        member = self._member_indices(messages, groups)
        ranges: List[Tuple[int, int]] = []
        start = 0
        for i in range(1, len(messages) + 1):
            if i < len(messages) and (i in member or i - 1 in member):
                continue  # 在组内部，不能断开
            if start < i:
                ranges.append((start, i))
            start = i
        return ranges
