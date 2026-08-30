"""P7 Task 2 — quota enforcement."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent, RunContext
from floodmind.agent.runtime.contracts.child_thread import ChildThread, SubagentEventType
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.child_thread_runtime import (
    ChildThreadQuota, ChildThreadRuntime, _TokenBudgetModelClient,
)
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.sandbox_service import SandboxService


def _runtime(tmp_path, mc, quota=None):
    parent_auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th_main", turn_id="tu_main",
    )
    rt = ChildThreadRuntime(
        model_client=mc,
        tool_executor=MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=100,
        system_prompts=["test prompt"],
        checkpoint_service=None,
        tracing_service=None,
        background_task_service=BackgroundTaskService(base_dir=str(tmp_path / "sessions")),
        journal_authority=parent_auth,
        sandbox_service=SandboxService(base_dir=str(tmp_path / "sbx")),
        permission_service=PermissionService(),
        path_service=PathService(),
        artifact_store_root=tmp_path / "artifacts",
        runtime_dir=tmp_path / "runtime",
        tool_runtime_factory=lambda: (_stub_reg(), _stub_loader()),
        quota_factory=(lambda child: quota) if quota is not None else None,
    )
    # 运行期在 run() 内自行包裹 _TokenBudgetModelClient；此处不预包，避免双重计数。
    return rt


def _stub_reg():
    reg = MagicMock(); reg.tools_schema.return_value = []
    return reg


def _stub_loader():
    return MagicMock()


def _parent_context(session_id="sess_main", abort_check=None):
    return RunContext(
        session_id=session_id, user_text="child task", agent_tier="main",
        abort_check=abort_check,
        runtime_context=RuntimeContext(
            conversation_id="c", task_id="t", run_id="run_1",
            thread_id="th_main", turn_id="tu_main", actor_type="agent",
            actor_id="main", agent_tier="main", runtime_mode="execution",
        ),
    )


def test_token_budget_reads_usage_from_content_json():
    """ModelClient 发出的 usage 事件 raw 恒为 None，数值在 content（JSON 字符串）。

    旧实现只读 event.raw["total_tokens"]，导致子代理 token 配额完全失效（回归测试）。
    """
    inner = MagicMock(spec=ModelClient)
    inner.stream_chat.return_value = iter([
        ModelEvent(type="token", content="x"),
        ModelEvent(type="usage", content=json.dumps(
            {"prompt_tokens": 30, "completion_tokens": 50, "total_tokens": 80})),
        ModelEvent(type="done"),
    ])
    quota = ChildThreadQuota(max_turns=10, max_tokens=100, wall_clock_budget_seconds=30.0)
    client = _TokenBudgetModelClient(inner, quota)
    list(client.stream_chat())
    assert quota.token_total == 80
    assert quota.turn_count == 1


def test_token_budget_ignores_malformed_usage_content():
    """usage content 不是合法 JSON 时容错忽略（计 0），不中断子线程。"""
    inner = MagicMock(spec=ModelClient)
    inner.stream_chat.return_value = iter([
        ModelEvent(type="token", content="x"),
        ModelEvent(type="usage", content="not-json"),
        ModelEvent(type="done"),
    ])
    quota = ChildThreadQuota(max_turns=10, max_tokens=100, wall_clock_budget_seconds=30.0)
    client = _TokenBudgetModelClient(inner, quota)
    list(client.stream_chat())
    assert quota.token_total == 0


def test_quota_max_turns_terminates_child_as_failed():
    import floodmind.agent.runtime.services.child_thread_runtime as ctr
    turn = {"n": 0}
    mc = MagicMock(spec=ModelClient)
    def stream(*a, **k):
        turn["n"] += 1
        return [ModelEvent(type="token", content=f"turn {turn['n']}"),
                ModelEvent(type="usage", raw={"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}),
                ModelEvent(type="done")]
    mc.stream_chat.side_effect = stream
    quota = ChildThreadQuota(max_turns=1, max_tokens=10**6, wall_clock_budget_seconds=30.0)
    rt = _runtime(__import__("pathlib").Path("."), mc, quota)
    child = ChildThread(thread_id="th_child", parent_thread_id="th_main", parent_call_id="s",
                        max_turns=1, max_tokens=10**6, wall_clock_budget_seconds=30.0)
    result = rt.run(child, _parent_context())
    assert result.event_type == SubagentEventType.failed
    assert result.reason.startswith("quota:max_turns")


def test_quota_max_tokens_terminates_child_as_failed():
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [
        ModelEvent(type="token", content="x"),
        ModelEvent(type="usage", raw={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 100}),
        ModelEvent(type="done"),
    ]
    quota = ChildThreadQuota(max_turns=100, max_tokens=100, wall_clock_budget_seconds=30.0)
    rt = _runtime(__import__("pathlib").Path("."), mc, quota)
    child = ChildThread(thread_id="th_child", parent_thread_id="th_main", parent_call_id="s",
                        max_turns=100, max_tokens=100, wall_clock_budget_seconds=30.0)
    result = rt.run(child, _parent_context())
    assert result.event_type == SubagentEventType.failed
    assert result.reason.startswith("quota:max_tokens")


def test_quota_wall_clock_terminates_child_as_failed():
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [ModelEvent(type="token", content="x"), ModelEvent(type="done")]
    quota = ChildThreadQuota(max_turns=100, max_tokens=10**6, wall_clock_budget_seconds=0.01)
    quota._deadline = time.monotonic() - 1  # 已过期
    rt = _runtime(__import__("pathlib").Path("."), mc, quota)
    child = ChildThread(thread_id="th_child", parent_thread_id="th_main", parent_call_id="s",
                        max_turns=100, max_tokens=10**6, wall_clock_budget_seconds=0.01)
    result = rt.run(child, _parent_context())
    assert result.event_type == SubagentEventType.failed
    assert result.reason == "quota:wall_clock"


def test_sequential_children_do_not_share_quota_state(tmp_path):
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [ModelEvent(type="token", content="x"), ModelEvent(type="done")]
    rt = _runtime(tmp_path, mc)

    child_a = ChildThread(
        thread_id="th_child_a", parent_thread_id="th_main", parent_call_id="sa",
        max_turns=1, max_tokens=10**6, wall_clock_budget_seconds=30.0,
    )
    result_a = rt.run(child_a, _parent_context(session_id="sess_a"))
    assert result_a.event_type == SubagentEventType.failed
    assert result_a.reason.startswith("quota:max_turns")

    child_b = ChildThread(
        thread_id="th_child_b", parent_thread_id="th_main", parent_call_id="sb",
        max_turns=10, max_tokens=10**6, wall_clock_budget_seconds=30.0,
    )
    result_b = rt.run(child_b, _parent_context(session_id="sess_b"))
    assert result_b.event_type == SubagentEventType.result
    assert result_b.reason == ""


def test_parallel_children_do_not_cross_cancel_reasons(tmp_path):
    """并行子代理必须保留各自不同的父取消/配额终止原因。"""
    mc = MagicMock(spec=ModelClient)
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    parent_cancel = threading.Event()
    calls = {"n": 0}

    def stream(*args, **kwargs):
        with lock:
            calls["n"] += 1
            call_number = calls["n"]
        if call_number > 1:
            barrier.wait(timeout=10)
            parent_cancel.set()
        return [
            ModelEvent(type="token", content="x"),
            ModelEvent(type="usage", raw={"prompt_tokens": 0, "completion_tokens": 0,
                                           "total_tokens": 1}),
            ModelEvent(type="done"),
        ]

    mc.stream_chat.side_effect = stream
    rt = _runtime(tmp_path, mc)

    # 先用高限额运行一次：旧实现会把该 quota 缓存在 runtime 上，令后续 B
    # 错误继承 max_turns=100；新实现每次 run 都创建独立 quota，不受此运行影响。
    seed = ChildThread(
        thread_id="th_z", parent_thread_id="th_main", parent_call_id="sz",
        max_turns=100, max_tokens=10**6, wall_clock_budget_seconds=30.0,
    )
    seed_result = rt.run(seed, _parent_context(session_id="sess_z"))
    assert seed_result.event_type == SubagentEventType.result

    child_a = ChildThread(
        thread_id="th_child_a", parent_thread_id="th_main", parent_call_id="sa",
        max_turns=100, max_tokens=10**6, wall_clock_budget_seconds=30.0,
    )
    child_b = ChildThread(
        thread_id="th_child_b", parent_thread_id="th_main", parent_call_id="sb",
        max_turns=1, max_tokens=10**6, wall_clock_budget_seconds=30.0,
    )

    with ThreadPoolExecutor(max_workers=2) as ex:
        future_a = ex.submit(
            rt.run, child_a,
            _parent_context(session_id="sess_a", abort_check=parent_cancel.is_set),
        )
        future_b = ex.submit(rt.run, child_b, _parent_context(session_id="sess_b"))
        result_a = future_a.result(timeout=20)
        result_b = future_b.result(timeout=20)

    assert calls["n"] == 3
    assert result_a.reason == "parent_cancelled"
    assert "quota:max_turns" in result_b.reason
