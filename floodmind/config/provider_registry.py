"""
Provider registry — maps provider IDs to API configs and model lists.

Pattern: provider/model (e.g. "openai/gpt-4o", "dashscope/deepseek-v4-flash")
Inspired by OpenCode's provider.ts architecture.

Supports:
  - Built-in providers (dashscope, openai, deepseek, anthropic, google, ollama, custom)
  - Model discovery via env/config
  - SDK client caching (per provider+options hash)
  - Automatic default model selection
"""

import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider definitions
# ---------------------------------------------------------------------------

# Known provider configurations — env vars, default base URLs, etc.
PROVIDER_DEFS: Dict[str, Dict[str, Any]] = {
    "dashscope": {
        "name": "DashScope (Alibaba)",
        "env": ["DASHSCOPE_API_KEY"],
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "openai": {
        "name": "OpenAI",
        "env": ["OPENAI_API_KEY"],
        "default_base_url": "https://api.openai.com/v1",
    },
    "deepseek": {
        "name": "DeepSeek",
        "env": ["DEEPSEEK_API_KEY"],
        "default_base_url": "https://api.deepseek.com",
    },
    "anthropic": {
        "name": "Anthropic",
        "env": ["ANTHROPIC_API_KEY"],
        "default_base_url": "https://api.anthropic.com",
    },
    "google": {
        "name": "Google Gemini",
        "env": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    "azure": {
        "name": "Azure OpenAI",
        "env": ["AZURE_OPENAI_API_KEY", "AZURE_API_KEY"],
        # base_url is derived from resource name + deployment
        "default_base_url": "",
    },
    "xai": {
        "name": "xAI Grok",
        "env": ["XAI_API_KEY"],
        "default_base_url": "https://api.x.ai/v1",
    },
    "groq": {
        "name": "Groq",
        "env": ["GROQ_API_KEY"],
        "default_base_url": "https://api.groq.com/openai/v1",
    },
    "together": {
        "name": "Together AI",
        "env": ["TOGETHER_API_KEY"],
        "default_base_url": "https://api.together.xyz/v1",
    },
    "mistral": {
        "name": "Mistral AI",
        "env": ["MISTRAL_API_KEY"],
        "default_base_url": "https://api.mistral.ai/v1",
    },
    "cerebras": {
        "name": "Cerebras",
        "env": ["CEREBRAS_API_KEY"],
        "default_base_url": "https://api.cerebras.ai/v1",
    },
    "openrouter": {
        "name": "OpenRouter",
        "env": ["OPENROUTER_API_KEY"],
        "default_base_url": "https://openrouter.ai/api/v1",
    },
    "ollama": {
        "name": "Ollama (Local)",
        "env": [],
        "default_base_url": "http://localhost:11434/v1",
    },
    "custom": {
        "name": "Custom OpenAI-compatible",
        "env": ["CUSTOM_API_KEY"],
        "default_base_url": "http://localhost:8000/v1",
    },
}

# Per-provider known model IDs
KNOWN_MODELS: Dict[str, List[str]] = {
    "dashscope": [
        "deepseek-v4-flash", "deepseek-v4-pro",
        "qwen3.6-plus", "qwen3.5-plus",
        "glm-5.1", "glm-5",
        "kimi-k2.5", "kimi-k2.6",
        "MiniMax-M2.5", "MiniMax-M2.1",
    ],
    "openai": [
        "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
        "gpt-4o", "gpt-4o-mini",
        "o4-mini", "o3-mini",
    ],
    "deepseek": [
        "deepseek-chat", "deepseek-reasoner",
    ],
    "anthropic": [
        "claude-sonnet-4-5", "claude-haiku-4-5",
        "claude-opus-4-1",
    ],
    "google": [
        "gemini-2.5-pro", "gemini-2.5-flash",
        "gemini-2.0-flash",
    ],
    "xai": [
        "grok-4",
    ],
    "groq": [
        "llama-4-maverick", "llama-4-scout",
    ],
    "together": [
        "deepseek-ai/DeepSeek-V3",
    ],
    "openrouter": [
        "anthropic/claude-sonnet-4-5",
        "google/gemini-2.5-pro",
        "openai/gpt-4o",
    ],
    "mistral": [
        "mistral-large-latest",
    ],
    "cerebras": [
        "llama-4-maverick",
    ],
}


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

def parse_model(model_spec: str) -> Tuple[str, str]:
    """
    Parse "provider/model" into (provider_id, model_id).
    Falls back to default provider if no '/' present.
    """
    if "/" in model_spec:
        provider_id, model_id = model_spec.split("/", 1)
        return provider_id.strip(), model_id.strip()
    # Use default provider (= catalog 第一个模型的 provider)
    from floodmind.config.model_resolver import resolve_model
    return resolve_model().provider, model_spec.strip()


def resolve_provider(provider_id: str) -> Dict[str, Any]:
    """从 settings.json 解析提供商配置（providers 新结构，兼容旧 provider.options）。"""
    from floodmind.config.settings import get_config
    from floodmind.config.model_resolver import _providers_section, _normalize_models

    cfg = get_config()
    providers = _providers_section(cfg)
    pdata = providers.get(provider_id, {}) if isinstance(providers, dict) else {}
    if not isinstance(pdata, dict):
        pdata = {}

    models_list = _normalize_models(pdata.get("models", []))

    result = {
        "id": provider_id,
        "name": pdata.get("name", provider_id),
        "base_url": pdata.get("base_url") or pdata.get("baseURL", ""),
        "api_key": pdata.get("api_key") or pdata.get("apiKey", ""),
        "models": [m.get("id", "") for m in models_list if m.get("id")],
    }

    # Fallback: env vars
    if not result["api_key"]:
        for env_var in PROVIDER_DEFS.get(provider_id, {}).get("env", []):
            val = os.getenv(env_var, "").strip()
            if val:
                result["api_key"] = val
                break

    if not result["api_key"]:
        fallback = os.getenv("FLOODMIND_API_KEY", "").strip()
        if fallback:
            result["api_key"] = fallback

    return result


def list_available_providers() -> List[Dict[str, Any]]:
    """列出所有已配置的有效提供商（有 api_key，或 ollama 本地）。"""
    from floodmind.config.settings import get_config
    from floodmind.config.model_resolver import _providers_section, _normalize_models

    cfg = get_config()
    providers = _providers_section(cfg)
    if not isinstance(providers, dict):
        return []
    result: List[Dict[str, Any]] = []
    for pid in sorted(providers.keys()):
        pdata = providers[pid]
        if not isinstance(pdata, dict):
            continue
        api_key = (pdata.get("api_key") or pdata.get("apiKey") or "").strip()
        if api_key or pid == "ollama":
            models_list = _normalize_models(pdata.get("models", []))
            result.append({
                "id": pid,
                "name": pdata.get("name", pid),
                "base_url": pdata.get("base_url") or pdata.get("baseURL", ""),
                "models": [m.get("id", "") for m in models_list if m.get("id")],
            })
    return result


def get_default_model() -> Tuple[str, str]:
    """默认 (provider, model) = catalog 第一个。"""
    from floodmind.config.model_resolver import resolve_model
    rm = resolve_model()
    return rm.provider, rm.id


# ---------------------------------------------------------------------------
# Client cache
# ---------------------------------------------------------------------------

_client_cache: Dict[str, Any] = {}


def _cache_key(provider_id: str, model_id: str, api_key: str, base_url: str) -> str:
    raw = f"{provider_id}/{model_id}/{api_key}/{base_url}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def get_llm_client(provider_id: str = "", model_id: str = "", **kwargs):
    """
    Get or create a cached OpenAIClient for the given provider/model.

    Cached per (provider, model, key, url) combination.
    """
    from floodmind.config.model_resolver import resolve_model

    if provider_id or model_id:
        rm = resolve_model(model_key=model_id or None, provider_id=provider_id or None)
    else:
        rm = resolve_model()

    api_key = kwargs.get("api_key") or rm.api_key
    base_url = kwargs.get("base_url") or rm.base_url
    provider_id = rm.provider
    model_id = rm.id

    ck = _cache_key(provider_id, model_id, api_key, base_url)
    if ck in _client_cache:
        return _client_cache[ck]

    from floodmind.agent.native.model_client import ModelClient
    client = ModelClient(
        api_key=api_key,
        base_url=base_url,
        model_name=model_id,
        temperature=kwargs.get("temperature", rm.temperature),
        max_tokens=kwargs.get("max_tokens", rm.max_tokens),
    )
    _client_cache[ck] = client
    logger.info("Created LLM client: %s/%s @ %s", provider_id, model_id, base_url)
    return client


def clear_client_cache():
    """Clear cached LLM clients."""
    _client_cache.clear()
