"""Guardrail 输入/输出校验：拦截、重试、短路、零差异。

对标 openai-agents 的 InputGuardrail/OutputGuardrail，适配同步 runtime：
- 输入 guardrail 在每次 stream_chat 调用前跑，tripwire → run failed，不调 LLM；
- 输出 guardrail 在 final_output 产出时跑，首次 tripwire 注入修正提示自动重试
  一次，二次 tripwire 才 failed；
- 多 guardrail 顺序执行，任一 tripwire 即停（短路）；
- 无 guardrail 时行为与现状零差异。
"""

import copy
from unittest.mock import MagicMock

from floodmind.agent.guardrail import GuardrailResult
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import AgentLoopState, ModelEvent, RunContext, TerminalReason


def _blocking_input_guardrail(name="block_input"):
    def guardrail(messages):
        return GuardrailResult(tripwire_triggered=True, message=f"{name}: 输入被拦截")
    guardrail.__name__ = name
    return guardrail


def _passing_guardrail(name):
    def guardrail(_arg):
        return GuardrailResult(tripwire_triggered=False)
    guardrail.__name__ = name
    return guardrail


def _blocking_output_guardrail(name="block_output"):
    def guardrail(output, state=None):
        return GuardrailResult(tripwire_triggered=True, message=f"{name}: 输出被拦截")
    guardrail.__name__ = name
    return guardrail


def _make_executor(mc, input_guardrails=None, output_guardrails=None, max_iterations=5,
                   tool_executor=None):
    reg = MagicMock()
    reg.get.return_value = None
    reg.all.return_value = []
    reg.tools_schema.return_value = []
    return NativeAgentExecutor(
        model_client=mc,
        tool_executor=tool_executor or MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=max_iterations,
        system_prompt="sys",
        tools_schema=[],
        tool_registry=reg,
        input_guardrails=input_guardrails,
        output_guardrails=output_guardrails,
    )


def _context():
    return RunContext(
        session_id="s", user_text="hello", output_dir="/tmp/o", upload_dir="/tmp/u",
    )


def _state():
    return AgentLoopState(
        session_id="s", run_id="run-1", status="created",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}],
    )


def _token_done(text="ok"):
    return [ModelEvent(type="token", content=text), ModelEvent(type="done")]


class TestInputGuardrail:
    def test_input_tripwire_fails_run_without_llm_call(self):
        """输入 tripwire：run failed，final_output 为拦截消息，LLM 一次都不调。"""
        mc = MagicMock(spec=ModelClient)
        executor = _make_executor(mc, input_guardrails=[_blocking_input_guardrail()])

        result = executor.run(_context(), "hello")

        assert result.final_output == "block_input: 输入被拦截"
        assert not result.is_timeout
        mc.stream_chat.assert_not_called()

    def test_input_guardrail_runs_before_every_llm_call(self):
        """输入 guardrail 对每次 LLM 调用都生效：第一次放行，工具回流后第二次被拦。

        输入变化（工具结果注入）后同样要过 guardrail。
        """
        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        from floodmind.agent.native.types import ToolCall

        calls = []

        def make_stream(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return [
                    ModelEvent(type="tool_call_done",
                               tool_call=ToolCall(id="t1", name="t", arguments={})),
                    ModelEvent(type="done"),
                ]
            return _token_done("done")

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = make_stream
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="t", content="tool output", status="completed",
        )

        guardrail_calls = []

        def conditional_guardrail(messages):
            guardrail_calls.append(1)
            if len(guardrail_calls) >= 2:
                return GuardrailResult(tripwire_triggered=True, message="二次输入被拦")
            return GuardrailResult(tripwire_triggered=False)
        conditional_guardrail.__name__ = "conditional"

        executor = _make_executor(
            mc, input_guardrails=[conditional_guardrail],
            tool_executor=tool_executor,
        )
        result = executor.run(_context(), "hello")

        # 第一次调用放行（stream 发生 1 次）；工具回流后第二次调用前被拦，
        # stream_chat 不再发生——fail-closed。
        assert len(calls) == 1, "第二次调用应在 stream 前被拦截"
        assert "二次输入被拦" in result.final_output
        assert "done" not in result.final_output

    def test_multiple_input_guardrails_short_circuit(self):
        """多 guardrail 顺序执行，第一个 tripwire 即停，后面不再跑。"""
        mc = MagicMock(spec=ModelClient)
        second_called = []

        def first(messages):
            return GuardrailResult(tripwire_triggered=True, message="first 拦截")
        first.__name__ = "first"

        def second(messages):
            second_called.append(1)
            return GuardrailResult(tripwire_triggered=False)
        second.__name__ = "second"

        executor = _make_executor(mc, input_guardrails=[first, second])
        result = executor.run(_context(), "hello")

        assert "first 拦截" in result.final_output
        assert not second_called
        mc.stream_chat.assert_not_called()

    def test_input_guardrail_can_replace_input(self):
        """replaced_input 生效：改写后的消息列表送入 LLM。"""
        captured = []

        def sanitize(messages):
            msgs = copy.deepcopy(messages)
            for m in msgs:
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    m["content"] = m["content"].replace("机密", "[已脱敏]")
            return GuardrailResult(tripwire_triggered=False, replaced_input=msgs)
        sanitize.__name__ = "sanitize"

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done()
        mc.stream_chat.side_effect = lambda *a, **kw: (
            captured.append(copy.deepcopy(kw["messages"])), _token_done())[1]

        executor = _make_executor(mc, input_guardrails=[sanitize])
        executor.run(_context(), "机密数据 hello")

        assert captured[0][-1]["content"] == "[已脱敏]数据 hello"


