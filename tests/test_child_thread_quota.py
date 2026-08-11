"""P7 Task 2 — quota enforcement."""
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


def _runtime(tmp_path, mc, quota):
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
    )
    rt._quota = quota
    # 运行期在 run() 内自行包裹 _TokenBudgetModelClient；此处不预包，避免双重计数。
    return rt


def _stub_reg():
    reg = MagicMock(); reg.tools_schema.return_value = []
    return reg


def _stub_loader():
    return MagicMock()


def _parent_context(session_id="sess_main"):
    return RunContext(
        session_id=session_id, user_text="child task", agent_tier="main",
        runtime_context=RuntimeContext(
            conversation_id="c", task_id="t", run_id="run_1",
            thread_id="th_main", turn_id="tu_main", actor_type="agent",
            actor_id="main", agent_tier="main", runtime_mode="execution",
        ),
    )


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


def test_parallel_children_do_not_cross_cancel_reasons(tmp_path):
    """两个子代理并行跑在同一个 cached runtime 上：各自的配额/取消 reason 不串。"""
    import floodmind.agent.runtime.services.child_thread_runtime as ctr
    mc = MagicMock(spec=ModelClient)
    turn = {"n": 0}
    def stream(*a, **k):
        with threading.Lock():
            turn["n"] += 1
            n = turn["n"]
        return [ModelEvent(type="token", content=f"t{n}"),
                ModelEvent(type="usage", raw={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 100}),
                ModelEvent(type="done")]
    mc.stream_chat.side_effect = stream
    rt = _runtime(tmp_path, mc, ChildThreadQuota(max_turns=1, max_tokens=10**6, wall_clock_budget_seconds=30.0))
    # 两个子代理共用 rt（cached runtime 的真实形态）
    def go(i):
        child = ChildThread(thread_id=f"th_child_{i}", parent_thread_id="th_main",
                            parent_call_id=f"s{i}", max_turns=1, max_tokens=10**6,
                            wall_clock_budget_seconds=30.0)
        res = rt.run(child, _parent_context(session_id=f"sess_{i}"))
        return res.reason
    with ThreadPoolExecutor(max_workers=2) as ex:
        reasons = list(ex.map(go, [0, 1]))
    # 每个子代理因自己的 max_turns=1 配额终止，reason 必须都含 max_turns，且互不为对方污染
    assert all("quota:max_turns" in r for r in reasons)
