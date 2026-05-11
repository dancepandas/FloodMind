"""
模型预设注册表

所有前端可选模型统一在此注册，后端根据 model_key 查找真实模型名、API 配置和能力标签。
"""

import os
from typing import Any, Dict, List, Optional


MODEL_PRESETS: Dict[str, Dict[str, Any]] = {
    "qwen_35_plus": {
        "label": "Qwen 3.5 Plus",
        "description": "阿里混合推理模型，综合均衡",
        "model_name": "qwen3.5-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 1048576,
        "default_temperature": 0.3,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "qwen_36_plus": {
        "label": "Qwen 3.6 Plus",
        "description": "阿里最新混合推理模型，综合均衡",
        "model_name": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 1048576,
        "default_temperature": 0.3,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "glm_51": {
        "label": "GLM 5.1",
        "description": "智谱混合推理模型，偏复杂推理",
        "model_name": "glm-5.1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 204800,
        "default_temperature": 0.6,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "glm_5": {
        "label": "GLM 5",
        "description": "智谱混合推理模型，均衡通用",
        "model_name": "glm-5",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 204800,
        "default_temperature": 0.6,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "glm_47": {
        "label": "GLM 4.7",
        "description": "智谱混合推理模型，高效推理",
        "model_name": "glm-4.7",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 204800,
        "default_temperature": 0.6,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "glm_46": {
        "label": "GLM 4.6",
        "description": "智谱混合推理模型，轻量高效",
        "model_name": "glm-4.6",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 204800,
        "default_temperature": 0.6,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "glm_45": {
        "label": "GLM 4.5",
        "description": "智谱混合推理模型，支持结构化输出",
        "model_name": "glm-4.5",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 204800,
        "default_temperature": 0.6,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "deepseek_v4_pro": {
        "label": "DeepSeek V4 Pro",
        "description": "编程、数学与通用任务表现出色",
        "model_name": "deepseek-v4-pro",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 1048576,
        "default_temperature": 0.3,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "deepseek_v4_flash": {
        "label": "DeepSeek V4 Flash",
        "description": "快速高效，适合日常任务",
        "model_name": "deepseek-v4-flash",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 1048576,
        "default_temperature": 0.3,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "kimi_k2_thinking": {
        "label": "Kimi K2 Thinking",
        "description": "月之暗面混合推理模型，偏长上下文",
        "model_name": "kimi-k2-thinking",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 1048576,
        "default_temperature": 0.3,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "minimax_m25": {
        "label": "MiniMax M2.5",
        "description": "编程、办公、文本摘要，输出速度快",
        "model_name": "MiniMax-M2.5",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 204800,
        "default_temperature": 0.3,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
    "minimax_m21": {
        "label": "MiniMax M2.1",
        "description": "编程、办公、文本摘要，经济高效",
        "model_name": "MiniMax-M2.1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "max_context_tokens": 204800,
        "default_temperature": 0.3,
        "default_max_tokens": 4096,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 4096,
    },
}

DEFAULT_MODEL_KEY = "glm_51"


def get_preset(model_key: str) -> Optional[Dict[str, Any]]:
    return MODEL_PRESETS.get(model_key)


def get_default_model_key() -> str:
    env_key = os.getenv("DEFAULT_MODEL_KEY", "").strip()
    if env_key and env_key in MODEL_PRESETS:
        return env_key
    return DEFAULT_MODEL_KEY


def resolve_api_key(preset: Dict[str, Any]) -> str:
    env_var = preset.get("api_key_env", "DASHSCOPE_API_KEY")
    key = os.getenv(env_var, "").strip()
    if not key:
        raise ValueError(f"环境变量 {env_var} 未设置")
    return key


def resolve_base_url(preset: Dict[str, Any]) -> str:
    env_var = preset.get("base_url_env", "QWEN_BASE_URL")
    url = os.getenv(env_var, "").strip()
    return url or preset.get("default_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")


def get_models_list() -> List[Dict[str, Any]]:
    default_key = get_default_model_key()
    result = []
    for key, preset in MODEL_PRESETS.items():
        result.append({
            "key": key,
            "label": preset["label"],
            "description": preset.get("description", ""),
            "supports_reasoning": preset.get("supports_reasoning", False),
            "supports_search": preset.get("supports_search", False),
            "is_default": key == default_key,
        })
    return result