class TestOutputGuardrail:
    def test_output_tripwire_retries_once_then_fails(self):
        """输出首触：注入修正提示重试一次；二次触发：failed。"""
        attempts = []

        def always_block(output, state=None):
            attempts.append(1)
            return GuardrailResult(tripwire_triggered=True, message="输出不合规")
        always_block.__name__ = "always_block"

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("答案")
        executor = _make_executor(mc, output_guardrails=[always_block], max_iterations=10)

        result = executor.run(_context(), "hello")

        assert len(attempts) == 2, "恰好重试一次"
        assert "输出不合规" in result.final_output
        assert result.final_output and result.final_output.startswith("输出不合规") or "输出不合规" in result.final_output

    def test_output_retry_after_correction_succeeds(self):
        """首次触发后模型自我修正：第二次输出合规 → completed，重试消耗一次迭代。"""
        outputs = iter(["坏答案", "好答案"])

        def check(output, state=None):
            return GuardrailResult(
                tripwire_triggered="坏" in output,
                message="输出含坏词",
            )
        check.__name__ = "check"

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = lambda *a, **kw: _token_done(next(outputs))
        executor = _make_executor(mc, output_guardrails=[check], max_iterations=10)

        result = executor.run(_context(), "hello")

        assert result.final_output == "好答案"

    def test_output_guardrail_skipped_on_tool_call_rounds(self):
        """工具调用轮次（非 stop 终态）不触发输出 guardrail，只在最终答案时校验。"""
        output_checks = []

        def check(output, state=None):
            output_checks.append(output)
            return GuardrailResult(tripwire_triggered=False)
        check.__name__ = "check"

        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        from floodmind.agent.native.types import ToolCall

        def make_stream(*args, **kwargs):
            return [
                ModelEvent(type="tool_call_done",
                           tool_call=ToolCall(id="t1", name="t", arguments={})),
                ModelEvent(type="done"),
            ]

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = make_stream
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="t", content="ok", status="completed",
        )
        executor = _make_executor(mc, output_guardrails=[check], tool_executor=tool_executor)

        executor.run(_context(), "hello")

        assert not output_checks, "工具轮次的空输出不应送输出 guardrail"


class TestNoGuardrailZeroDiff:
    def test_no_guardrails_behavior_unchanged(self):
        """未配置 guardrail 时一切照旧。"""
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("正常回答")
        executor = _make_executor(mc)

        result = executor.run(_context(), "hello")

        assert result.final_output == "正常回答"


