"""
模型预设注册表

所有前端可选模型统一在此注册，后端根据 model_key 查找真实模型名、API 配置和能力标签。
能力字段遵循 docs/native-agent-multimodal-migration.md §10 规范。
"""

import os
from typing import Any, Dict, List, Optional

_VISION_DEFAULTS = {
    "supports_vision": False,
    "supports_tool_calling": True,
    "supports_tool_calling_with_vision": False,
    "supports_reasoning_with_vision": False,
    "vision_input_types": [],
    "max_image_count": 0,
    "max_image_size_mb": 0,
    "extra_body": {},
}

MODEL_PRESETS: Dict[str, Dict[str, Any]] = {
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
        "default_max_tokens": 65536,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 65536,
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
        "default_max_tokens": 65536,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 65536,
    },
    "qwen_36_plus": {
        "label": "Qwen 3.6 Plus",
        "description": "阿里最新混合推理模型，综合均衡，支持图像理解",
        "model_name": "qwen3.6-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "supports_vision": True,
        "supports_tool_calling_with_vision": True,
        "vision_input_types": ["image"],
        "max_image_count": 10,
        "max_image_size_mb": 20,
        "extra_body": {},
        "max_context_tokens": 1048576,
        "default_temperature": 0.3,
        "default_max_tokens": 32768,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 32768,
    },
    "qwen_35_plus": {
        "label": "Qwen 3.5 Plus",
        "description": "阿里混合推理模型，综合均衡，支持图像理解",
        "model_name": "qwen3.5-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "supports_vision": True,
        "supports_tool_calling_with_vision": True,
        "vision_input_types": ["image"],
        "max_image_count": 10,
        "max_image_size_mb": 20,
        "extra_body": {},
        "max_context_tokens": 1048576,
        "default_temperature": 0.3,
        "default_max_tokens": 32768,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 32768,
    },
    "qwen3_6_27b_local": {
        "label": "Qwen3.6 27B (local)",
        "description": "本地部署的 qwen3.6:27b",
        "model_name": "qwen3.6:27b",
        "api_key_env": "LOCAL_API_KEY",
        "base_url_env": "LOCAL_BASE_URL",
        "default_base_url": "http://192.168.30.112:3000/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "supports_vision": False,
        "max_context_tokens": 32768,
        "default_temperature": 0.3,
        "default_max_tokens": 8192,
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
        "default_max_tokens": 16384,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 16384,
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
        "default_max_tokens": 16384,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 16384,
    },
    "kimi_k2_5": {
        "label": "Kimi K2.5",
        "description": "月之暗面多模态模型，支持图像/视频理解",
        "model_name": "kimi-k2.5",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "supports_vision": True,
        "supports_tool_calling_with_vision": False,
        "supports_reasoning_with_vision": True,
        "vision_input_types": ["image", "video"],
        "max_image_count": 10,
        "max_image_size_mb": 20,
        "extra_body": {},
        "max_context_tokens": 1048576,
        "default_temperature": 0.3,
        "default_max_tokens": 32768,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 32768,
    },
    "kimi_k2_6": {
        "label": "Kimi K2.6",
        "description": "月之暗面多模态模型，支持图像/视频理解及显式缓存",
        "model_name": "kimi-k2.6",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_reasoning": True,
        "supports_search": False,
        "supports_vision": True,
        "supports_tool_calling_with_vision": False,
        "supports_reasoning_with_vision": True,
        "vision_input_types": ["image", "video"],
        "max_image_count": 10,
        "max_image_size_mb": 20,
        "extra_body": {},
        "max_context_tokens": 1048576,
        "default_temperature": 0.3,
        "default_max_tokens": 32768,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 32768,
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
        "default_max_tokens": 16384,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 16384,
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
        "default_max_tokens": 16384,
        "thinking_temperature": 0.2,
        "thinking_max_tokens": 16384,
    },
}

for _key, _preset in MODEL_PRESETS.items():
    for _vk, _vv in _VISION_DEFAULTS.items():
        _preset.setdefault(_vk, _vv)

DEFAULT_MODEL_KEY = "deepseek_v4_flash"


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
            "supports_vision": preset.get("supports_vision", False),
            "supports_tool_calling": preset.get("supports_tool_calling", True),
            "supports_tool_calling_with_vision": preset.get("supports_tool_calling_with_vision", False),
            "supports_reasoning_with_vision": preset.get("supports_reasoning_with_vision", False),
            "max_image_count": preset.get("max_image_count", 0),
            "max_image_size_mb": preset.get("max_image_size_mb", 0),
            "is_default": key == default_key,
        })
    return result
