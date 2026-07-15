"""Tests for model_presets — 前端 /api/models 契约（输出形状不随 schema 变化而变）。"""

from unittest.mock import patch

from floodmind.config.model_presets import (
    get_models_list, get_default_model_key, get_preset,
)


_TEST_CFG = {
    "providers": {
        "dashscope": {
            "base_url": "https://x/v1",
            "api_key": "sk",
            "models": [
                {"id": "model-a", "name": "A", "context_window": 65536, "default_max_tokens": 8192, "supports_reasoning": True},
                {"id": "model-b", "name": "B", "context_window": 32768, "supports_vision": True},
            ],
        }
    }
}


def _patched():
    return patch("floodmind.config.model_resolver.get_config", return_value=_TEST_CFG)


def test_default_key_is_first():
    with _patched():
        assert get_default_model_key() == "model-a"


def test_models_list_shape():
    """前端契约：每项含 key/label/description/supports_*/is_default。"""
    with _patched():
        items = get_models_list()
    assert len(items) == 2
    first = items[0]
    for field in ("key", "label", "description", "supports_reasoning",
                  "supports_search", "supports_vision", "is_default"):
        assert field in first
    assert first["is_default"] is True
    assert items[1]["is_default"] is False
    assert items[1]["supports_vision"] is True


def test_preset_carries_connection_and_params():
    with _patched():
        p = get_preset("model-a")
    assert p["model_name"] == "model-a"
    assert p["provider"] == "dashscope"
    assert p["api_key"] == "sk"
    assert p["default_base_url"] == "https://x/v1"
    assert p["max_context_tokens"] == 65536