class TestSdkAgentWiring:
    @staticmethod
    def _sdk_llm():
        """SDK Agent 走线程+队列真实 runtime，mock 需补齐 runtime 触碰的属性。

        spec=ModelClient 缺 enable_thinking 等属性时 run 线程抛 AttributeError，
        主线程永久阻塞在队列上。
        """
        llm = MagicMock(spec=ModelClient)
        llm.enable_thinking = False
        llm.model_name = "test-model"
        llm.classify_error.return_value = None
        return llm

    def test_sdk_agent_accepts_guardrails(self):
        """公共 Agent 构造参数接线：guardrail 触发时 run 失败且不调 LLM。"""
        from floodmind import Agent
        from floodmind.agent.guardrail import GuardrailResult

        llm = self._sdk_llm()

        def block(messages):
            return GuardrailResult(tripwire_triggered=True, message="sdk 拦截")
        block.__name__ = "block"

        agent = Agent(llm=llm, session_id="gr-sdk", input_guardrails=[block])
        result = agent.run("hello")

        assert "sdk 拦截" in result
        llm.stream_chat.assert_not_called()


class TestReviewFixes:
    """code-review 发现的修复回归：重试预算 per-run、续写过闸、arity 适配、归因。"""

    def test_retry_budget_resets_per_run(self):
        """#1：同一 executor 的第二次 run 仍享有一次输出重试（标志随 state 走）。"""
        attempts = []

        def always_block(output, state=None):
            attempts.append(1)
            return GuardrailResult(tripwire_triggered=True, message="不合规")
        always_block.__name__ = "always_block"

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("答案")
        executor = _make_executor(mc, output_guardrails=[always_block], max_iterations=10)

        executor.run(_context(), "第一次")
        first_run_attempts = len(attempts)
        executor.run(_context(), "第二次")

        assert first_run_attempts == 2, "第一次 run：首触+重试 = 2 次校验"
        assert len(attempts) == 4, "第二次 run 仍应重试一次（2+2），而非首次触发即失败"

    def test_output_guardrail_covers_continuation(self):
        """#2：max_tokens 续写拼接后的全量输出要过输出 guardrail。

        第一轮答案违规但被 max_tokens 截断，续写干净——旧实现对续写片段
        单独校验会放行；全量校验应拦截。
        """
        mc = MagicMock(spec=ModelClient)
        rounds = []

        def make_stream(*args, **kwargs):
            rounds.append(1)
            if len(rounds) == 1:
                # 违规 + 截断：模型输出违规内容且 finish_reason=max_tokens
                return [
                    ModelEvent(type="token", content="违规内容"),
                    ModelEvent(type="done",
                               terminal_reason=TerminalReason.from_raw("max_tokens")),
                ]
            return _token_done("干净续写")

        mc.stream_chat.side_effect = make_stream

        def check_all(output, state=None):
            if "违规" in output:
                return GuardrailResult(tripwire_triggered=True, message="含违规内容")
            return GuardrailResult(tripwire_triggered=False)
        check_all.__name__ = "check_all"

        executor = _make_executor(mc, output_guardrails=[check_all], max_iterations=10)
        result = executor.run(_context(), "hello")

        # 修复后语义：重试轮清掉 stale final_output（违规前缀不再拼回），
        # 续写轮产出干净文本通过全量校验 → 完成。违规内容不得外泄。
        assert "违规" not in result.final_output, "违规内容不得出现在最终输出"
        assert result.final_output == "干净续写"

    def test_single_arg_output_guardrail_supported(self):
        """#4：单参输出 guardrail def g(output) 可用，异常转 fail-closed tripwire。"""
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("答案")

        def one_arg(output):
            return GuardrailResult(tripwire_triggered=True, message="单参拦截")
        one_arg.__name__ = "one_arg"

        executor = _make_executor(mc, output_guardrails=[one_arg], max_iterations=10)
        result = executor.run(_context(), "hello")
        assert "单参拦截" in result.final_output

    def test_guardrail_exception_fails_closed(self):
        """#4b：guardrail 抛异常转为 tripwire（fail-closed），不炸状态机。"""
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("答案")

        def broken(output, state=None):
            raise RuntimeError("宿主代码故障")
        broken.__name__ = "broken"

        executor = _make_executor(mc, output_guardrails=[broken], max_iterations=10)
        result = executor.run(_context(), "hello")
        assert "宿主代码故障" in result.final_output

    def test_multi_guardrail_event_names_tripped_one(self):
        """#7：多 guardrail 时事件/journal 归因到实际触发者，而非泛名。"""
        events = []

        def pii_check(messages):
            return GuardrailResult(tripwire_triggered=False)
        pii_check.__name__ = "pii_check"

        def url_check(messages):
            return GuardrailResult(tripwire_triggered=True, message="发现外链")
        url_check.__name__ = "url_check"

        mc = MagicMock(spec=ModelClient)
        executor = _make_executor(mc, input_guardrails=[pii_check, url_check])
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: events.append(e)

        result = executor.run(_context(), "hello")

        assert "发现外链" in result.final_output
        gr_events = [e for e in events if e.get("type") == "guardrail_triggered"]
        assert gr_events and gr_events[0]["guardrail"] == "url_check"


