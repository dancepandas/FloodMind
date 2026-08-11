"""ModelCapabilities（目标 §7.6）：数据化能力 + 分层覆盖，禁止 startswith 能力判断散落。"""

from enum import Enum
from typing import Dict, List, Tuple

from pydantic import BaseModel


class CapabilitySource(str, Enum):
    provider_default = "provider_default"
    family = "family"
    exact = "exact"
    discovery = "discovery"


class ModelCapabilities(BaseModel):
    transport_family: str = ""
    context_window: int = 0
    max_output_tokens: int = 0
    supports_tools: bool = False
    supports_parallel_tools: bool = False
    supports_reasoning: bool = False
    reasoning_replay_mode: str = ""
    supports_vision: bool = False
    supports_documents: bool = False
    supports_structured_output: bool = False
    supports_stream_usage: bool = False
    supports_server_compaction: bool = False
    resumable_terminal_reasons: List[str] = []


class CapabilitySnapshot(BaseModel):
    capabilities: ModelCapabilities
    source: CapabilitySource
    source_version: str = ""
    observed_at: str = ""


class CapabilityRegistry:
    """provider defaults → family override → exact override → (runtime discovery 预留)。"""

    def __init__(self):
        self._provider_defaults: Dict[str, ModelCapabilities] = {}
        self._family: Dict[Tuple[str, str], ModelCapabilities] = {}
        self._exact: Dict[str, ModelCapabilities] = {}

    def register_provider_defaults(self, provider: str, caps: ModelCapabilities) -> None:
        self._provider_defaults[provider] = caps

    def register_family(self, provider: str, family: str, caps: ModelCapabilities) -> None:
        self._family[(provider, family)] = caps

    def register_exact(self, model_id: str, caps: ModelCapabilities) -> None:
        self._exact[model_id] = caps

    def resolve_capabilities(self, provider: str, family: str, model: str) -> Tuple[ModelCapabilities, CapabilitySource]:
        if model in self._exact:
            return self._exact[model], CapabilitySource.exact
        if (provider, family) in self._family:
            return self._family[(provider, family)], CapabilitySource.family
        if provider in self._provider_defaults:
            return self._provider_defaults[provider], CapabilitySource.provider_default
        return ModelCapabilities(), CapabilitySource.provider_default

    def snapshot(self, provider: str, family: str, model: str, *, source_version: str = "", observed_at: str = "") -> CapabilitySnapshot:
        caps, source = self.resolve_capabilities(provider, family, model)
        return CapabilitySnapshot(capabilities=caps, source=source, source_version=source_version, observed_at=observed_at)


def _default_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.register_provider_defaults("openai", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_parallel_tools=True,
        supports_reasoning=False, supports_vision=True, supports_structured_output=True,
        supports_stream_usage=True))
    reg.register_family("openai", "o", ModelCapabilities(supports_reasoning=True, reasoning_replay_mode="none"))
    reg.register_provider_defaults("deepseek", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_reasoning=True,
        reasoning_replay_mode="think_tags"))
    reg.register_provider_defaults("kimi", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_reasoning=False))
    reg.register_provider_defaults("minimax", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_parallel_tools=True))
    reg.register_provider_defaults("dashscope", ModelCapabilities(
        transport_family="openai", supports_tools=True, supports_reasoning=True,
        reasoning_replay_mode="think_tags"))
    return reg


_DEFAULT_REGISTRY: CapabilityRegistry | None = None


def default_registry() -> CapabilityRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = _default_registry()
    return _DEFAULT_REGISTRY
