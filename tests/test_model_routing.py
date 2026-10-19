"""G-19：模型回退链与子代理模型路由。"""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_gateway import (
    FallbackModelClient,
    build_fallback_client,
    build_subagent_model_client,
)
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.transport import TransportRetryAdvice
from floodmind.agent.native.types import ModelEvent, RunContext

_TOOLS_SCHEMA = [{"type": "function", "function": {"name": "test_tool"}}]


class _FakeClient:
    def __init__(self, name, events=(), error_before=None, error_after=None):
        self.model_name = name
        self.temperature = 0.25
        self.calls = 0
        self.invoked = []
        self._events = list(events)
        self._error_before = error_before
        self._error_after = error_after

    def stream_chat(self, *args, **kwargs):
        self.calls += 1
        if self._error_before is not None:
            raise self._error_before

        def _gen():
            yield from self._events
            if self._error_after is not None:
                raise self._error_after

        return _gen()

    def invoke(self, prompt, **kwargs):
        self.invoked.append(prompt)
        return f"{self.model_name}:{prompt}"

    def classify_error(self, exc):
        return TransportRetryAdvice(retry_suggested=False, normalized_error=str(exc))


def _final(text="ok"):
    return [ModelEvent(type="token", content=text), ModelEvent(type="done")]


class TestFallbackModelClient:
    def test_switches_before_any_output_and_records(self):
        primary = _FakeClient("primary", error_before=RuntimeError("503 busy"))
        backup = _FakeClient("backup", events=_final())
        recorded = []

        client = FallbackModelClient(primary, [backup], on_fallback=lambda *a: recorded.append(a))

        events = list(client.stream_chat(messages=[], tools=None))

        assert [e.content for e in events if e.type == "token"] == ["ok"]
        assert primary.calls == 1
        assert backup.calls == 1
        assert client.model_name == "backup"
        assert recorded[0][0] == "primary" and recorded[0][1] == "backup"
        assert "503 busy" in recorded[0][2]

    def test_does_not_switch_after_output_started(self):
        primary = _FakeClient(
            "primary", events=[ModelEvent(type="token", content="partial")],
            error_after=RuntimeError("connection lost"),
        )
        backup = _FakeClient("backup", events=_final())
        recorded = []

        client = FallbackModelClient(primary, [backup], on_fallback=lambda *a: recorded.append(a))

        with pytest.raises(RuntimeError, match="connection lost"):
            list(client.stream_chat(messages=[], tools=None))

        assert backup.calls == 0
        assert recorded == []

    def test_chain_cap_limits_fallbacks(self):
        a = _FakeClient("a", error_before=RuntimeError("a failed"))
        b = _FakeClient("b", error_before=RuntimeError("b failed"))
        c = _FakeClient("c", error_before=RuntimeError("c failed"))

        client = FallbackModelClient(a, [b, c], max_fallbacks=1)

        with pytest.raises(RuntimeError, match="b failed"):
            list(client.stream_chat(messages=[], tools=None))
        assert c.calls == 0

    def test_all_failed_resets_to_primary(self):
        primary = _FakeClient("primary", error_before=RuntimeError("down"))
        backup = _FakeClient("backup", error_before=RuntimeError("also down"))
        client = FallbackModelClient(primary, [backup])

        with pytest.raises(RuntimeError, match="also down"):
            list(client.stream_chat(messages=[], tools=None))

        assert client.model_name == "primary"
        assert primary.calls == 1 and backup.calls == 1

    def test_reset_returns_to_primary(self):
        primary = _FakeClient("primary", events=_final("first"))
        backup = _FakeClient("backup", events=_final("second"))
        client = FallbackModelClient(primary, [backup])

        primary._error_before = RuntimeError("temporary")
        list(client.stream_chat(messages=[], tools=None))
        assert client.model_name == "backup"

        client.reset_fallbacks()
        assert client.model_name == "primary"

    def test_attribute_and_non_stream_calls_delegate_to_active_client(self):
        primary = _FakeClient("primary", error_before=RuntimeError("down"))
        backup = _FakeClient("backup", events=_final())
        client = FallbackModelClient(primary, [backup])

        list(client.stream_chat(messages=[], tools=None))

        assert client.temperature == 0.25
        assert client.invoke("hi") == "backup:hi"


