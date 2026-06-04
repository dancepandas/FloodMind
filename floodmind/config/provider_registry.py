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
    # Use default provider
    from floodmind.config.settings import settings
    return settings.model.provider_name, model_spec.strip()


def resolve_provider(provider_id: str) -> Dict[str, Any]:
    """从 settings.json 解析提供商配置"""
    from floodmind.config.settings import get_config
    cfg = get_config()
    provider_cfg = cfg.get("provider", {}).get(provider_id, {})

    if not isinstance(provider_cfg, dict):
        provider_cfg = {}

    options = provider_cfg.get("options", {}) if isinstance(provider_cfg, dict) else {}
    models = provider_cfg.get("models", {}) if isinstance(provider_cfg, dict) else {}

    result = {
        "id": provider_id,
        "name": provider_cfg.get("name", provider_id),
        "base_url": options.get("baseURL", options.get("base_url", "")),
        "api_key": options.get("apiKey", options.get("api_key", "")),
        "models": list(models.keys()) if isinstance(models, dict) else [],
    }

    # Fallback: env vars
    if not result["api_key"]:
        for env_var in provider_cfg.get("env", []):
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
    """列出所有已配置的有效提供商"""
    from floodmind.config.settings import get_config
    cfg = get_config()
    provider_cfg = cfg.get("provider", {})
    if not isinstance(provider_cfg, dict):
        return []
    result = []
    for pid in sorted(provider_cfg.keys()):
        pdata = provider_cfg[pid]
        if not isinstance(pdata, dict):
            continue
        options = pdata.get("options", {}) if isinstance(pdata, dict) else {}
        models = pdata.get("models", {}) if isinstance(pdata, dict) else {}
        has_key = bool(options.get("apiKey", "").strip()) or bool(options.get("api_key", "").strip())
        if has_key or pid == "ollama":
            result.append({
                "id": pid,
                "name": pdata.get("name", pid),
                "base_url": options.get("baseURL", options.get("base_url", "")),
                "models": list(models.keys()) if isinstance(models, dict) else [],
            })
    return result


def get_default_model() -> Tuple[str, str]:
    """Get the default provider/model pair."""
    from floodmind.config.settings import settings
    model_cfg = settings.model
    return model_cfg.provider_name, model_cfg.model_name


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
    from floodmind.config.settings import settings
    if not provider_id:
        provider_id = settings.model.provider_name
    if not model_id:
        model_id = settings.model.model_name

    provider = resolve_provider(provider_id)
    api_key = kwargs.get("api_key") or provider["api_key"]
    base_url = kwargs.get("base_url") or provider["base_url"]

    ck = _cache_key(provider_id, model_id, api_key, base_url)
    if ck in _client_cache:
        return _client_cache[ck]

    from floodmind.agent.native.model_client import ModelClient
    client = ModelClient(
        api_key=api_key,
        base_url=base_url,
        model_name=model_id,
        temperature=kwargs.get("temperature", settings.model.temperature),
        max_tokens=kwargs.get("max_tokens", settings.model.max_tokens),
    )
    _client_cache[ck] = client
    logger.info("Created LLM client: %s/%s @ %s", provider_id, model_id, base_url)
    return client


def clear_client_cache():
    """Clear cached LLM clients."""
    _client_cache.clear()
