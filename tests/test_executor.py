"""Tests for NativeAgentExecutor core loop."""

from unittest.mock import MagicMock, patch

import pytest

from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import (
    AgentResult, InvalidToolCall, ModelEvent, RunContext, TerminalReason, ToolCall,
)
from floodmind.agent.runtime.reducer import initial_run_state


class TestNativeAgentExecutor:
    def _make_executor(self, model_client, tool_executor=None, tools_schema=None, tool_registry=None, tool_loader=None, max_iterations=5):
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
            tool_loader=tool_loader,
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

    def test_attempt_started_payload_uses_envelope_scope_for_attempt_id(self):
        mc = MagicMock(spec=ModelClient)
        mc.model_name = "test-model"
        mc.stream_chat.return_value = [ModelEvent(type="token", content="done"), ModelEvent(type="done")]
        authority = MagicMock()
        authority.replay.return_value = initial_run_state("run-test")
        executor = self._make_executor(mc, tools_schema=[])
        executor._journal_authority = authority

        executor.run(self._make_context(), "hello")

        started = next(
            call for call in authority.emit.call_args_list
            if call.args[0] == "model.attempt.started"
        )
        assert started.args[1] == {
            "model": "test-model",
            "iteration": 0,
            "messages_count": 2,
        }
        assert started.kwargs["attempt_id"].startswith("attempt_")

    def test_is_retryable_error_recognizes_peer_closed_connection(self):
        """流式中断（peer closed connection / chunked read / remote protocol）可重试。"""
        from floodmind.agent.native.retry import is_retryable_error

        cases = [
            "流式输出异常: peer closed connection without sending complete message body (incomplete chunked read)",
            "remote protocol error: connection lost",
            "chunked read error: peer disconnected",
        ]
        for msg in cases:
            assert is_retryable_error(RuntimeError(msg)), msg

    def test_is_retryable_error_inspects_cause_chain(self):
        """openai.APIConnectionError str() 恒为 "Connection error."，真实原因在 __cause__。"""
        import httpx
        import openai
        from floodmind.agent.native.retry import is_retryable_error

        req = httpx.Request("POST", "http://example.com")
        try:
            try:
                raise httpx.RemoteProtocolError(
                    "peer closed connection without sending complete message body (incomplete chunked read)"
                )
            except Exception as orig:
                raise openai.APIConnectionError(message="Connection error.", request=req) from orig
        except openai.APIConnectionError as e:
            assert is_retryable_error(e)  # 递归 __cause__ → peer closed 命中
        # 无 cause 的 APIConnectionError 不误判
        assert not is_retryable_error(openai.APIConnectionError(message="Connection error.", request=req))

    def test_executor_retries_connection_stage_apiconnectionerror(self):
        """create() 连接阶段 APIConnectionError（真实原因在 __cause__）→ executor 重试。"""
        import httpx
        import openai

        mc = MagicMock(spec=ModelClient)
        calls = {"n": 0}
        req = httpx.Request("POST", "http://example.com")

        def stream_chat(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                try:
                    try:
                        raise httpx.RemoteProtocolError(
                            "peer closed connection without sending complete message body (incomplete chunked read)"
                        )
                    except Exception as orig:
                        raise openai.APIConnectionError(message="Connection error.", request=req) from orig
                except openai.APIConnectionError as e:
                    raise e
            return iter([
                ModelEvent(type="token", content="Hello world"),
                ModelEvent(type="done"),
            ])

        mc.stream_chat.side_effect = stream_chat
        executor = self._make_executor(mc, tools_schema=[])
        result = executor.run(self._make_context(), "hello")

        assert calls["n"] == 2  # 连接失败后自动重试了一次
        assert "Hello world" in result.final_output
        assert not result.is_timeout

    def test_executor_retries_stream_error_on_peer_closed_connection(self):
        """流式中断（SDK 包装成 error 事件）被识别为可重试 → executor 自动重试 LLM 请求。"""
        mc = MagicMock(spec=ModelClient)
        calls = {"n": 0}

        def stream_chat(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # 第一次：中途断流，SDK 包装成"流式输出异常" error 事件
                return iter([
                    ModelEvent(type="token", content="Hel"),
                    ModelEvent(type="error", content="流式输出异常: peer closed connection without sending complete message body (incomplete chunked read)"),
                ])
            # 第二次：成功
            return iter([
                ModelEvent(type="token", content="Hello world"),
                ModelEvent(type="done"),
            ])

        mc.stream_chat.side_effect = stream_chat
        executor = self._make_executor(mc, tools_schema=[])
        observed = []
        executor.event_bus.add_listener(observed.append)
        result = executor.run(self._make_context(), "hello")

        assert calls["n"] == 2  # 断流后自动重试了一次
        assert "Hello world" in result.final_output
        assert not result.is_timeout
        assert "".join(
            event.get("content", "") for event in observed if event.get("type") == "answer_delta"
        ) == "Hello world"

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

    def test_malformed_tool_json_returns_retry_feedback_without_execution(self):
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = [
            [
                ModelEvent(
                    type="invalid_tool_call",
                    content="bad json",
                    invalid_tool_call=InvalidToolCall(
                        id="bad1", name="danger", raw_arguments='{"target":', error="bad json"
                    ),
                ),
                ModelEvent(type="done", terminal_reason=TerminalReason.from_raw("tool_calls")),
            ],
            [
                ModelEvent(type="token", content="Recovered"),
                ModelEvent(type="done", terminal_reason=TerminalReason.from_raw("stop")),
            ],
        ]
        tool_executor = MagicMock()
        executor = self._make_executor(mc, tool_executor=tool_executor, tools_schema=[])
        result = executor.run(self._make_context(), "call it")

        tool_executor.execute.assert_not_called()
        assert result.final_output == "Recovered"
        retry_messages = mc.stream_chat.call_args_list[1].kwargs["messages"]
        assert any("参数 JSON 无法解析" in str(message) for message in retry_messages)

    @pytest.mark.parametrize("reason", ["content_filter", "refusal", "pause_turn", "aborted"])
    def test_non_success_finish_reasons_are_not_normal_completion(self, reason):
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = [
            ModelEvent(type="token", content="partial"),
            ModelEvent(type="done", terminal_reason=TerminalReason.from_raw(reason)),
        ]
        memory = MagicMock()
        executor = self._make_executor(mc, tools_schema=[])
        executor._memory = memory
        result = executor.run(self._make_context(), "hello")

        assert result.final_output != "partial"
        memory.add_assistant_round.assert_not_called()

    def test_max_tokens_continues_once_then_completes(self):
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = [
            [ModelEvent(type="token", content="first "),
             ModelEvent(type="done", terminal_reason=TerminalReason.from_raw("length"))],
            [ModelEvent(type="token", content="second"),
             ModelEvent(type="done", terminal_reason=TerminalReason.from_raw("stop"))],
        ]
        executor = self._make_executor(mc, tools_schema=[])
        result = executor.run(self._make_context(), "hello")
        assert result.final_output == "first second"
        assert mc.stream_chat.call_count == 2

    def test_executor_appends_provider_assistant_snapshot_for_tool_calls(self):
        """工具调用轮优先使用 ModelClient 给出的 provider 原生 assistant message。"""
        mc = MagicMock(spec=ModelClient)
        snapshot = {
            "role": "assistant",
            "content": "我来查天气",
            "reasoning_content": "需要调用天气工具",
            "reasoning_details": [{"type": "reasoning.text", "text": "需要调用天气工具"}],
            "tool_calls": [{
                "id": "t1",
                "type": "function",
                "function": {"name": "test_tool", "arguments": '{"key":"val"}'},
            }],
        }
        mc.stream_chat.side_effect = [
            [
                ModelEvent(type="reasoning", content="需要调用天气工具"),
                ModelEvent(
                    type="tool_call_done",
                    tool_call=ToolCall(id="t1", name="test_tool", arguments={"key": "val"}),
                ),
                ModelEvent(type="assistant_message_done", raw={"message": snapshot, "provider": "minimax"}),
                ModelEvent(type="done"),
            ],
            [ModelEvent(type="token", content="Done."), ModelEvent(type="done")],
        ]

        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="test_tool", content="tool output", status="completed"
        )

        executor = self._make_executor(
            mc,
            tool_executor=tool_executor,
            tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
        )
        executor.run(self._make_context(), "call tool")

        second_call_messages = mc.stream_chat.call_args_list[1].kwargs["messages"]
        assert snapshot in second_call_messages

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
        """Tool fails 5 times consecutively → forced termination.

        Note: DOOM LOOP detection fires first at 3 calls with same arguments,
        so the effective threshold here is 3 (the lower bound).
        """
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

        # DOOM LOOP (same args × 3) triggers before consecutive failure (× 5)
        assert tool_executor.execute.call_count < 10
        assert tool_executor.execute.call_count >= 3

    def test_executor_doom_loop_same_args_even_on_success(self):
        """连续 3 次相同工具+相同参数 → DOOM LOOP 检测触发，即使结果成功。"""
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
        # All calls return success — DOOM LOOP still detects same args
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="test_tool", content="ok", status="completed"
        )

        executor = self._make_executor(
            mc,
            tool_executor=tool_executor,
            tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
            max_iterations=10,
        )
        result = executor.run(self._make_context(), "looping task")

        # DOOM LOOP triggers at 3: stops before max_iterations (10)
        assert tool_executor.execute.call_count < 10
        assert tool_executor.execute.call_count == 3

    def test_executor_consecutive_failure_without_doom_loop(self):
        """连续失败但不触发 DOOM LOOP（不同参数），按连续失败检测。"""
        mc = MagicMock(spec=ModelClient)
        # Each iteration uses different arguments — DOOM LOOP won't fire
        # but consecutive failure counter will
        calls = []
        def make_stream(*a, **kw):
            idx = len(calls) + 1
            calls.append(1)
            return [
                ModelEvent(type="tool_call_done",
                           tool_call=ToolCall(id=f"t{idx}", name="test_tool", arguments={"k": idx})),
                ModelEvent(type="done"),
            ]
        mc.stream_chat.side_effect = make_stream

        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t", name="test_tool", content="错误: fail", status="error"
        )

        executor = self._make_executor(
            mc,
            tool_executor=tool_executor,
            tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
            max_iterations=10,
        )
        result = executor.run(self._make_context(), "failing task")

        # Consecutive failure (5) fires: stops before max_iterations
        assert tool_executor.execute.call_count < 10
        assert tool_executor.execute.call_count == 5
    def test_progressive_loader_filters_request_tools_and_blocks_unloaded_call(self):
        """progressive 模式只暴露 loaded tools，未加载工具不能直接执行。"""
        from floodmind.agent.native.tool_loading import ToolLoader, ToolLoadingConfig
        from floodmind.agent.runtime.contracts.tools import ToolSpec

        read_spec = ToolSpec(
            name="Read",
            description="读取文件",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=lambda **kw: "ok",
        )
        reg = MagicMock()
        reg.get.side_effect = lambda name: read_spec if name == "Read" else None
        reg.all.return_value = [read_spec]
        reg.tools_schema.return_value = [read_spec.to_openai_tool()]

        loader = ToolLoader(ToolLoadingConfig(mode="progressive", core_tools=[]))
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = [
            ModelEvent(type="tool_call_done", tool_call=ToolCall(id="t1", name="Read", arguments={"path": "a.txt"})),
            ModelEvent(type="done"),
        ]
        tool_executor = MagicMock()
        executor = self._make_executor(
            mc,
            tool_executor=tool_executor,
            tools_schema=[read_spec.to_openai_tool()],
            tool_registry=reg,
            tool_loader=loader,
        )
        result = executor.run(self._make_context(), "read file")

        first_tools = mc.stream_chat.call_args.kwargs["tools"]
        assert first_tools is None
        tool_executor.execute.assert_not_called()
        assert result.tool_results[0].status == "error"
        assert "未加载" in result.tool_results[0].content

    def test_progressive_loader_exposes_tool_after_get_tool_loads_it(self):
        from floodmind.agent.native.tool_loading import ToolLoader, ToolLoadingConfig
        from floodmind.agent.runtime.contracts.tools import ToolSpec

        read_spec = ToolSpec(
            name="Read",
            description="读取文件",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=lambda **kw: "ok",
        )
        reg = MagicMock()
        reg.get.side_effect = lambda name: read_spec if name == "Read" else None
        reg.all.return_value = [read_spec]
        loader = ToolLoader(ToolLoadingConfig(mode="progressive", core_tools=[]))
        loader.get_tool_detail(reg, "Read")

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = [ModelEvent(type="token", content="ok"), ModelEvent(type="done")]
        executor = self._make_executor(mc, tool_registry=reg, tool_loader=loader)
        executor.run(self._make_context(), "hello")

        names = [t["function"]["name"] for t in mc.stream_chat.call_args.kwargs["tools"]]
        assert names == ["Read"]


