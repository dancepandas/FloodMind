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
from floodmind.agent.native.retry import should_retry
from floodmind.agent.native.transport import TransportRetryAdvice
from floodmind.agent.native.types import ModelEvent, RunContext, TerminalReason


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


def _make_context():
    return RunContext(
        session_id="test-session",
        user_text="hello",
        output_dir="/tmp/test-out",
        upload_dir="/tmp/test-up",
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