class TestReviewRound2Fixes:
    """第二轮 review 修复回归：流式缓冲、出口全覆盖、健壮性、链式组合。"""

    def test_streaming_tokens_buffered_until_guardrail_passes(self):
        """#1：有输出 guardrail 时 token 缓冲到验证通过才放流。"""
        emitted = []
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("答案文本")

        def check(output, state=None):
            return GuardrailResult(tripwire_triggered=False)
        check.__name__ = "check"

        executor = _make_executor(mc, output_guardrails=[check])
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: emitted.append(e)
        executor.event_bus.emit_token = lambda c: emitted.append({"type": "token", "content": c})

        executor.run(_context(), "hello")

        token_events = [e for e in emitted if e.get("type") == "token"]
        assert token_events, "验证通过后缓冲 token 必须放流"
        assert any(e.get("content") == "答案文本" for e in token_events)

    def test_streaming_tokens_suppressed_when_tripped(self):
        """#1b：输出 tripwire 时缓冲 token 永不放流。"""
        emitted = []
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("违规答案")

        def check(output, state=None):
            return GuardrailResult(tripwire_triggered=True, message="拦截")
        check.__name__ = "check"

        executor = _make_executor(mc, output_guardrails=[check], max_iterations=10)
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: emitted.append(e)
        executor.event_bus.emit_token = lambda c: emitted.append({"type": "token", "content": c})

        result = executor.run(_context(), "hello")

        token_events = [e for e in emitted if e.get("type") == "token" and "违规" in str(e.get("content", ""))]
        assert not token_events, "违规内容不得以 token 形式外泄"
        assert "拦截" in result.final_output

    def test_no_guardrail_streams_realtime(self):
        """无 guardrail 时流式行为不变（token 实时 emit）。"""
        emitted = []
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("实时答案")

        executor = _make_executor(mc)
        executor.event_bus = MagicMock()
        executor.event_bus.emit_token = lambda c: emitted.append(c)

        executor.run(_context(), "hello")

        assert "实时答案" in emitted

    def test_continuation_exhausted_exit_guardrailed(self):
        """#2：续写预算耗尽出口（failed）也过输出 guardrail。"""
        mc = MagicMock(spec=ModelClient)
        rounds = []

        def make_stream(*args, **kwargs):
            rounds.append(1)
            return [
                ModelEvent(type="token", content="违规内容"),
                ModelEvent(type="done",
                           terminal_reason=TerminalReason.from_raw("max_tokens")),
            ]

        mc.stream_chat.side_effect = make_stream

        def check(output, state=None):
            if "违规" in output:
                return GuardrailResult(tripwire_triggered=True, message="续写出口拦截")
            return GuardrailResult(tripwire_triggered=False)
        check.__name__ = "check"

        executor = _make_executor(mc, output_guardrails=[check], max_iterations=10)
        result = executor.run(_context(), "hello")

        assert "续写出口拦截" in result.final_output
        assert "违规" not in result.final_output

    def test_max_iterations_fallback_guardrailed(self):
        """#4：max_iterations 兜底出口（回退工具结果原文）也过输出 guardrail。"""
        mc = MagicMock(spec=ModelClient)
        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        from floodmind.agent.native.types import ToolCall

        def make_stream(*args, **kwargs):
            return [
                ModelEvent(type="tool_call_done",
                           tool_call=ToolCall(id="t1", name="t", arguments={})),
                ModelEvent(type="done"),
            ]

        mc.stream_chat.side_effect = make_stream
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="t", content="含密钥的工具结果", status="completed",
        )

        def check(output, state=None):
            if "密钥" in output:
                return GuardrailResult(tripwire_triggered=True, message="兜底出口拦截")
            return GuardrailResult(tripwire_triggered=False)
        check.__name__ = "check"

        executor = _make_executor(
            mc, output_guardrails=[check], tool_executor=tool_executor, max_iterations=1)
        result = executor.run(_context(), "hello")

        assert "兜底出口拦截" in result.final_output

    def test_invalid_return_type_fails_closed(self):
        """#5：guardrail 返回非 GuardrailResult（dict 等）转 tripwire，不炸状态机。"""
        mc = MagicMock(spec=ModelClient)

        def dict_return(messages):
            return {"tripwire_triggered": False}
        dict_return.__name__ = "dict_return"

        executor = _make_executor(mc, input_guardrails=[dict_return])
        result = executor.run(_context(), "hello")

        assert "返回类型非法" in result.final_output
        mc.stream_chat.assert_not_called()

    def test_input_guardrails_chain_compose(self):
        """#7：链式组合——后续 guardrail 收到前一个的 replaced_input。"""
        seen_by_scanner = []

        def redactor(messages):
            msgs = copy.deepcopy(messages)
            for m in msgs:
                if isinstance(m.get("content"), str):
                    m["content"] = m["content"].replace("secret", "[REDACTED]")
            return GuardrailResult(tripwire_triggered=False, replaced_input=msgs)
        redactor.__name__ = "redactor"

        def scanner(messages):
            seen_by_scanner.append(copy.deepcopy(messages))
            for m in messages:
                if isinstance(m.get("content"), str) and "secret" in m["content"]:
                    return GuardrailResult(tripwire_triggered=True, message="扫到 secret")
            return GuardrailResult(tripwire_triggered=False)
        scanner.__name__ = "scanner"

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("ok")
        executor = _make_executor(mc, input_guardrails=[redactor, scanner])
        result = executor.run(_context(), "secret data")

        assert result.final_output == "ok", "脱敏后 scanner 不应触发"
        assert all("secret" not in str(m.get("content", ""))
                   for m in seen_by_scanner[0]), "scanner 收到的必须是脱敏后的列表"

    def test_retry_with_empty_answer_skips_empty_assistant_message(self):
        """#8：空 current_answer 触发重试时不注入空 assistant 消息。"""
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("")

        def check(output, state=None):
            return GuardrailResult(tripwire_triggered=True, message="empty output not allowed")
        check.__name__ = "check"

        executor = _make_executor(mc, output_guardrails=[check], max_iterations=10)
        captured = []
        executor.stream_chat = None  # noqa: 占位，用 side_effect 捕获
        orig = mc.stream_chat.side_effect
        mc.stream_chat.side_effect = lambda *a, **kw: (
            captured.append(copy.deepcopy(kw["messages"])), orig(*a, **kw))[1]

        executor.run(_context(), "hello")

        for messages in captured:
            for m in messages:
                if m.get("role") == "assistant":
                    assert m.get("content") != "" or m.get("tool_calls"), \
                        "不得注入空 assistant 消息"


