# tests/test_memory_recall.py
import math
from datetime import datetime, timedelta

import pytest

from floodmind.memory.recall import MemoryRecallService
from floodmind.memory.semantic_store import SemanticMemoryStore, extract_terms


@pytest.fixture
def store(tmp_path):
    return SemanticMemoryStore(store_file=str(tmp_path / "sem.json"))


def make_service(store, **kw):
    params = dict(top_k=5, min_score=0.05, decay_scale_days=30.0, max_chars=1500)
    params.update(kw)
    return MemoryRecallService(store, **params)


def test_score_zero_on_disjoint_terms(store):
    e = store.add("报表导出格式要求", category="rule")
    terms = extract_terms("敖江洪水预报")
    assert MemoryRecallService.score(e, terms, datetime.now(), 30.0) == 0.0


def test_score_monotonic_decay(store):
    ts = datetime(2026, 1, 1, 12, 0, 0)
    e = store.add("敖江洪水预报参数", category="parameter", importance=1.0)
    e.valid_at = ts.isoformat()
    terms = extract_terms("敖江预报")
    s1 = MemoryRecallService.score(e, terms, ts, 30.0)
    s2 = MemoryRecallService.score(e, terms, ts + timedelta(days=30), 30.0)
    assert s1 == pytest.approx(1.0 * 1.0 * 1.0)          # relevance=1, recency=1, importance=1
    assert s2 == pytest.approx(math.exp(-1.0))           # 30 天后衰减一个尺度
    assert s2 < s1


def test_score_partial_relevance(store):
    e = store.add("敖江洪水预报", category="note", importance=1.0)
    terms = {"敖江", "预报", "完全无关词"}
    s = MemoryRecallService.score(e, terms, datetime.now(), 30.0)
    assert s == pytest.approx(2 / 3)  # relevance=2/3, recency=1, importance=1


def test_build_block_format_and_order(store):
    store.add("用户要求输出包含置信区间", category="preference", importance=0.9)
    store.add("敖江方案采用新安江三水源模型", category="decision", importance=0.5)
    store.add("报表配色规范", category="rule", importance=0.1)
    svc = make_service(store, top_k=2)
    block = svc.build_block("敖江预报方案怎么做")
    assert block.startswith("[长期记忆]")
    assert "敖江方案采用新安江三水源模型" in block
    assert "报表配色规范" not in block     # top_k=2 截断
    assert "(decision)" in block


def test_build_block_excludes_inactive(store):
    old = store.add("旧参数A", category="parameter")
    store.mark_deleted(old.entry_id)
    svc = make_service(store)
    assert svc.build_block("参数A") == ""


def test_build_block_below_min_score(store):
    store.add("弱相关内容数据", category="note", importance=0.1)
    svc = make_service(store, min_score=0.5)
    assert svc.build_block("数据") == ""


def test_build_block_max_chars(store):
    store.add("长事实" + "字" * 200, category="note", importance=1.0)
    store.add("另一条" + "内容" * 100, category="note", importance=1.0)
    svc = make_service(store, max_chars=120)
    block = svc.build_block("长事实 另一条")
    assert len(block) <= 120 + 60  # 头行 + 截断余量