class TestExecutorPlaceholderStates:
    def _make_context(self):
        return RunContext(
            session_id="test-session",
            user_text="hello",
            output_dir="/tmp/test-out",
            upload_dir="/tmp/test-up",
        )

    def _make_executor(self, tool_executor=None, context_compressor=None, context_window=32000):
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = []
        return NativeAgentExecutor(
            model_client=mc,
            tool_executor=tool_executor or MagicMock(),
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=5,
            system_prompt="test",
            tools_schema=[],
            context_compressor=context_compressor,
            context_window=context_window,
        )

    def _make_state(self, status):
        from floodmind.agent.native.types import AgentLoopState
        return AgentLoopState(
            session_id="test-session",
            run_id="run-1",
            status=status,
        )

    def test_context_compress_reduces_messages(self):
        from floodmind.agent.native.context_compressor import ContextCompressor
        from floodmind.agent.native.types import AgentLoopState

        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="summary")
        compressor = ContextCompressor(model_client=llm, head_keep=1, tail_keep=1, trigger_threshold=0.5)
        executor = self._make_executor(context_compressor=compressor, context_window=100)
        state = AgentLoopState(
            session_id="test-session",
            run_id="run-1",
            status="context_compress",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "x" * 200},
                {"role": "assistant", "content": "y" * 200},
                {"role": "user", "content": "a" * 200},
                {"role": "assistant", "content": "b" * 200},
                {"role": "user", "content": "z" * 200},
            ],
        )
        original_len = len(state.messages)
        new_state = executor._on_context_compress(state, self._make_context())
        assert new_state.status == "awaiting_llm"
        assert len(new_state.messages) < original_len

    def test_awaiting_llm_triggers_compression(self):
        from floodmind.agent.native.context_compressor import ContextCompressor
        from floodmind.agent.native.types import AgentLoopState

        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="summary")
        compressor = ContextCompressor(model_client=llm, head_keep=1, tail_keep=1, trigger_threshold=0.5)
        executor = self._make_executor(context_compressor=compressor, context_window=100)
        state = AgentLoopState(
            session_id="test-session",
            run_id="run-1",
            status="awaiting_llm",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "x" * 200},
                {"role": "assistant", "content": "y" * 200},
                {"role": "user", "content": "a" * 200},
                {"role": "assistant", "content": "b" * 200},
                {"role": "user", "content": "z" * 200},
            ],
        )
        new_state = executor._on_awaiting_llm(state, self._make_context())
        assert new_state.status == "context_compress"


