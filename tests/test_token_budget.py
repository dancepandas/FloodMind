"""G-18：单 run token 预算与显式终态。"""

import json
from unittest.mock import MagicMock

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent, RunContext, ToolCall
from floodmind.agent.runtime.contracts.tools import ToolResult

_TOOLS_SCHEMA = [{"type": "function", "function": {"name": "test_tool"}}]


def _make_executor(model_client, tool_executor=None, max_iterations=5):
    registry = MagicMock()
    registry.get.return_value = None
    registry.all.return_value = []
    registry.tools_schema.return_value = _TOOLS_SCHEMA
    return NativeAgentExecutor(
        model_client=model_client,
        tool_executor=tool_executor or MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=max_iterations,
        system_prompt="test prompt",
        tools_schema=_TOOLS_SCHEMA,
        tool_registry=registry,
    )


def _context(max_total_tokens: int) -> RunContext:
    return RunContext(
        session_id="budget-session",
        user_text="hello",
        output_dir="/tmp/budget-out",
        upload_dir="/tmp/budget-up",
        max_total_tokens=max_total_tokens,
    )


def _tool_round(total_tokens: int):
    return [
        ModelEvent(
            type="usage",
            content=json.dumps({
                "prompt_tokens": total_tokens,
                "completion_tokens": 0,
                "total_tokens": total_tokens,
            }),
        ),
        ModelEvent(
            type="tool_call_done",
            tool_call=ToolCall(id="t1", name="test_tool", arguments={"k": "v"}),
        ),
        ModelEvent(type="done"),
    ]


def _final_round(text: str = "Done."):
    return [ModelEvent(type="token", content=text), ModelEvent(type="done")]


def _tool_executor():
    executor = MagicMock()
    executor.execute.return_value = ToolResult(
        tool_call_id="t1", name="test_tool", content="tool output", status="completed",
    )
    return executor


class TestRunTokenBudget:
    def test_exhausted_budget_blocks_next_llm_call_with_explicit_terminal(self):
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = [_tool_round(100), _final_round()]
        executor = _make_executor(mc, tool_executor=_tool_executor())

        result = executor.run(_context(max_total_tokens=100), "hello")

        # 达到上限（>=）后不再发起第二次 LLM 调用。
        assert mc.stream_chat.call_count == 1
        assert "预算上限 100" in result.final_output
        assert "累计 token 用量 100" in result.final_output
        assert "缩小任务范围" in result.final_output

    def test_budget_zero_means_unlimited(self):
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = [_tool_round(10_000), _final_round()]
        executor = _make_executor(mc, tool_executor=_tool_executor())

        result = executor.run(_context(max_total_tokens=0), "hello")

        assert mc.stream_chat.call_count == 2
        assert result.final_output == "Done."

    def test_budget_not_reached_allows_next_call(self):
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = [_tool_round(40), _final_round()]
        executor = _make_executor(mc, tool_executor=_tool_executor())

        result = executor.run(_context(max_total_tokens=100), "hello")

        assert mc.stream_chat.call_count == 2
        assert result.final_output == "Done."


class TestSdkPassthrough:
    @staticmethod
    def _llm():
        return ModelClient(
            api_key="mock-key",
            base_url="https://mock.api/v1",
            model_name="mock-model",
        )

    def test_agent_passes_budget_to_runtime(self):
        from floodmind.agent.api import Agent

        agent = Agent(llm=self._llm(), max_total_tokens=1234)

        assert agent.raw._max_total_tokens == 1234

    def test_agent_negative_budget_normalizes_to_unlimited(self):
        from floodmind.agent.api import Agent

        agent = Agent(llm=self._llm(), max_total_tokens=-1)

        assert agent.raw._max_total_tokens == 0
