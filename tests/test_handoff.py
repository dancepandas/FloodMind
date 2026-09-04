"""Handoff 控制权移交：目标 Agent 完整接管同一 run。

验证：
- handoff 作为模型可见工具注册；
- 调用后目标 Agent 的 ModelClient/prompt/tools/executor 接管，主 Agent 不接回；
- 默认历史压缩，图片不复制；
- guardrail/workspace/journal 沿用当前 run；
- 事件与 canonical journal 落点。
"""

import copy
from unittest.mock import MagicMock

from floodmind.agent.handoff import HandoffTarget, default_handoff_history_filter
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import AgentLoopState, ModelEvent, RunContext, ToolCall
from floodmind.agent.runtime.contracts.tools import ToolResult, ToolSpec


def _mc(events):
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = events
    mc.model_name = "model"
    mc.enable_thinking = False
    return mc


def _ctx():
    return RunContext(session_id="s", user_text="help", output_dir="/tmp/o", upload_dir="/tmp/u")


def _executor(mc, registry, tool_executor=None, handoffs=None, prompts=None):
    return NativeAgentExecutor(
        model_client=mc,
        tool_executor=tool_executor or MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=5,
        system_prompts=prompts or ["MAIN PROMPT"],
        tools_schema=registry.tools_schema(),
        tool_registry=registry,
        handoffs=handoffs or [],
    )


class Registry:
    def __init__(self, specs=None):
        self._specs = {s.name: s for s in (specs or [])}

    def register(self, spec):
        self._specs[spec.name] = spec

    def get(self, name):
        return self._specs.get(name)

    def all(self):
        return list(self._specs.values())

    def tools_schema(self):
        return [s.to_openai_tool() for s in self._specs.values()]


class Target:
    """最小目标 Agent 适配对象：测试完整依赖接管。"""
    def __init__(self, mc, registry, tool_executor=None):
        self.session_id = "forecast"
        self.model_client = mc
        self.registry = registry
        self.tool_executor = tool_executor or MagicMock()
        self.system_prompts = ["FORECAST PROMPT"]
        self.tools_schema = registry.tools_schema()
        self.input_guardrails = []
        self.output_guardrails = []


