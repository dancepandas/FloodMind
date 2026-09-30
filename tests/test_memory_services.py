"""MemoryServices 门面测试（Task 5）。

验证三点：
1. 门面懒加载单例装配出 store/recall/consolidation/core 四个服务；
2. on_turn_completed 把 review 结果路由到四类操作管线（无模型客户端时判定失败全部丢弃，不回退直写）；
3. on_run_completed / on_checkpoint 为 P1/P2 预留空钩子，当前为 no-op。
"""

import pytest

from floodmind.memory import services as svc_mod


@pytest.fixture(autouse=True)
def _reset_singleton(tmp_path, monkeypatch):
    """每个用例重置门面单例，并把两个 JSON 存储重定向到临时目录，避免污染真实数据。"""
    monkeypatch.setattr(svc_mod, "_instance", None)
    monkeypatch.setattr(
        "floodmind.memory.semantic_store._DEFAULT_STORE_FILE",
        str(tmp_path / "sem.json"),
    )
    monkeypatch.setattr(
        "floodmind.memory.core_memory._DEFAULT_STORE_FILE",
        str(tmp_path / "core.json"),
    )


def test_facade_constructs_services():
    """门面装配：四个服务全部就绪（配置默认启用）。"""
    ms = svc_mod.get_memory_services()
    assert ms.store is not None
    assert ms.recall is not None
    assert ms.consolidation is not None
    assert ms.core is not None


def test_on_turn_completed_routes_candidates():
    """turn 完成钩子：偏好 + facts 进入固化管线；无客户端时判定失败全丢弃。"""
    ms = svc_mod.get_memory_services()
    ms.consolidation.set_model_client(None)  # 无客户端 → 判定失败 → 丢弃（不回退 ADD）

    class _Result:
        user_preferences = [{"content": "用户偏好甲", "type": "preference"}]
        facts = [{"content": "参数已改为B", "type": "parameter"}]

    stats_before = ms.store.count_active()
    ms.on_turn_completed(_Result())  # 不抛异常即通过（丢弃路径）
    assert ms.store.count_active() == stats_before


def test_on_run_completed_and_checkpoint_noop():
    """P1/P2 预留钩子当前为空实现。"""
    ms = svc_mod.get_memory_services()
    assert ms.on_run_completed(object()) is None
    assert ms.on_checkpoint(object()) is None