class TestReviewRound3Fixes:
    """第三轮 review 修复回归：绑定方法 arity、flush 时序、出口语义统一、单发事件。"""

    def test_bound_method_guardrail_works(self):
        """#1：绑定方法单参 guardrail 不再被误判为双参而 fail-closed。"""
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("ok")

        class Svc:
            def check(self, messages):
                return GuardrailResult(tripwire_triggered=False)

        executor = _make_executor(mc, input_guardrails=[Svc().check])
        result = executor.run(_context(), "hello")
        assert result.final_output == "ok"

    def test_tool_round_narrative_buffered_until_final_verdict(self):
        """有输出 guardrail 时工具轮叙述跨轮缓冲，最终 verdict 通过才放流。"""
        emitted = []
        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        from floodmind.agent.native.types import ToolCall

        call_count = []

        def make_stream(*args, **kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                return [
                    ModelEvent(type="token", content="先说两句"),
                    ModelEvent(type="tool_call_done",
                               tool_call=ToolCall(id="t1", name="t", arguments={})),
                    ModelEvent(type="done"),
                ]
            return _token_done("最终回答")

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = make_stream
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="t", content="ok", status="completed")

        def check(output, state=None):
            return GuardrailResult(tripwire_triggered=False)
        check.__name__ = "check"

        executor = _make_executor(mc, output_guardrails=[check], tool_executor=tool_executor)
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: emitted.append(e)
        executor.event_bus.emit_token = lambda c: emitted.append({"type": "token", "content": c})
        executor.event_bus.emit_reasoning = lambda c: emitted.append({"type": "reasoning", "content": c})

        executor.run(_context(), "hello")

        assert any(e.get("content") == "先说两句" for e in emitted if e.get("type") == "token"), \
            "工具轮叙述必须在最终 verdict 通过后放流"
        assert any(e.get("content") == "最终回答" for e in emitted if e.get("type") == "token")

    def test_flush_order_before_llm_step_end(self):
        """#3：缓冲 token 在 llm_step_end 之前放流（时序正确，前端折叠不裂）。"""
        order = []
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("最终答案")

        def check(output, state=None):
            return GuardrailResult(tripwire_triggered=False)
        check.__name__ = "check"

        executor = _make_executor(mc, output_guardrails=[check])
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: order.append(("emit", e.get("type")))
        executor.event_bus.emit_token = lambda c: order.append(("token", c))
        executor.event_bus.emit_reasoning = lambda c: order.append(("reasoning", c))
        executor.event_bus.emit_llm_step_end = \
            lambda **kw: order.append(("llm_step_end", kw.get("reason")))

        executor.run(_context(), "hello")

        types = [t for t, _ in order]
        assert "token" in types, "最终答案轮 token 必须放流"
        assert types.index("token") < types.index("llm_step_end"), \
            "token 必须先于 llm_step_end"

    def test_abort_discards_buffered_output(self):
        """#4：abort 出口丢弃缓冲 token，final_output 不保留未验证的续写残留。"""
        emitted = []
        mc = MagicMock(spec=ModelClient)

        def make_stream(*args, **kwargs):
            return [
                ModelEvent(type="token", content="半截违规"),
                ModelEvent(type="done", terminal_reason=TerminalReason.from_raw("max_tokens")),
            ]

        mc.stream_chat.side_effect = make_stream

        def check(output, state=None):
            return GuardrailResult(tripwire_triggered=False)
        check.__name__ = "check"

        executor = _make_executor(mc, output_guardrails=[check], max_iterations=10)
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: emitted.append(e)
        executor.event_bus.emit_token = lambda c: emitted.append({"type": "token", "content": c})

        ctx = _context()
        ctx.abort_check = lambda: True
        result = executor.run(ctx, "hello")

        assert not any("半截违规" in str(e.get("content", "")) for e in emitted), \
            "abort 时缓冲 token 不得放流"
        assert result.final_output == "任务已被用户中断。"

    def test_max_iterations_trip_is_failed_not_completed(self):
        """#6：max_iterations 兜底出口触发 guardrail 时终态为 failed（语义统一）。"""
        mc = MagicMock(spec=ModelClient)
        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        from floodmind.agent.native.types import ToolCall

        def make_stream(*args, **kwargs):
            return [
                ModelEvent(type="tool_call_done",
                           tool_call=ToolCall(id="t1", name="t", arguments={})),
                ModelEvent(type="done"),
            ]

        mc.stream_chat.side_effect = make_stream
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="t", content="含密钥", status="completed")

        def check(output, state=None):
            if "密钥" in output:
                return GuardrailResult(tripwire_triggered=True, message="兜底拦截")
            return GuardrailResult(tripwire_triggered=False)
        check.__name__ = "check"

        executor = _make_executor(
            mc, output_guardrails=[check], tool_executor=tool_executor, max_iterations=1)
        events = []
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: events.append(e)
        executor.event_bus.emit_token = lambda c: None
        executor.event_bus.emit_reasoning = lambda c: None

        result = executor.run(_context(), "hello")

        assert "兜底拦截" in result.final_output
        gr = [e for e in events if e.get("type") == "guardrail_triggered"]
        assert len(gr) == 1, "出口触发只发一次事件"
        assert gr[0]["retrying"] is False

    def test_trip_emits_single_event(self):
        """#7：一次触发只发一个 guardrail_triggered（不再 helper+调用方双发）。"""
        attempts = []

        def always_block(output, state=None):
            attempts.append(1)
            return GuardrailResult(tripwire_triggered=True, message="不合规")
        always_block.__name__ = "always_block"

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = _token_done("答案")
        executor = _make_executor(mc, output_guardrails=[always_block], max_iterations=10)
        events = []
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: events.append(e)
        executor.event_bus.emit_token = lambda c: None
        executor.event_bus.emit_reasoning = lambda c: None

        executor.run(_context(), "hello")

        gr = [e for e in events if e.get("type") == "guardrail_triggered"]
        assert len(gr) == 2, "首触(retrying=True) + 二触(retrying=False) 各一次"
        assert gr[0]["retrying"] is True and gr[1]["retrying"] is False


