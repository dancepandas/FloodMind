# tests/test_semantic_store.py
import json
from datetime import datetime, timedelta

import pytest

from floodmind.memory.semantic_store import LTMEntryV2, SemanticMemoryStore, extract_terms


@pytest.fixture
def store(tmp_path):
    return SemanticMemoryStore(store_file=str(tmp_path / "semantic_memory.json"))


def test_extract_terms_chinese_and_ascii():
    terms = extract_terms("敖江 洪水预报 flood forecast")
    assert "敖江" in terms and "flood" in terms and "forecast" in terms
    assert "洪" not in terms  # 单字不抽取（{2,}）


def test_add_and_active(store):
    e = store.add("用户要求输出包含置信区间", category="preference", importance=0.8)
    assert e.status == "active" and e.entry_id.startswith("mem_")
    assert store.count_active() == 1
    assert store.get(e.entry_id).content == "用户要求输出包含置信区间"


def test_superseded_chain(store):
    old = store.add("预报参数采用A", category="parameter")
    new = store.add("预报参数采用B", category="parameter")
    assert store.mark_superseded(old.entry_id, new.entry_id) is True
    old2 = store.get(old.entry_id)
    assert old2.status == "superseded"
    assert old2.invalid_at is not None and old2.superseded_by == new.entry_id
    assert old2.content == "预报参数采用A"  # 失效不覆盖
    assert [e.entry_id for e in store.active_entries()] == [new.entry_id]


def test_soft_delete(store):
    e = store.add("旧规则：每天8点推送", category="rule")
    assert store.mark_deleted(e.entry_id) is True
    got = store.get(e.entry_id)
    assert got.status == "deleted" and got.content  # 软删保留内容
    assert store.count_active() == 0


def test_find_candidates_only_active(store):
    a = store.add("敖江洪水预报参数", category="parameter")
    store.add("敖江洪水预报参数", category="parameter")
    b = store.add("报表导出格式要求", category="rule")
    store.mark_deleted(b.entry_id)
    cands = store.find_candidates(extract_terms("敖江预报"), top_k=3)
    assert all(c.status == "active" for c in cands)
    assert any(c.entry_id == a.entry_id for c in cands)


def test_import_legacy(tmp_path):
    legacy = tmp_path / "long_term_memory.json"
    ts = (datetime.now() - timedelta(days=10)).isoformat()
    legacy.write_text(json.dumps([
        {"content": "旧事实一", "category": "preference", "importance": 0.7,
         "timestamp": ts, "access_count": 2},
        {"content": "旧事实二", "category": "note", "importance": 0.5,
         "timestamp": ts, "access_count": 0},
    ], ensure_ascii=False), encoding="utf-8")
    store = SemanticMemoryStore(store_file=str(tmp_path / "semantic_memory.json"))
    n = store.import_legacy(str(legacy))
    assert n == 2
    assert store.count_active() == 2
    entry = [e for e in store.active_entries() if e.content == "旧事实一"][0]
    assert entry.valid_at == ts and entry.status == "active"
    # 幂等：重复导入不增加
    assert store.import_legacy(str(legacy)) == 0
    assert store.count_active() == 2


def test_persistence_roundtrip(tmp_path):
    f = str(tmp_path / "semantic_memory.json")
    s1 = SemanticMemoryStore(store_file=f)
    e = s1.add("持久化测试", category="note")
    s2 = SemanticMemoryStore(store_file=f)
    assert s2.get(e.entry_id).content == "持久化测试"
    s2.mark_deleted(e.entry_id)
    s3 = SemanticMemoryStore(store_file=f)
    assert s3.get(e.entry_id).status == "deleted"
