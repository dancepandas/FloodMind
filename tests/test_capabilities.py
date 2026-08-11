import pytest

from floodmind.agent.native.capabilities import (
    CapabilityRegistry, CapabilitySource, ModelCapabilities, default_registry,
)


def test_layered_resolution_precedence():
    reg = CapabilityRegistry()
    reg.register_provider_defaults("deepseek", ModelCapabilities(supports_reasoning=True))
    reg.register_family("deepseek", "deepseek-reasoner", ModelCapabilities(supports_reasoning=True, context_window=64000))
    reg.register_exact("deepseek-chat", ModelCapabilities(context_window=128000))
    caps, src = reg.resolve_capabilities("deepseek", "deepseek-reasoner", "deepseek-reasoner")
    assert caps.context_window == 64000 and src == CapabilitySource.family
    caps2, src2 = reg.resolve_capabilities("deepseek", "deepseek-reasoner", "deepseek-chat")
    assert caps2.context_window == 128000 and src2 == CapabilitySource.exact
    caps3, src3 = reg.resolve_capabilities("deepseek", "unknown", "deepseek-unknown")
    assert src3 == CapabilitySource.provider_default


def test_capability_snapshot_records_source_and_version():
    reg = CapabilityRegistry()
    reg.register_provider_defaults("kimi", ModelCapabilities(supports_tools=True))
    snap = reg.snapshot("kimi", "moonshot", "kimi-k2", source_version="v1", observed_at="2026-08-11T00:00:00Z")
    assert snap.capabilities.supports_tools is True
    assert snap.source == CapabilitySource.provider_default
    assert snap.source_version == "v1"


def test_family_override_inherits_provider_defaults():
    # o-family override 只声明 reasoning，其余能力必须继承 openai provider 默认（§7.6 分层覆盖）。
    reg = CapabilityRegistry()
    reg.register_provider_defaults("openai", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_vision=True))
    reg.register_family("openai", "o", ModelCapabilities(
        supports_reasoning=True, reasoning_replay_mode="none"))
    caps, src = reg.resolve_capabilities("openai", "o", "o4-mini")
    assert src == CapabilitySource.family
    assert caps.transport_family == "openai"
    assert caps.supports_tools is True
    assert caps.supports_vision is True
    assert caps.supports_reasoning is True
    assert caps.reasoning_replay_mode == "none"


def test_default_registry_moonshot_alias():
    # 运行时 provider id 可能是 "moonshot"（KimiPipeline.match 接受两者），必须解析到 kimi 默认能力。
    reg = default_registry()
    caps, src = reg.resolve_capabilities("moonshot", "moonshot", "kimi-k2")
    assert src == CapabilitySource.provider_default
    assert caps.transport_family == "openai"
    assert caps.supports_tools is True


def test_resolve_returns_copy_isolated_from_registry():
    # resolve 返回副本：调用方改动不得污染 registry 持有的默认值（纯确定性不变量）。
    reg = CapabilityRegistry()
    reg.register_provider_defaults("deepseek", ModelCapabilities(supports_reasoning=True))
    caps, _ = reg.resolve_capabilities("deepseek", "deepseek", "deepseek-chat")
    caps.supports_reasoning = False
    caps.resumable_terminal_reasons.append("refusal")
    caps2, _ = reg.resolve_capabilities("deepseek", "deepseek", "deepseek-chat")
    assert caps2.supports_reasoning is True
    assert caps2.resumable_terminal_reasons == []
