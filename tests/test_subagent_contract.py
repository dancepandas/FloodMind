"""G-16：子代理结构化任务包与显式 user_text 契约。"""

from types import SimpleNamespace

from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.agent.native.types import RunContext
from floodmind.agent.runtime.contracts.child_thread import SubagentEventType, SubagentResult


def _make_agent(runtime) -> NativeFloodAgent:
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent._child_thread_defaults = {
        "max_turns": 5, "max_tokens": 1000, "wall_clock_budget_seconds": 10.0,
    }
    agent._ensure_child_thread_runtime = lambda _ctx: runtime
    return agent


class _CapturingRuntime:
    def __init__(self):
        self.context = None
        self.delegate_cwd = None

    def run(self, child_thread, context, *, step_event_bus=None, delegate_cwd=None):
        self.context = context
        self.delegate_cwd = delegate_cwd
        return SubagentResult(
            thread_id=child_thread.thread_id,
            parent_call_id=child_thread.parent_call_id,
            event_type=SubagentEventType.result,
            summary="done",
            completed=True,
        )


def _parent_context(user_text: str = "父代理收到的原始用户请求") -> RunContext:
    return RunContext(
        session_id="s1",
        user_text=user_text,
        runtime_context=SimpleNamespace(thread_id="parent-thread"),
    )


class TestSpecialistTaskPacket:
    def test_packet_contains_contract_sections_and_workdir(self):
        packet = NativeFloodAgent._build_specialist_user_input(
            "生成流域分析报告", "docx", "/work/dir"
        )
        assert "[核心任务]" in packet and "生成流域分析报告" in packet
        assert "[指定skill]" in packet and "docx" in packet
        assert "[工作目录]" in packet and "/work/dir" in packet
        assert "[边界]" in packet
        assert "[返回格式]" in packet
        assert "[结论]" in packet and "[产出]" in packet and "[未完成/风险]" in packet

    def test_empty_task_yields_empty_packet(self):
        assert NativeFloodAgent._build_specialist_user_input("  ") == ""


class TestSpecialistRunContext:
    def test_child_receives_packet_not_parent_user_text(self):
        runtime = _CapturingRuntime()
        agent = _make_agent(runtime)
        parent = _parent_context()

        agent._run_specialist_task(
            task_text="只做这一步",
            skill_name="docx",
            parent_context=parent,
            step_key="step-1",
            delegate_cwd="/tmp/w",
        )

        assert runtime.context is not parent
        assert "只做这一步" in runtime.context.user_text
        assert "[返回格式]" in runtime.context.user_text
        assert parent.user_text == "父代理收到的原始用户请求"
        assert runtime.delegate_cwd == "/tmp/w"

    def test_delegation_fails_closed_without_parent_context(self):
        agent = NativeFloodAgent.__new__(NativeFloodAgent)
        agent._current_run_context = None

        serial = agent._handle_delegate_specialist(task="do it")
        parallel = agent._handle_delegate_parallel(
            tasks=[{"task": "do it"}]
        )

        assert "父上下文" in serial
        assert "父上下文" in parallel
