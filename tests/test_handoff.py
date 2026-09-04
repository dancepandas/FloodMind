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
from floodmind.agent.native.types import ModelEvent, RunContext, ToolCall
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
