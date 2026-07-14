"""
模型预设注册表 — 从 settings.json 的 provider.models 段动态读取

所有前端可选模型由用户在 ~/.floodmind/settings.json 中配置。
内置预设仅作为初始模板（settings_template.json），会被用户配置覆盖。
"""

import os
from typing import Any, Dict, List, Optional

from floodmind.config.settings import get_config, settings


def _get_all_provider_models() -> Dict[str, Dict[str, Any]]:
    """从配置中提取所有 provider 的 models"""
    cfg = get_config()
    provider_cfg = cfg.get("provider", {})
    result = {}
    if not isinstance(provider_cfg, dict):
        return result
    for provider_name, provider_data in provider_cfg.items():
        if not isinstance(provider_data, dict):
            continue
        models = provider_data.get("models", {})
        if not isinstance(models, dict):
            continue
        options = provider_data.get("options", {}) if isinstance(provider_data, dict) else {}
        for model_key, model_info in models.items():
            if not isinstance(model_info, dict):
                continue
            result[model_key] = {
                "label": model_info.get("name", model_key),
                "description": model_info.get("description", ""),
                "model_name": model_key,
                "provider": provider_name,
                "api_key_env": "",
                "base_url_env": "",
                "default_base_url": options.get("baseURL", options.get("base_url", "")),
                "api_key": options.get("apiKey", options.get("api_key", "")),
                "supports_reasoning": model_info.get("supportsReasoning", model_info.get("supports_reasoning", False)),
                "supports_search": model_info.get("supportsSearch", model_info.get("supports_search", False)),
                "supports_vision": model_info.get("supportsVision", model_info.get("supports_vision", False)),
                "max_context_tokens": model_info.get("maxTokens", model_info.get("max_tokens", 8192)),
                "default_temperature": model_info.get("temperature", model_info.get("default_temperature", 0.3)),
                "default_max_tokens": model_info.get("maxTokens", model_info.get("max_tokens", 8192)),
                "thinking_temperature": model_info.get("thinkingTemperature", model_info.get("thinking_temperature", 0.2)),
                "thinking_max_tokens": model_info.get("thinkingMaxTokens", model_info.get("thinking_max_tokens", 8192)),
            }
    return result


def get_preset(model_key: str) -> Optional[Dict[str, Any]]:
    return _get_all_provider_models().get(model_key)


DEFAULT_MODEL_KEY = None


def get_default_model_key() -> str:
    global DEFAULT_MODEL_KEY
    if DEFAULT_MODEL_KEY is None:
        from floodmind.config.settings import settings
        model_cfg = settings.model
        cfg = get_config()
        model_section = cfg.get("model", {})
        DEFAULT_MODEL_KEY = model_section.get("model", model_section.get("model_name", "deepseek-v4-flash"))
    return DEFAULT_MODEL_KEY


def resolve_api_key(preset: Dict[str, Any]) -> str:
    key = preset.get("api_key", "").strip()
    if key:
        return key
    env_var = preset.get("api_key_env", "DASHSCOPE_API_KEY")
    if env_var:
        key = os.getenv(env_var, "").strip()
    if not key:
        raise ValueError(f"模型 {preset.get('model_name', 'unknown')} 未配置 API 密钥")
    return key


def resolve_base_url(preset: Dict[str, Any]) -> str:
    url = preset.get("default_base_url", "").strip()
    if url:
        return url
    env_var = preset.get("base_url_env", "")
    if env_var:
        env_url = os.getenv(env_var, "").strip()
        if env_url:
            return env_url
    # 兜底：从 provider options 中读取 base_url
    provider = preset.get("provider", "")
    if provider:
        from floodmind.config.settings import get_config
        provider_opts = get_config().get("provider", {}).get(provider, {}).get("options", {})
        if isinstance(provider_opts, dict):
            url = provider_opts.get("baseURL") or provider_opts.get("base_url")
            if url:
                return url
    return ""


def get_models_list() -> List[Dict[str, Any]]:
    all_models = _get_all_provider_models()
    default_key = get_default_model_key()
    result = []
    for key, preset in all_models.items():
        result.append({
            "key": key,
            "label": preset["label"],
            "description": preset.get("description", ""),
            "supports_reasoning": preset.get("supports_reasoning", False),
            "supports_search": preset.get("supports_search", False),
            "supports_vision": preset.get("supports_vision", False),
            "is_default": key == default_key,
        })
    return result


def reload_presets():
    """运行时重新加载预设（配置变更后调用）"""
    from floodmind.config.settings import reload_config
    reload_config()
    global DEFAULT_MODEL_KEY
    DEFAULT_MODEL_KEY = None
