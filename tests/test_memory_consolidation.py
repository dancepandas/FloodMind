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


def test_duplicate_update_targets_discard_round(store):
    """同一轮两个 UPDATE 指向同一 target → 整轮丢弃：后到者 mark_superseded
    会因目标已 superseded 返回 False，其新条目成孤儿 active。判非法挡在应用前。"""
    old = store.add("旧参数A方案", category="parameter")
    svc = MemoryConsolidationService(store, _FakeClient(decisions([
        {"index": 0, "action": "UPDATE", "target_id": old.entry_id, "reason": "r1"},
        {"index": 1, "action": "UPDATE", "target_id": old.entry_id, "reason": "r2"},
    ])))
    stats = svc.consolidate([
        {"content": "新参数B方案", "type": "parameter"},
        {"content": "新参数C方案", "type": "parameter"},
    ])
    assert stats["discarded"] == 2 and stats["updated"] == 0 and stats["added"] == 0
    # store 无孤儿：旧条目仍 active，且没有任何新增条目
    assert store.get(old.entry_id).status == "active"
    assert store.count_active() == 1


def test_delete_add_same_index_discards(store):
    """DELETE+ADD 指向同一 index（同候选）→ index 唯一性校验拒绝，整轮丢弃，
    已删事实不会"复活"成新 active 条目。"""
    e = store.add("旧事实待删除", category="note")
    svc = MemoryConsolidationService(store, _FakeClient(decisions([
        {"index": 0, "action": "DELETE", "target_id": e.entry_id, "reason": "r1"},
        {"index": 0, "action": "ADD", "reason": "r2"},
        {"index": 1, "action": "NOOP", "reason": "r3"},
    ])))
    stats = svc.consolidate([
        {"content": "同 index 候选", "type": "note"},
        {"content": "另一候选", "type": "note"},
    ])
    assert stats["discarded"] == 2
    assert stats["deleted"] == 0 and stats["added"] == 0
    # 未软删、无新增——语义存储保持原状
    assert store.get(e.entry_id).status == "active"
    assert store.count_active() == 1


def test_update_race_reclaims_new_entry(store, monkeypatch):
    """并发竞争窗口：_validate 通过后 mark_superseded 返回 False（目标被其他
    写入抢先失效）→ 刚 add 的新条目被 mark_deleted 回收，不计入 updated，
    不留下孤儿 active。"""
    old = store.add("旧参数A方案", category="parameter")
    svc = MemoryConsolidationService(store, _FakeClient(decisions([
        {"index": 0, "action": "UPDATE", "target_id": old.entry_id, "reason": "r"},
    ])))
    monkeypatch.setattr(store, "mark_superseded", lambda *a, **k: False)
    stats = svc.consolidate([{"content": "新参数B方案", "type": "parameter"}])
    assert stats["updated"] == 0
    # 新条目已回收（deleted），旧条目仍 active——无孤儿 active
    active = store.active_entries()
    assert len(active) == 1 and active[0].entry_id == old.entry_id
    assert all(e.status == "deleted" for e in store._entries
               if e.content == "新参数B方案")


def test_no_candidates_skips_llm(store):
    client = _FakeClient("[]")
    svc = MemoryConsolidationService(store, client)
    stats = svc.consolidate([])
    assert client.calls == 0
    assert stats == {"added": 0, "updated": 0, "deleted": 0, "noop": 0, "discarded": 0}
