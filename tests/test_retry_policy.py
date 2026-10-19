"""P4 §7.7 Retry Orchestrator 决策测试。

Transport 只返回 Retry Advice；是否重试由 Orchestrator（executor 的
``should_retry``）决定。默认禁止自动重试：
refusal / content_filter / length(max_tokens) / pause / 已开始输出且无 replay safety。
"""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.retry import RetryPolicy, should_retry
from floodmind.agent.native.transport import TransportRetryAdvice
from floodmind.agent.native.types import ModelEvent, RunContext, TerminalReason, ToolCall


# ── should_retry 纯决策 ────────────────────────────────────────────


def test_retry_advice_but_refusal_blocks_retry():
    assert should_retry(TransportRetryAdvice(retry_suggested=True), "refusal") is False


def test_retry_advice_connection_error_retries():
    assert should_retry(TransportRetryAdvice(retry_suggested=True, response_started=False), None) is True


def test_started_no_replay_safety_blocks_retry():
    assert should_retry(
        TransportRetryAdvice(retry_suggested=True, response_started=True, replay_safe=False),
        None,
    ) is False


@pytest.mark.parametrize(
    "reason",
    ["refusal", "content_filter", "length", "pause", "paused", "max_tokens"],
)
def test_blocked_terminal_reasons_block_retry(reason):
    """§7.7 默认禁自动重试清单：refusal / filtered / max_tokens / paused。"""
    assert should_retry(TransportRetryAdvice(retry_suggested=True), reason) is False


def test_terminal_reason_object_is_normalized():
    """TerminalReason 对象（raw=wire 值 / code=规范化值）同样纳入阻断判断。"""
    assert should_retry(
        TransportRetryAdvice(retry_suggested=True),
        TerminalReason.from_raw("length"),
    ) is False
    assert should_retry(
        TransportRetryAdvice(retry_suggested=True),
        TerminalReason.from_raw("refusal"),
    ) is False


def test_not_suggested_never_retries():
    assert should_retry(TransportRetryAdvice(retry_suggested=False), None) is False


def test_started_with_replay_safety_retries():
    assert should_retry(
        TransportRetryAdvice(retry_suggested=True, response_started=True, replay_safe=True),
        None,
    ) is True


# ── Executor 接线：Transport 只给 Advice，Orchestrator 决策 ─────────


def _make_executor(model_client):
    tool_executor = MagicMock()
    from floodmind.agent.runtime.contracts.tools import ToolSpec  # noqa: F401

    reg = MagicMock()
    reg.get.return_value = None
    reg.all.return_value = []
    reg.tools_schema.return_value = []
    return NativeAgentExecutor(
        model_client=model_client,
        tool_executor=tool_executor,
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=5,
        system_prompt="test prompt",
        tools_schema=[],
        tool_registry=reg,
        tool_loader=None,
    )


def _make_context(abort_check=None):
    return RunContext(
        session_id="test-session",
        user_text="hello",
        output_dir="/tmp/test-out",
        upload_dir="/tmp/test-up",
        abort_check=abort_check,
    )


def test_executor_does_not_retry_when_advice_blocks():
    """Advice 为 retry_suggested=False（如 refusal/content_filter）→ 不重试，直接失败。"""
    mc = MagicMock(spec=ModelClient)
    mc.classify_error.return_value = TransportRetryAdvice(
        retry_suggested=False, normalized_error="refusal"
    )
    mc.model_name = "test-model"

    def stream_chat(*args, **kwargs):
        raise RuntimeError("refusal")

    mc.stream_chat.side_effect = stream_chat
    executor = _make_executor(mc)
    result = executor.run(_make_context(), "hello")

    assert mc.stream_chat.call_count == 1  # 未自动重试
    assert result.final_output.startswith("模型调用失败")