class TestConfigDrivenConstruction:
    def test_no_fallbacks_returns_primary_unchanged(self, monkeypatch):
        from floodmind.agent.native import model_gateway

        monkeypatch.setattr(model_gateway, "routing_config", lambda: {"fallbacks": []})
        primary = _FakeClient("primary")

        assert build_fallback_client(primary) is primary

    def test_invalid_fallbacks_config_fails_closed(self, monkeypatch):
        from floodmind.agent.native import model_gateway

        monkeypatch.setattr(
            model_gateway, "routing_config",
            lambda: {"fallbacks": "not-a-list"},
        )
        with pytest.raises(ValueError, match="fallbacks"):
            build_fallback_client(_FakeClient("primary"))

    def test_invalid_max_fallbacks_fails_closed(self, monkeypatch):
        from floodmind.agent.native import model_gateway

        monkeypatch.setattr(
            model_gateway, "routing_config",
            lambda: {"fallbacks": ["backup"], "max_fallbacks": "two"},
        )
        with pytest.raises(ValueError, match="max_fallbacks"):
            build_fallback_client(_FakeClient("primary"))

    def test_builds_only_capped_fallbacks(self, monkeypatch):
        from floodmind.agent.native import model_gateway

        built = []

        def _from_settings(cls, model_key, enable_reasoning=False):
            built.append(model_key)
            return _FakeClient(model_key)

        monkeypatch.setattr(ModelClient, "from_settings_with_preset", classmethod(_from_settings))
        monkeypatch.setattr(
            model_gateway, "routing_config",
            lambda: {"fallbacks": ["b1", "b2", "b3"], "max_fallbacks": 2},
        )

        client = build_fallback_client(_FakeClient("primary"))

        assert built == ["b1", "b2"]
        assert isinstance(client, FallbackModelClient)
        assert [c.model_name for c in client._clients] == ["primary", "b1", "b2"]

    def test_subagent_model_routing_uses_configured_model(self, monkeypatch):
        from floodmind.agent.native import model_gateway

        built = []

        def _from_settings(cls, model_key, enable_reasoning=False):
            built.append(model_key)
            return _FakeClient(model_key)

        monkeypatch.setattr(ModelClient, "from_settings_with_preset", classmethod(_from_settings))
        monkeypatch.setattr(
            model_gateway, "routing_config",
            lambda: {"subagent_model": "cheap", "fallbacks": []},
        )
        parent = _FakeClient("parent")

        wrapped = []
        client = build_subagent_model_client(parent, wrap=lambda c: wrapped.append(c) or c)

        assert built == ["cheap"]
        assert client.model_name == "cheap"
        assert wrapped and wrapped[0].model_name == "cheap"

    def test_subagent_without_routing_inherits_parent(self, monkeypatch):
        from floodmind.agent.native import model_gateway

        monkeypatch.setattr(
            model_gateway, "routing_config",
            lambda: {"subagent_model": "", "fallbacks": []},
        )
        parent = _FakeClient("parent")

        client = build_subagent_model_client(parent, wrap=lambda c: c)

        assert client is parent


class TestExecutorFallbackIntegration:
    def _executor(self, model_client):
        registry = MagicMock()
        registry.get.return_value = None
        registry.all.return_value = []
        registry.tools_schema.return_value = _TOOLS_SCHEMA
        return NativeAgentExecutor(
            model_client=model_client,
            tool_executor=MagicMock(),
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=3,
            system_prompt="test prompt",
            tools_schema=_TOOLS_SCHEMA,
            tool_registry=registry,
        )

    def _context(self):
        return RunContext(
            session_id="routing-test",
            user_text="hello",
            output_dir="/tmp/routing-out",
            upload_dir="/tmp/routing-up",
        )

    def test_run_completes_on_fallback_model(self):
        primary = _FakeClient("primary", error_before=RuntimeError("503 busy"))
        backup = _FakeClient("backup", events=_final("fallback answer"))
        recorded = []
        client = FallbackModelClient(primary, [backup], on_fallback=lambda *a: recorded.append(a))

        result = self._executor(client).run(self._context(), "hello")

        assert result.final_output == "fallback answer"
        assert len(recorded) == 1

    def test_executor_resets_fallbacks_at_run_start(self):
        primary = _FakeClient("primary", events=_final("primary answer"))
        backup = _FakeClient("backup", events=_final("backup answer"))
        resets = []

        class _Tracking(FallbackModelClient):
            def reset_fallbacks(self):
                resets.append(True)
                super().reset_fallbacks()

        client = _Tracking(primary, [backup])
        client._active = 1

        result = self._executor(client).run(self._context(), "hello")

        assert result.final_output == "primary answer"
        assert resets