class TestHandoffContract:
    def test_default_history_filter_compacts_and_strips_image(self):
        messages = [
            {"role": "system", "content": "MAIN"},
            {"role": "user", "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]},
            {"role": "assistant", "content": "我看到了"},
        ]
        filtered = default_handoff_history_filter(messages)
        assert filtered[0] == {"role": "system", "content": "MAIN"}
        assert len(filtered) == 2
        assert "看图" in filtered[1]["content"]
        assert "image_url" not in str(filtered)

    def test_handoff_tool_visible_to_model(self):
        target = Target(_mc([]), Registry())
        handoff = HandoffTarget(target, name="forecast")
        executor = _executor(_mc([]), Registry(), handoffs=[handoff])
        names = [t["function"]["name"] for t in executor._tools_schema]
        assert "handoff_to_forecast" in names

    def test_target_fully_takes_over_same_run(self):
        """第一轮主模型发 handoff；第二轮必须由目标模型调用，输出直接成为 final。"""
        main_mc = _mc([
            ModelEvent(type="tool_call_done",
                       tool_call=ToolCall(id="h1", name="handoff_to_forecast",
                                          arguments={"reason": "需要预报专家"})),
            ModelEvent(type="done"),
        ])
        captured = []
        target_mc = _mc([])
        target_mc.stream_chat.side_effect = lambda *a, **kw: (
            captured.append(copy.deepcopy(kw["messages"])),
            [ModelEvent(type="token", content="目标专家回答"), ModelEvent(type="done")],
        )[1]
        target_tool = ToolSpec(name="ForecastTool", description="预报", parameters={"type": "object"},
                               func=lambda: "x")
        target_registry = Registry([target_tool])
        target = Target(target_mc, target_registry)

        main_registry = Registry()
        executor = _executor(main_mc, main_registry, handoffs=[HandoffTarget(target, name="forecast")])
        result = executor.run(_ctx(), "请做预报")

        assert result.final_output == "目标专家回答"
        assert main_mc.stream_chat.call_count == 1
        assert target_mc.stream_chat.call_count == 1
        assert any(m.get("role") == "system" and m.get("content") == "FORECAST PROMPT"
                   for m in captured[0])
        tool_names = [t["function"]["name"] for t in target_mc.stream_chat.call_args.kwargs["tools"]]
        assert "ForecastTool" in tool_names

    def test_handoff_does_not_execute_as_normal_tool(self):
        main_mc = _mc([
            ModelEvent(type="tool_call_done",
                       tool_call=ToolCall(id="h1", name="handoff_to_forecast", arguments={})),
            ModelEvent(type="done"),
        ])
        target_mc = _mc([ModelEvent(type="token", content="done"), ModelEvent(type="done")])
        target = Target(target_mc, Registry())
        tool_executor = MagicMock()
        executor = _executor(main_mc, Registry(), tool_executor=tool_executor,
                             handoffs=[HandoffTarget(target, name="forecast")])
        executor.run(_ctx(), "help")
        tool_executor.execute.assert_not_called()

    def test_handoff_events_and_journal(self):
        main_mc = _mc([
            ModelEvent(type="tool_call_done",
                       tool_call=ToolCall(id="h1", name="handoff_to_forecast", arguments={})),
            ModelEvent(type="done"),
        ])
        target_mc = _mc([ModelEvent(type="token", content="done"), ModelEvent(type="done")])
        target = Target(target_mc, Registry())
        executor = _executor(main_mc, Registry(), handoffs=[HandoffTarget(target, name="forecast")])
        events = []
        executor.event_bus.add_listener(events.append)
        authority = MagicMock()
        from floodmind.agent.runtime.reducer import initial_run_state
        authority.replay.return_value = initial_run_state("run-1")
        executor._journal_authority = authority

        executor.run(_ctx(), "help")

        assert any(e.get("type") == "handoff_started" for e in events)
        journal_types = [c.args[0] for c in authority.emit.call_args_list]
        assert "agent.handoff.requested" in journal_types
        assert "agent.handoff.completed" in journal_types

    def test_terminal_agent_has_no_handoff_tool(self):
        executor = _executor(_mc([]), Registry(), handoffs=[])
        names = [t["function"]["name"] for t in (executor._tools_schema or [])]
        assert not any(n.startswith("handoff_to_") for n in names)


class TestHandoffReviewFixes:
    def test_public_agent_accepts_handoffs(self, tmp_path):
        """公共 Agent(handoffs=[...]) 必须把目标接到 production executor。"""
        from floodmind import Agent
        from floodmind.agent.runtime.contracts.workspace import Workspace

        target_llm = _mc([ModelEvent(type="token", content="target"), ModelEvent(type="done")])
        target_ws = Workspace.from_folder(str(tmp_path / "target"), session_id="target").ensure()
        target = Agent(llm=target_llm, session_id="target", workspace=target_ws)

        main_llm = _mc([
            ModelEvent(type="tool_call_done",
                       tool_call=ToolCall(id="h1", name="handoff_to_target", arguments={})),
            ModelEvent(type="done"),
        ])
        main_ws = Workspace.from_folder(str(tmp_path / "main"), session_id="main").ensure()
        main = Agent(
            llm=main_llm, session_id="main", workspace=main_ws,
            handoffs=[HandoffTarget(target, name="target")],
        )

        assert main.run("help") == "target"

    def test_handoff_takeover_does_not_leak_to_next_run(self):
        """handoff 只在本次 run 内接管；下一 run 恢复主模型/prompt/tools。"""
        main_rounds = []
        main_mc = _mc([])

        def main_stream(*a, **kw):
            main_rounds.append(copy.deepcopy(kw["messages"]))
            if len(main_rounds) == 1:
                return [
                    ModelEvent(type="tool_call_done",
                               tool_call=ToolCall(id="h1", name="handoff_to_forecast", arguments={})),
                    ModelEvent(type="done"),
                ]
            return [ModelEvent(type="token", content="main second run"), ModelEvent(type="done")]
        main_mc.stream_chat.side_effect = main_stream

        target_mc = _mc([ModelEvent(type="token", content="target first run"), ModelEvent(type="done")])
        target = Target(target_mc, Registry())
        executor = _executor(main_mc, Registry(), handoffs=[HandoffTarget(target, name="forecast")])

        first = executor.run(_ctx(), "first")
        second = executor.run(_ctx(), "second")

        assert first.final_output == "target first run"
        assert second.final_output == "main second run"
        assert main_mc.stream_chat.call_count == 2
        assert any(m.get("content") == "MAIN PROMPT" for m in main_rounds[-1]
                   if m.get("role") == "system")

    def test_pass_through_filter_drops_dangling_tool_calls(self):
        """自定义 pass-through filter 时，handoff/sibling tool_calls 不得悬空进目标请求。"""
        main_mc = _mc([
            ModelEvent(type="tool_call_done",
                       tool_call=ToolCall(id="h1", name="handoff_to_forecast", arguments={})),
            ModelEvent(type="tool_call_done",
                       tool_call=ToolCall(id="t9", name="SiblingTool", arguments={})),
            ModelEvent(type="done"),
        ])
        captured = []
        target_mc = _mc([])
        target_mc.stream_chat.side_effect = lambda *a, **kw: (
            captured.append(copy.deepcopy(kw["messages"])),
            [ModelEvent(type="token", content="done"), ModelEvent(type="done")],
        )[1]
        target = Target(target_mc, Registry())
        passthrough = lambda messages: copy.deepcopy(messages)
        executor = _executor(
            main_mc, Registry(),
            handoffs=[HandoffTarget(target, name="forecast", history_filter=passthrough)],
        )

        executor.run(_ctx(), "help")

        dangling = [
            tc for m in captured[0] if m.get("role") == "assistant"
            for tc in m.get("tool_calls", [])
        ]
        assert not dangling, "目标请求不得含没有 tool response 的悬空 tool_calls"

    def test_reducer_records_active_handoff_agent(self):
        """handoff.completed 必须折叠为 RunState.active_agent，供跨进程 resume 恢复控制面。"""
        from floodmind.agent.runtime.reducer import initial_run_state, reduce
        from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope

        rs = initial_run_state("run-1")
        rs = reduce(rs, EventEnvelope(
            event_id="e1", sequence=1, event_type="agent.handoff.completed",
            payload={"target_agent": "forecast", "tool_name": "handoff_to_forecast"},
        ))
        assert rs.active_agent == "forecast"

    def test_resume_reapplies_target_control_plane(self):
        """新 executor + replay(active_agent) 必须重新应用目标模型/工具，不靠进程内泄漏。"""
        from floodmind.agent.runtime.reducer import initial_run_state

        target_mc = _mc([ModelEvent(type="token", content="resumed target"), ModelEvent(type="done")])
        target = Target(target_mc, Registry())
        main_mc = _mc([ModelEvent(type="token", content="wrong main"), ModelEvent(type="done")])
        executor = _executor(main_mc, Registry(), handoffs=[HandoffTarget(target, name="forecast")])

        rs = initial_run_state("run-1")
        rs.active_agent = "forecast"
        rs.last_committed_sequence = 1
        state = AgentLoopState(
            session_id="s", run_id="run-1", status="awaiting_llm",
            active_handoff_agent="forecast",
            messages=[{"role": "system", "content": "FORECAST PROMPT"},
                      {"role": "user", "content": "resume"}],
        )

        result = executor.run_from_state(_ctx(), state, run_state=rs)
        assert result.final_output == "resumed target"
        main_mc.stream_chat.assert_not_called()
        assert target_mc.stream_chat.call_count == 1



class TestHandoffReviewRound4:
    def test_journal_completed_run_does_not_stick_handoff_to_next_turn(self):
        """Run terminal event clears active_agent：同 session 下一轮回到主 Agent。"""
        from floodmind.agent.runtime.reducer import initial_run_state, reduce
        from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
        rs = initial_run_state("run-1")
        rs = reduce(rs, EventEnvelope(
            event_id="h", sequence=1, event_type="agent.handoff.completed",
            payload={"target_agent": "forecast"},
        ))
        assert rs.active_agent == "forecast"
        rs = reduce(rs, EventEnvelope(
            event_id="r", sequence=2, event_type="run.completed",
            payload={"final_output": "done"},
        ))
        assert rs.active_agent == "", "handoff 只能影响当前 run，终态后必须清空"

    def test_unicode_name_sanitizes_to_ascii_tool_contract(self):
        target = Target(_mc([]), Registry())
        handoff = HandoffTarget(target, name="水文预报")
        import re
        assert re.fullmatch(r"[a-zA-Z0-9_-]+", handoff.resolved_tool_name)

    def test_passthrough_filter_drops_orphan_tool_messages(self):
        history = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "old", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "old", "content": "old result"},
        ]
        main_mc = _mc([
            ModelEvent(type="tool_call_done",
                       tool_call=ToolCall(id="h1", name="handoff_to_forecast", arguments={})),
            ModelEvent(type="done"),
        ])
        captured = []
        target_mc = _mc([])
        target_mc.stream_chat.side_effect = lambda *a, **kw: (
            captured.append(copy.deepcopy(kw["messages"])),
            [ModelEvent(type="token", content="done"), ModelEvent(type="done")],
        )[1]
        target = Target(target_mc, Registry())
        passthrough = lambda _msgs: copy.deepcopy(history)
        executor = _executor(main_mc, Registry(), handoffs=[
            HandoffTarget(target, name="forecast", history_filter=passthrough),
        ])
        executor.run(_ctx(), "help")
        assert not any(m.get("role") == "tool" for m in captured[0]),             "移除 tool_calls 后必须同步移除 orphan tool messages"

    def test_handoff_journal_failure_fails_atomically(self):
        main_mc = _mc([
            ModelEvent(type="tool_call_done",
                       tool_call=ToolCall(id="h1", name="handoff_to_forecast", arguments={})),
            ModelEvent(type="done"),
        ])
        target_mc = _mc([ModelEvent(type="token", content="target"), ModelEvent(type="done")])
        target = Target(target_mc, Registry())
        executor = _executor(main_mc, Registry(), handoffs=[HandoffTarget(target, name="forecast")])
        authority = MagicMock()
        from floodmind.agent.runtime.reducer import initial_run_state
        authority.replay.return_value = initial_run_state("run-1")
        authority.emit.side_effect = OSError("disk full")
        executor._journal_authority = authority
        try:
            executor.run(_ctx(), "help")
        except OSError:
            pass
        assert executor.model_client is main_mc, "journal requested 未提交不得切换目标控制面"

    def test_target_capabilities_recomputed_on_takeover(self):
        main_mc = _mc([
            ModelEvent(type="tool_call_done",
                       tool_call=ToolCall(id="h1", name="handoff_to_forecast", arguments={})),
            ModelEvent(type="done"),
        ])
        main_mc.provider = "main-provider"
        main_mc.model_name = "main-model"
        target_mc = _mc([ModelEvent(type="token", content="target"), ModelEvent(type="done")])
        target_mc.provider = "target-provider"
        target_mc.model_name = "target-model"
        target = Target(target_mc, Registry())
        executor = _executor(main_mc, Registry(), handoffs=[HandoffTarget(target, name="forecast")])
        executor.run(_ctx(), "help")
        assert "target-provider" in executor._capability_snapshot_id
        assert "target-model" in executor._capability_snapshot_id

    def test_invalid_target_dependencies_rejected_before_completed_event(self):
        class Broken:
            session_id = "broken"
            system_prompts = ["BROKEN"]
            registry = Registry()
            tool_executor = MagicMock()
        main_mc = _mc([
            ModelEvent(type="tool_call_done",
                       tool_call=ToolCall(id="h1", name="handoff_to_broken", arguments={})),
            ModelEvent(type="done"),
        ])
        executor = _executor(main_mc, Registry(), handoffs=[HandoffTarget(Broken(), name="broken")])
        events = []
        executor.event_bus.add_listener(events.append)
        try:
            executor.run(_ctx(), "help")
        except (TypeError, ValueError):
            pass
        assert not any(e.get("type") == "handoff_completed" for e in events)