def test_executor_retries_when_advice_suggests():
    """Advice 为 retry_suggested=True（如连接层超时）→ 经 should_retry 决策重试。"""
    mc = MagicMock(spec=ModelClient)
    mc.classify_error.return_value = TransportRetryAdvice(
        retry_suggested=True, response_started=False
    )
    mc.model_name = "test-model"
    calls = {"n": 0}

    def stream_chat(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timed out")
        return iter([ModelEvent(type="token", content="ok"), ModelEvent(type="done")])

    mc.stream_chat.side_effect = stream_chat
    executor = _make_executor(mc)
    result = executor.run(_make_context(), "hello")

    assert calls["n"] == 2  # 重试一次成功
    assert "ok" in result.final_output


# ── 重试安全性：已开始输出禁止重试 / 退避可中断 ─────────────────────


class _FastRetryPolicy(RetryPolicy):
    """测试用快退避：保持重试次数，只把等待压到毫秒级。"""

    def __init__(self, *args, **kwargs):
        kwargs["base_delay"] = 0.01
        kwargs["max_delay"] = 0.01
        super().__init__(*args, **kwargs)


def _capture_events(executor):
    events = []
    executor.event_bus.add_listener(events.append)
    return events


def _event_types(events, *types_):
    return [e for e in events if e.get("type") in types_]


@pytest.mark.parametrize(
    "first_event",
    [
        ModelEvent(type="token", content="部分"),
        ModelEvent(type="reasoning", content="思考"),
        ModelEvent(
            type="tool_call_done",
            tool_call=ToolCall(id="call-1", name="query", arguments={}),
        ),
    ],
    ids=["token", "reasoning", "tool_call"],
)
def test_executor_does_not_retry_after_output_started(first_event):
    """已产生 token/reasoning/工具事件后异常：绝不重试（避免重复输出与扣费）。"""
    mc = MagicMock(spec=ModelClient)
    mc.classify_error.return_value = TransportRetryAdvice(
        retry_suggested=True, response_started=False
    )
    mc.model_name = "test-model"
    calls = {"n": 0}

    def stream_chat(*args, **kwargs):
        calls["n"] += 1

        def gen():
            yield first_event
            raise RuntimeError("peer closed connection")

        return gen()

    mc.stream_chat.side_effect = stream_chat
    executor = _make_executor(mc)
    events = _capture_events(executor)
    result = executor.run(_make_context(), "hello")

    assert calls["n"] == 1
    assert result.final_output.startswith("模型调用失败")
    assert _event_types(events, "retry_attempt", "wait") == []
    assert [e["finish_reason"] for e in _event_types(events, "llm_step_end")] == ["error"]


def test_executor_retries_when_response_not_started(monkeypatch):
    """流未产生任何输出时，可重试错误仍按 max_retries + 1 次尝试。"""
    monkeypatch.setattr(
        "floodmind.agent.native.executor.RetryPolicy", _FastRetryPolicy
    )
    mc = MagicMock(spec=ModelClient)
    mc.classify_error.return_value = TransportRetryAdvice(
        retry_suggested=True, response_started=False
    )
    mc.model_name = "test-model"
    calls = {"n": 0}

    def stream_chat(*args, **kwargs):
        calls["n"] += 1

        def gen():
            raise TimeoutError("timed out")
            yield  # pragma: no cover

        return gen()

    mc.stream_chat.side_effect = stream_chat
    executor = _make_executor(mc)
    events = _capture_events(executor)
    result = executor.run(_make_context(), "hello")

    assert calls["n"] == 4  # max_retries(3) + 1
    assert len(_event_types(events, "retry_attempt")) == 3
    assert result.final_output.startswith("模型调用失败")


def test_executor_abort_during_backoff_skips_next_stream(monkeypatch):
    """退避期间用户取消：立即以 aborted 终态收尾，不再发起模型调用。"""
    monkeypatch.setattr(
        "floodmind.agent.native.executor.RetryPolicy", _FastRetryPolicy
    )
    mc = MagicMock(spec=ModelClient)
    mc.classify_error.return_value = TransportRetryAdvice(
        retry_suggested=True, response_started=False
    )
    mc.model_name = "test-model"
    calls = {"n": 0}
    aborted = {"flag": False}

    def stream_chat(*args, **kwargs):
        calls["n"] += 1

        def gen():
            raise TimeoutError("timed out")
            yield  # pragma: no cover

        return gen()

    mc.stream_chat.side_effect = stream_chat
    executor = _make_executor(mc)
    events = _capture_events(executor)

    # retry_attempt 在退避开始前发出：观察到它即视为用户取消（退避中中断）。
    def abort_on_retry_event(event):
        if event.get("type") == "retry_attempt":
            aborted["flag"] = True

    executor.event_bus.add_listener(abort_on_retry_event)
    context = _make_context(abort_check=lambda: aborted["flag"])
    result = executor.run(context, "hello")

    assert calls["n"] == 1
    assert result.final_output == "任务已被用户中断。"
    assert len(_event_types(events, "retry_attempt")) == 1
    assert [
        e["finish_reason"] for e in _event_types(events, "llm_step_end")
    ] == ["aborted"]


def test_executor_abort_before_backoff_emits_no_retry_events():
    """异常发生时已取消：不发 retry_attempt/wait，直接 aborted 收尾。"""
    mc = MagicMock(spec=ModelClient)
    mc.classify_error.return_value = TransportRetryAdvice(
        retry_suggested=True, response_started=False
    )
    mc.model_name = "test-model"
    calls = {"n": 0}
    aborted = {"flag": False}

    def stream_chat(*args, **kwargs):
        calls["n"] += 1
        aborted["flag"] = True  # 模型调用期间收到取消

        def gen():
            raise TimeoutError("timed out")
            yield  # pragma: no cover

        return gen()

    mc.stream_chat.side_effect = stream_chat
    executor = _make_executor(mc)
    events = _capture_events(executor)
    context = _make_context(abort_check=lambda: aborted["flag"])
    result = executor.run(context, "hello")

    assert calls["n"] == 1
    assert result.final_output == "任务已被用户中断。"
    assert _event_types(events, "retry_attempt", "wait") == []
    assert [
        e["finish_reason"] for e in _event_types(events, "llm_step_end")
    ] == ["aborted"]