class TestAwaitingPermissionRecovery:
    def _make_context(self):
        return RunContext(
            session_id="test-session",
            user_text="hello",
            output_dir="/tmp/test-out",
            upload_dir="/tmp/test-up",
        )

    def _make_executor(self, tool_executor=None, context_compressor=None, context_window=32000):
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = []
        return NativeAgentExecutor(
            model_client=mc,
            tool_executor=tool_executor or MagicMock(),
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=5,
            system_prompt="test",
            tools_schema=[],
            context_compressor=context_compressor,
            context_window=context_window,
        )

    def test_real_denial_does_not_reissue(self):
        from floodmind.agent.native.types import AgentLoopState
        from floodmind.agent.runtime.contracts.permissions import PermissionAskRequest, PermissionAskResponse
        from floodmind.agent.runtime.services.ask_service import AskService, set_ask_service

        ask_svc = AskService()
        set_ask_service(ask_svc)
        ask_svc.set_emit_fn(lambda e: None, session_id="test-session")

        ask_id = ask_svc.start_ask(PermissionAskRequest(
            session_id="test-session",
            call_id="c1",
            tool_name="Write",
            reason="写文件",
            tool_input={"path": "x.txt"},
        ), journal_authority=MagicMock())
        ask_svc.respond(PermissionAskResponse(session_id="test-session", ask_id=ask_id, approved=False))

        tool_executor = MagicMock()
        executor = self._make_executor(tool_executor=tool_executor)

        state = AgentLoopState(
            session_id="test-session",
            run_id="run-1",
            status="awaiting_permission",
            pending_ask_id=ask_id,
            pending_tool_calls=[ToolCall(id="c1", name="Write", arguments={"path": "x.txt"})],
        )
        new_state = executor._on_awaiting_permission(state, self._make_context())

        assert new_state.status == "awaiting_llm"
        assert new_state.pending_ask_id is None
        assert new_state.pending_tool_calls == []
        tool_executor.execute.assert_not_called()

    def test_approved_ask_uses_normal_journal_memory_and_artifact_path(self):
        from floodmind.agent.native.types import AgentLoopState
        from floodmind.agent.runtime.contracts.permissions import PermissionAskRequest, PermissionAskResponse
        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        from floodmind.agent.runtime.services.ask_service import AskService, set_ask_service

        ask_svc = AskService()
        set_ask_service(ask_svc)
        ask_svc.set_emit_fn(lambda e: None, session_id="test-session")
        ask_id = ask_svc.start_ask(PermissionAskRequest(
            session_id="test-session", call_id="c1", tool_name="Write",
            reason="write", tool_input={"path": "x.txt"},
        ), journal_authority=MagicMock())
        ask_svc.respond(PermissionAskResponse(session_id="test-session", ask_id=ask_id, approved=True))

        result = NativeToolResult(
            tool_call_id="c1", name="Write", content="written", status="completed",
            artifacts=["/tmp/x.txt"],
        )
        tool_executor = MagicMock()
        tool_executor.execute.return_value = result
        authority = MagicMock()
        executor = self._make_executor(tool_executor=tool_executor)
        executor._journal_authority = authority
        state = AgentLoopState(
            session_id="test-session", run_id="run-1", status="awaiting_permission",
            pending_ask_id=ask_id,
            pending_tool_calls=[ToolCall(id="c1", name="Write", arguments={"path": "x.txt"})],
        )

        state = executor._on_awaiting_permission(state, self._make_context())
        state = executor._on_awaiting_tool(state, self._make_context())

        event_types = [call.args[0] for call in authority.emit.call_args_list]
        assert event_types == ["tool.execution.completed", "model.attempt.completed"]
        assert state.artifacts == ["/tmp/x.txt"]
        assert "written" in str(state.messages[-1])

    def test_lost_ask_id_reissues_ask(self):
        from floodmind.agent.native.types import AgentLoopState
        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        from floodmind.agent.runtime.services.ask_service import AskService, set_ask_service

        ask_svc = AskService()
        set_ask_service(ask_svc)
        ask_svc.set_emit_fn(lambda e: None, session_id="test-session")

        tool_executor = MagicMock()
        awaiting_result = NativeToolResult(
            tool_call_id="c1",
            name="Write",
            content="等待用户确认",
            status="awaiting_permission",
            metadata={"ask_id": "ask-new"},
        )
        tool_executor.execute.return_value = awaiting_result

        executor = self._make_executor(tool_executor=tool_executor)

        state = AgentLoopState(
            session_id="test-session",
            run_id="run-1",
            status="awaiting_permission",
            pending_ask_id="ask-lost",
            pending_tool_calls=[ToolCall(id="c1", name="Write", arguments={"path": "x.txt"})],
        )
        new_state = executor._on_awaiting_permission(state, self._make_context())

        assert new_state.status == "awaiting_permission"
        assert new_state.pending_ask_id == "ask-new"
        tool_executor.execute.assert_called_once()