class TestCrossCapabilityReviewFixes:
    """独立 review 发现：非 stop 正常终态 + 重试轮生命周期/usage。"""

    def test_end_turn_completion_still_guardrailed(self):
        """TerminalReason(code=completed, raw=end_turn) 也必须走输出 guardrail。"""
        emitted = []
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.return_value = [
            ModelEvent(type="token", content="SECRET"),
            ModelEvent(type="done", terminal_reason=TerminalReason.from_raw("end_turn")),
        ]

        def block(output, state=None):
            return GuardrailResult(tripwire_triggered=True, message="blocked end_turn")
        block.__name__ = "block"

        executor = _make_executor(mc, output_guardrails=[block], max_iterations=10)
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: emitted.append(e)
        executor.event_bus.emit_token = lambda c: emitted.append({"type": "token", "content": c})
        executor.event_bus.emit_reasoning = lambda c: None

        result = executor.run(_context(), "hello")

        assert "blocked end_turn" in result.final_output
        assert not any("SECRET" in str(e.get("content", "")) for e in emitted), \
            "非 stop 正常终态也不得提前泄漏"
        assert any(e.get("type") == "guardrail_triggered" for e in emitted)

    def test_retry_round_closes_llm_step_and_counts_usage(self):
        """输出 tripwire 重试轮也必须有 step_end，且 token usage 不漏记。"""
        events = []
        outputs = iter(["坏答案", "好答案"])
        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = lambda *a, **kw: [
            ModelEvent(type="token", content=next(outputs)),
            ModelEvent(type="usage", raw={
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
            }),
            ModelEvent(type="done"),
        ]

        def check(output, state=None):
            return GuardrailResult(tripwire_triggered="坏" in output, message="修正")
        check.__name__ = "check"

        executor = _make_executor(mc, output_guardrails=[check], max_iterations=10)
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: events.append(e)
        executor.event_bus.emit_llm_step_start = lambda **kw: events.append({"type": "start"})
        executor.event_bus.emit_llm_step_end = lambda **kw: events.append({"type": "end"})
        executor.event_bus.emit_token = lambda c: None
        executor.event_bus.emit_reasoning = lambda c: None

        result = executor.run(_context(), "hello")

        assert result.final_output == "好答案"
        assert sum(e.get("type") == "start" for e in events) == 2
        assert sum(e.get("type") == "end" for e in events) == 2, \
            "每个 llm_step_start 必须配对一个 llm_step_end"
        assert result is not None

    def test_guardrail_retry_context_survives_projection(self):
        """retry checkpoint replay 后保留被拒答案+修正提示，且重试预算不刷新。"""
        from floodmind.agent.native.executor import project_run_state_to_loop_state
        from floodmind.agent.runtime.reducer import initial_run_state

        correction = [
            {"role": "assistant", "content": "被拒答案"},
            {"role": "user", "content": "请修正"},
        ]
        state = AgentLoopState(
            session_id="s", run_id="run-1", status="awaiting_llm",
            output_guardrail_retried=True,
            output_guardrail_retry_messages=copy.deepcopy(correction),
            messages=[{"role": "system", "content": "sys"}, *correction],
        )
        rs = initial_run_state("run-1")
        rs.last_committed_sequence = 1
        rs.turns = [{"role": "user", "content": "原始问题", "thread_id": ""}]

        projected = project_run_state_to_loop_state(state, rs)

        assert projected.output_guardrail_retried is True
        assert projected.messages[-2:] == correction



