import pytest

from floodmind.agent.native.capabilities import (
    CapabilityRegistry, CapabilitySource, ModelCapabilities,
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
