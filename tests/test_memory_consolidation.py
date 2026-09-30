# tests/test_memory_consolidation.py
import json

import pytest

from floodmind.memory.consolidation import MemoryConsolidationService
from floodmind.memory.semantic_store import SemanticMemoryStore


class _FakeClient:
    """按固定 JSON 响应的假模型客户端。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def invoke(self, prompt, system_prompt=None, temperature=None, max_tokens=None):
        self.calls += 1
        return type("R", (), {"content": self.payload})()


@pytest.fixture
def store(tmp_path):
    return SemanticMemoryStore(store_file=str(tmp_path / "sem.json"))


def decisions(payload_list):
    return json.dumps(payload_list, ensure_ascii=False)


def test_add_path(store):
    svc = MemoryConsolidationService(store, _FakeClient(decisions([
        {"index": 0, "action": "ADD", "reason": "新事实"},
    ])))
    stats = svc.consolidate([{"content": "用户要求输出包含置信区间", "type": "preference"}])
    assert stats["added"] == 1 and store.count_active() == 1


def test_update_invalidates_old(store):
    old = store.add("预报参数采用A方案", category="parameter")
    svc = MemoryConsolidationService(store, _FakeClient(decisions([
        {"index": 0, "action": "UPDATE", "target_id": old.entry_id, "reason": "参数已变更"},
    ])))
    stats = svc.consolidate([{"content": "预报参数采用B方案", "type": "parameter"}])
    assert stats["updated"] == 1
    assert store.get(old.entry_id).status == "superseded"
    assert store.get(old.entry_id).superseded_by is not None
    active = store.active_entries()
    assert len(active) == 1 and active[0].content == "预报参数采用B方案"


def test_delete_soft(store):
    e = store.add("旧规则每天8点推送", category="rule")
    svc = MemoryConsolidationService(store, _FakeClient(decisions([
        {"index": 0, "action": "DELETE", "target_id": e.entry_id, "reason": "规则已废止"},
    ])))
    stats = svc.consolidate([{"content": "旧规则每天8点推送已废止", "type": "rule"}])
    assert stats["deleted"] == 1
    assert store.get(e.entry_id).status == "deleted"
    assert store.get(e.entry_id).content  # 软删保留内容


def test_noop(store):
    svc = MemoryConsolidationService(store, _FakeClient(decisions([
        {"index": 0, "action": "NOOP", "reason": "临时信息"},
    ])))
    stats = svc.consolidate([{"content": "随便聊聊", "type": "note"}])
    assert stats["noop"] == 1 and store.count_active() == 0


def test_invalid_action_discards_all(store):
    svc = MemoryConsolidationService(store, _FakeClient(decisions([
        {"index": 0, "action": "DESTROY", "reason": "非法操作"},
    ])))
    stats = svc.consolidate([{"content": "内容甲", "type": "note"}])
    assert stats["discarded"] == 1 and store.count_active() == 0


def test_parse_failure_discards(store):
    svc = MemoryConsolidationService(store, _FakeClient("这不是JSON"))
    stats = svc.consolidate([{"content": "内容甲", "type": "note"}, {"content": "内容乙", "type": "note"}])
    assert stats["discarded"] == 2 and store.count_active() == 0


def test_update_with_unknown_target_discards(store):
    svc = MemoryConsolidationService(store, _FakeClient(decisions([
        {"index": 0, "action": "UPDATE", "target_id": "mem_nonexistent", "reason": "x"},
    ])))
    stats = svc.consolidate([{"content": "内容甲", "type": "note"}])
    assert stats["discarded"] == 1 and store.count_active() == 0


def test_no_candidates_skips_llm(store):
    client = _FakeClient("[]")
    svc = MemoryConsolidationService(store, client)
    stats = svc.consolidate([])
    assert client.calls == 0
    assert stats == {"added": 0, "updated": 0, "deleted": 0, "noop": 0, "discarded": 0}