class TestGuardrailReviewRound4:
    def test_max_tokens_partial_does_not_leak_before_final_verdict(self):
        emitted = []
        rounds = []
        mc = MagicMock(spec=ModelClient)
        def stream(*a, **kw):
            rounds.append(1)
            if len(rounds) == 1:
                return [
                    ModelEvent(type="token", content="违规片段"),
                    ModelEvent(type="done", terminal_reason=TerminalReason.from_raw("max_tokens")),
                ]
            return [ModelEvent(type="token", content="干净续写"), ModelEvent(type="done")]
        mc.stream_chat.side_effect = stream
        def block(output, state=None):
            return GuardrailResult(tripwire_triggered="违规" in output, message="blocked")
        block.__name__ = "block"
        executor = _make_executor(mc, output_guardrails=[block], max_iterations=10)
        executor.event_bus = MagicMock()
        executor.event_bus.emit = lambda e: emitted.append(e)
        executor.event_bus.emit_token = lambda c: emitted.append({"type": "token", "content": c})
        executor.event_bus.emit_reasoning = lambda c: None
        executor.run(_context(), "hello")
        assert not any("违规片段" in str(e.get("content", "")) for e in emitted),             "续写中间片段未经全量 guardrail verdict 不得放流"

    def test_input_guardrail_receives_only_input_scope_not_tool_outputs(self):
        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult
        from floodmind.agent.native.types import ToolCall
        calls = []
        mc = MagicMock(spec=ModelClient)
        def stream(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                return [ModelEvent(type="tool_call_done",
                                   tool_call=ToolCall(id="t", name="t", arguments={})),
                        ModelEvent(type="done")]
            return _token_done("final ok")
        mc.stream_chat.side_effect = stream
        te = MagicMock()
        te.execute.return_value = NativeToolResult(
            tool_call_id="t", name="t", content="tool says API_KEY=secret", status="completed")
        def no_secrets(messages):
            if any("API_KEY=" in str(m.get("content", "")) for m in messages):
                return GuardrailResult(tripwire_triggered=True, message="input blocked")
            return GuardrailResult(tripwire_triggered=False)
        no_secrets.__name__ = "no_secrets"
        executor = _make_executor(mc, input_guardrails=[no_secrets], tool_executor=te)
        result = executor.run(_context(), "clean user input")
        assert result.final_output == "final ok"

    def test_empty_answer_retry_really_runs_and_has_no_empty_assistant(self):
        captured = []
        mc = MagicMock(spec=ModelClient)
        responses = iter([_token_done(""), _token_done("")])
        def stream(*a, **kw):
            captured.append(copy.deepcopy(kw["messages"]))
            return next(responses)
        mc.stream_chat.side_effect = stream
        def check(output, state=None):
            return GuardrailResult(tripwire_triggered=True, message="empty output not allowed")
        check.__name__ = "check"
        executor = _make_executor(mc, output_guardrails=[check], max_iterations=10)
        executor.run(_context(), "hello")
        assert len(captured) == 2, "必须实际进入 guardrail 修正重试轮"
        for messages in captured:
            for m in messages:
                if m.get("role") == "assistant":
                    assert m.get("content") != "" or m.get("tool_calls"),                         "不得注入空 assistant 消息"
