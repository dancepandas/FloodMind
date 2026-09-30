# tests/test_core_memory_store.py
import json
from pathlib import Path

import pytest

from floodmind.memory.core_memory import CoreMemoryStore


@pytest.fixture
def store(tmp_path):
    return CoreMemoryStore(
        store_file=str(tmp_path / "core_memory.json"),
        migrate_legacy=False,
    )


def test_append_and_dedup(store):
    assert store.append("user_preferences", "输出包含置信区间") is True
    assert store.append("user_preferences", "输出包含置信区间") is False  # 去重
    assert store.append("project_constraints", "数据目录只读") is True
    data = store.load()
    assert len(data["facts"]["user_preferences"]) == 1
    assert data["facts"]["user_preferences"][0]["scope"] == "project"


def test_render_format_and_empty(store):
    assert store.render() == ""
    store.append("task_state", "当前任务：敖江预报")
    out = store.render()
    assert out.startswith("[核心记忆]")
    assert "## task_state" in out and "- 当前任务：敖江预报" in out


def test_render_truncation(store):
    store.append("c1", "字" * 500)
    store.append("c2", "另一条" * 100)
    out = store.render(max_chars=200)
    assert len(out) <= 260  # 头行 + 余量


def test_persistence_roundtrip(tmp_path):
    f = str(tmp_path / "core_memory.json")
    s1 = CoreMemoryStore(store_file=f, migrate_legacy=False)
    s1.append("k", "v", scope="session")
    s2 = CoreMemoryStore(store_file=f, migrate_legacy=False)
    item = s2.load()["facts"]["k"][0]
    assert item["fact"] == "v" and item["scope"] == "session"  # scope 预留字段保留


def test_legacy_session_migration(tmp_path):
    sessions = tmp_path / "sessions"
    s1 = sessions / "aaa"
    s2dir = sessions / "bbb"
    s1.mkdir(parents=True)
    s2dir.mkdir(parents=True)
    (s1 / "core_memory.json").write_text(json.dumps(
        {"facts": {"prefs": ["事实甲", "事实乙"]}}, ensure_ascii=False), encoding="utf-8")
    (s2dir / "core_memory.json").write_text(json.dumps(
        {"facts": {"prefs": ["事实乙", "事实丙"], "state": ["状态一"]}}, ensure_ascii=False), encoding="utf-8")
    target = str(tmp_path / "core_memory.json")
    store = CoreMemoryStore(store_file=target, migrate_legacy=True)
    data = store.load()["facts"]
    # 修正：Python sorted() 按 Unicode 码位排序，丙(0x4E19)<乙(0x4E59)<甲(0x7532)
    assert sorted(item["fact"] for item in data["prefs"]) == ["事实丙", "事实乙", "事实甲"]
    assert [item["fact"] for item in data["state"]] == ["状态一"]
    # 迁移幂等：再构造一次不重复
    store2 = CoreMemoryStore(store_file=target, migrate_legacy=True)
    assert len(store2.load()["facts"]["prefs"]) == 3
