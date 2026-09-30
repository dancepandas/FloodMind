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


def test_review_wiring_injects_model_client_into_consolidation():
    """终审 C1 接线回归：worker 路径必须把 model_client 注入 consolidation。

    生产事故背景：门面构造 consolidation 时未传 client，apply_review_result
    直接调 on_turn_completed → _judge 抛 RuntimeError → 每轮候选全 discarded，
    P0-2 写路径整体失效。本测试锁死「生产接线必须注入 client」契约：
    走 spawn_background_review 的同步业务体 run_review_and_apply（spawn 本身是
    fire-and-forget 线程不可直接断言），断言固化管线收到非 None client、
    假 client 被真实调用（回顾 1 次 + 判定 1 次）、候选真正落盘。
    """
    import json as _json

    from floodmind.agent.native.background_review import run_review_and_apply

    ms = svc_mod.get_memory_services()
    assert ms.consolidation is not None

    class _FakeClient:
        """顺序弹出预设响应的假模型客户端：第 1 次=回顾 JSON，第 2 次=判定 JSON。"""

        def __init__(self, payloads):
            self._payloads = list(payloads)
            self.calls = 0

        def invoke(self, prompt, system_prompt=None, temperature=None, max_tokens=None):
            self.calls += 1
            payload = self._payloads.pop(0)
            return type("R", (), {"content": payload})()

    long_text = "水文预报参数校准与方案比选讨论。" * 30  # 保证格式化后 >= 200 字符
    messages = [
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": long_text},
        {"role": "user", "content": long_text},
    ]
    review_payload = _json.dumps(
        {
            "user_preferences": [{"content": "偏好输出包含置信区间", "type": "preference"}],
            "experience": None,
            "skill_suggestions": [],
            "facts": [],
        },
        ensure_ascii=False,
    )
    judge_payload = _json.dumps(
        [{"index": 0, "action": "ADD", "reason": "新偏好"}], ensure_ascii=False
    )
    fake = _FakeClient([review_payload, judge_payload])

    # 等价于 spawn_background_review 的 _worker 执行体（含 C1 注入逻辑）
    run_review_and_apply(
        "s1", messages, fake,
        memory_instance=None, experience_tree=None, memory_services=ms,
        min_message_count=3,
    )

    # 契约断言：consolidation 收到的是注入的 client 本身，且被真实调用
    assert ms.consolidation._model_client is fake
    assert fake.calls == 2  # 回顾 1 次 + 判定 1 次；若为 1 则说明注入缺失、判定被跳过
    assert ms.store.count_active() == 1  # ADD 真正落盘，写路径端到端打通


def test_apply_review_fallback_when_consolidation_disabled(monkeypatch):
    """配置旋钮回归：memory_services 提供但 consolidation 为 None
    （memory_consolidation.enabled=false）时，回顾偏好不得落地 nowhere——
    必须回退旧直写路径写 memory_instance.add_long_term_memory。
    （存储默认路径已由 autouse fixture _reset_singleton 重定向到 tmp。）"""
    from floodmind.agent.native.background_review import BackgroundReviewer, ReviewResult

    ms = svc_mod.get_memory_services()
    monkeypatch.setattr(ms, "consolidation", None)  # 模拟 consolidation 被配置禁用

    class _Mem:
        def __init__(self):
            self.written = []

        def add_long_term_memory(self, content, entry_type):
            self.written.append((content, entry_type))
            return True

    mem = _Mem()
    result = ReviewResult(
        user_preferences=[{"content": "偏好输出包含置信区间", "type": "preference"}],
        experience=None,
        skill_suggestions=[],
        facts=[],
    )
    applied = BackgroundReviewer(model_client=None).apply_review_result(
        "s1", result,
        memory_instance=mem, experience_tree=None, memory_services=ms,
    )
    assert applied["preferences"] == 1
    assert mem.written == [("偏好输出包含置信区间", "preference")]
