"""G-15：记忆有效期（valid_to）、过期失效与召回过滤。"""

from datetime import datetime, timedelta

import pytest

from floodmind.memory.recall import MemoryRecallService
from floodmind.memory.semantic_store import SemanticMemoryStore, parse_valid_to


@pytest.fixture
def store(tmp_path):
    return SemanticMemoryStore(store_file=str(tmp_path / "semantic.json"))


class TestValidToParsing:
    def test_date_only_expires_at_end_of_day(self):
        parsed = parse_valid_to("2026-12-31")
        assert parsed == datetime(2026, 12, 31, 23, 59, 59)

    def test_iso_datetime(self):
        assert parse_valid_to("2026-12-31T10:30:00") == datetime(2026, 12, 31, 10, 30)

    def test_invalid_value_means_no_expiry(self):
        assert parse_valid_to("not-a-date") is None
        assert parse_valid_to("") is None
        assert parse_valid_to(None) is None


class TestExpiryLifecycle:
    def test_expired_entries_are_not_active(self, store):
        expired = store.add("临时结论", valid_to="2000-01-01")
        active = store.add("长期事实")

        assert expired.is_expired()
        assert store.count_active() == 1
        assert [e.entry_id for e in store.active_entries()] == [active.entry_id]

    def test_expire_due_marks_status_and_is_idempotent(self, store):
        entry = store.add("阶段性方案", valid_to="2000-01-01")

        assert store.expire_due() == 1
        stored = store.get(entry.entry_id)
        assert stored.status == "expired"
        assert stored.invalid_at
        assert stored.valid_to == "2000-01-01"
        assert store.expire_due() == 0

    def test_manual_now_controls_expiry(self, store):
        entry = store.add("按时点失效", valid_to="2030-01-01")
        assert entry.is_expired(datetime(2031, 1, 1))
        assert not entry.is_expired(datetime(2029, 1, 1))

    def test_future_entry_stays_active(self, store):
        store.add("未来有效", valid_to=(datetime.now() + timedelta(days=3)).isoformat())
        assert store.count_active() == 1

    def test_expiry_persists_across_reload(self, store, tmp_path):
        entry = store.add("持久化过期", valid_to="2000-01-01")
        store.expire_due()

        reloaded = SemanticMemoryStore(store_file=str(tmp_path / "semantic.json"))
        assert reloaded.get(entry.entry_id).status == "expired"
        assert reloaded.count_active() == 0


class TestRecallExcludesExpired:
    def test_expired_fact_is_not_recalled(self, store):
        store.add("敖江洪水预报参数使用新方案", valid_to="2000-01-01")
        recall = MemoryRecallService(store, decay_scale_days=30)

        assert recall.build_block("敖江洪水预报参数") == ""

    def test_active_fact_still_recalled(self, store):
        store.add("敖江洪水预报参数使用新方案")
        recall = MemoryRecallService(store, decay_scale_days=30)

        assert "敖江" in recall.build_block("敖江洪水预报参数")


class TestMemoryAddTtl:
    def test_ttl_days_sets_valid_to(self, tmp_path, monkeypatch):
        from floodmind.memory import services as svc_mod
        from floodmind.tools import base_tools

        store = SemanticMemoryStore(store_file=str(tmp_path / "sem.json"))
        fake_services = type("MS", (), {"store": store})()
        monkeypatch.setattr(svc_mod, "_instance", fake_services)

        result = base_tools._impl_add_memory("本月临时使用旧参数", "decision", ttl_days=30)

        assert "已记录到长期记忆" in result
        entry = store.active_entries()[0]
        assert entry.valid_to is not None
        deadline = parse_valid_to(entry.valid_to)
        assert timedelta(days=29) < deadline - datetime.now() <= timedelta(days=31)

    def test_memory_add_without_ttl_has_no_expiry(self, tmp_path, monkeypatch):
        from floodmind.memory import services as svc_mod
        from floodmind.tools import base_tools

        store = SemanticMemoryStore(store_file=str(tmp_path / "sem.json"))
        fake_services = type("MS", (), {"store": store})()
        monkeypatch.setattr(svc_mod, "_instance", fake_services)

        base_tools._impl_add_memory("长期事实", "note")

        assert store.active_entries()[0].valid_to is None
