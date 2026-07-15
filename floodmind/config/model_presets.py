"""模型预设注册表 — 从 settings.json 的 providers 目录读取（OpenCode 层级）。

层级::

    providers.<id>.{base_url, api_key, models[]}
        models[].{id, context_window, default_max_tokens, default_temperature, ...}

目录读取统一委托 model_resolver.list_models()，避免重复解析。
对前端暴露的输出形状（get_models_list）保持不变——这是前端模型选择器契约。
"""

import os
from typing import Any, Dict, List, Optional


def _get_all_provider_models() -> Dict[str, Dict[str, Any]]:
    """从 providers 目录提取所有模型 → {model_key: preset}。

    输出形状保持与旧版一致（供 resolve_api_key/base_url、ModelClient 复用）。
    """
    from floodmind.config.model_resolver import list_models

    result: Dict[str, Dict[str, Any]] = {}
    for pid, pdata, m in list_models():
        key = m.get("id", "")
        if not key:
            continue
        result[key] = {
            "label": m.get("name", key),
            "description": m.get("description", ""),
            "model_name": key,
            "provider": pid,
            "api_key_env": "",
            "base_url_env": "",
            "default_base_url": pdata.get("base_url", ""),
            "api_key": pdata.get("api_key", ""),
            "supports_reasoning": bool(m.get("supports_reasoning", m.get("supportsReasoning", False))),
            "supports_search": bool(m.get("supports_search", m.get("supportsSearch", False))),
            "supports_vision": bool(m.get("supports_vision", m.get("supportsVision", False))),
            "max_context_tokens": int(m.get("context_window", m.get("maxTokens", 8192))),
            "default_temperature": float(m.get("default_temperature", m.get("temperature", 0.3))),
            "default_max_tokens": int(m.get("default_max_tokens", m.get("maxTokens", 8192))),
            "thinking_temperature": float(m.get("thinking_temperature", m.get("thinkingTemperature", 0.2))),
            "thinking_max_tokens": int(m.get("thinking_max_tokens", m.get("thinkingMaxTokens", 8192))),
        }
    return result


def get_preset(model_key: str) -> Optional[Dict[str, Any]]:
    return _get_all_provider_models().get(model_key)


DEFAULT_MODEL_KEY: Optional[str] = None


def get_default_model_key() -> str:
    """默认激活模型 = catalog 第一个（不再读 settings.model 选择段）。"""
    global DEFAULT_MODEL_KEY
    if DEFAULT_MODEL_KEY is None:
        from floodmind.config.model_resolver import list_models
        candidates = list_models()
        DEFAULT_MODEL_KEY = (
            candidates[0][2].get("id", "deepseek-v4-flash") if candidates else "deepseek-v4-flash"
        )
    return DEFAULT_MODEL_KEY


def resolve_api_key(preset: Dict[str, Any]) -> str:
    """解析 API Key：preset 配置 > 环境变量。"""
    key = (preset.get("api_key") or "").strip()
    if key:
        return key
    env_var = preset.get("api_key_env", "DASHSCOPE_API_KEY")
    if env_var:
        key = os.getenv(env_var, "").strip()
    if not key:
        key = os.getenv("FLOODMIND_API_KEY", "").strip()
    if not key:
        raise ValueError(f"模型 {preset.get('model_name', 'unknown')} 未配置 API 密钥")
    return key


def resolve_base_url(preset: Dict[str, Any]) -> str:
    """解析 Base URL：preset 配置 > 环境变量。"""
    url = (preset.get("default_base_url") or "").strip()
    if url:
        return url
    env_var = preset.get("base_url_env", "")
    if env_var:
        env_url = os.getenv(env_var, "").strip()
        if env_url:
            return env_url
    return os.getenv("FLOODMIND_BASE_URL", "").strip()


def get_models_list() -> List[Dict[str, Any]]:
    """前端模型选择器契约：[{key,label,description,supports_*,is_default}]。"""
    all_models = _get_all_provider_models()
    default_key = get_default_model_key()
    result: List[Dict[str, Any]] = []
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


def reload_presets() -> None:
    """运行时重新加载预设（配置变更后调用）。"""
    from floodmind.config.settings import reload_config
    reload_config()
    global DEFAULT_MODEL_KEY
    DEFAULT_MODEL_KEY = None
