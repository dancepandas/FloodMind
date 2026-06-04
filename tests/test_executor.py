"""Tests for NativeAgentExecutor core loop."""

from unittest.mock import MagicMock, patch

import pytest

from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import AgentResult, ModelEvent, RunContext, ToolCall


class TestNativeAgentExecutor:
    def _make_executor(self, model_client, tool_executor=None, tools_schema=None, tool_registry=None, max_iterations=5):
        if tool_executor is None:
            tool_executor = MagicMock()
        if tool_registry is None:
            from floodmind.agent.runtime.contracts.tools import ToolSpec
            reg = MagicMock()
            reg.get.return_value = None
            reg.all.return_value = []
            reg.tools_schema.return_value = tools_schema or []
            tool_registry = reg
        return NativeAgentExecutor(
            model_client=model_client,
            tool_executor=tool_executor,
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=max_iterations,
            system_prompt="test prompt",
            tools_schema=tools_schema,
            tool_registry=tool_registry,
        )

    def _make_context(self):
        return RunContext(
            session_id="test-session",
            user_text="hello",
            output_dir="/tmp/test-out",
            upload_dir="/tmp/test-up",
        )

    def test_executor_returns_final_answer_without_tool_calls(self):
        """Agent loops ends when LLM returns text without tool calls."""
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = [
            ModelEvent(type="token", content="Hello, how can I help?"),
            ModelEvent(type="done"),
        ]
        executor = self._make_executor(mc, tools_schema=[])
        result = executor.run(self._make_context(), "hello")

        assert isinstance(result, AgentResult)
        assert "Hello" in result.final_output
        assert not result.is_timeout

    def test_executor_calls_tools_and_resumes_loop(self):
        """Agent loop executes tool call then continues."""
        mc = MagicMock(spec=ModelClient)
        # First iteration: tool call
        # Second iteration: final text
        mc.stream_chat.side_effect = [
            [
                ModelEvent(
                    type="tool_call_done",
                    tool_call=ToolCall(id="t1", name="test_tool", arguments={"key": "val"}),
                ),
                ModelEvent(type="done"),
            ],
            [
                ModelEvent(type="token", content="Done with tool."),
                ModelEvent(type="done"),
            ],
        ]

        tool_executor = MagicMock()
        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="test_tool", content="tool output ok", status="completed"
        )

        executor = self._make_executor(
            mc,
            tool_executor=tool_executor,
            tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
        )
        result = executor.run(self._make_context(), "call tool")

        assert tool_executor.execute.called
        assert "Done" in result.final_output

    def test_executor_hits_max_iterations(self):
        """Agent loop stops when max_iterations reached."""
        mc = MagicMock(spec=ModelClient)
        tool_call_events = [
            ModelEvent(
                type="tool_call_done",
                tool_call=ToolCall(id="t1", name="test_tool", arguments={"k": "v"}),
            ),
            ModelEvent(type="done"),
        ]
        mc.stream_chat.return_value = tool_call_events

        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="test_tool", content="ok", status="completed"
        )

        executor = self._make_executor(
            mc,
            tool_executor=tool_executor,
            tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
            max_iterations=3,
        )
        result = executor.run(self._make_context(), "loop forever")

        # Should stop after max_iterations API calls
        assert tool_executor.execute.call_count == 3

    def test_executor_abort_check(self):
        """Agent loop stops when abort_check returns True."""
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = []

        executor = self._make_executor(mc, tools_schema=[])
        ctx = self._make_context()
        ctx.abort_check = lambda: True

        result = executor.run(ctx, "hello")
        assert "中断" in result.final_output

    def test_executor_consecutive_failure_detection(self):
        """Tool fails 5 times consecutively → forced termination."""
        mc = MagicMock(spec=ModelClient)
        tool_call_events = [
            ModelEvent(
                type="tool_call_done",
                tool_call=ToolCall(id="t1", name="test_tool", arguments={"k": "v"}),
            ),
            ModelEvent(type="done"),
        ]
        mc.stream_chat.return_value = tool_call_events

        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="test_tool", content="错误: something wrong", status="error"
        )

        executor = self._make_executor(
            mc,
            tool_executor=tool_executor,
            tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
            max_iterations=10,
        )
        result = executor.run(self._make_context(), "failing task")

        # Should have stopped before max_iterations due to consecutive failures
        assert tool_executor.execute.call_count < 10
        assert tool_executor.execute.call_count >= 5
