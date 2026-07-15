"""Tests for model_resolver — 单一模型配置解析点。"""

import os
from unittest.mock import patch

import pytest

from floodmind.config.model_resolver import resolve_model, list_models, ResolvedModel


# 测试用 catalog：两个 provider，各含若干模型，context_window 各异
_TEST_CFG = {
    "providers": {
        "dashscope": {
            "name": "DashScope",
            "base_url": "https://dashscope.example/v1",
            "api_key": "sk-dash",
            "models": [
                {"id": "model-a", "name": "A", "context_window": 65536, "default_max_tokens": 8192, "default_temperature": 0.3, "supports_reasoning": True},
                {"id": "model-b", "name": "B", "context_window": 32768, "default_max_tokens": 4096, "default_temperature": 0.5, "supports_vision": True},
            ],
        },
        "openai": {
            "name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-oai",
            "models": [
                {"id": "gpt-test", "name": "GPT", "context_window": 128000, "default_max_tokens": 16384, "default_temperature": 0.2},
            ],
        },
    }
}


@pytest.fixture(autouse=True)
def _isolated_config():
    with patch("floodmind.config.model_resolver.get_config", return_value=_TEST_CFG):
        yield


class TestResolveModel:
    def test_default_is_first(self):
        """无参 → catalog 第一个模型（dashscope/model-a）。"""
        rm = resolve_model()
        assert rm.provider == "dashscope"
        assert rm.id == "model-a"

    def test_context_window_from_model(self):
        """context_window 取自模型定义，无额外回退。"""
        assert resolve_model().context_window == 65536
        assert resolve_model(model_key="model-b").context_window == 32768
        assert resolve_model(model_key="gpt-test").context_window == 128000

    def test_model_key_specific(self):
        rm = resolve_model(model_key="model-b")
        assert rm.id == "model-b"
        assert rm.temperature == 0.5
        assert rm.supports_vision is True

    def test_provider_connection_resolved(self):
        """api_key/base_url 取自 provider 连接段。"""
        rm = resolve_model(model_key="gpt-test")
        assert rm.api_key == "sk-oai"
        assert rm.base_url == "https://api.openai.com/v1"

    def test_provider_id_filter(self):
        """provider_id 限定 → 该 provider 下第一个模型。"""
        rm = resolve_model(provider_id="openai")
        assert rm.id == "gpt-test"

    def test_env_model_override(self):
        with patch.dict(os.environ, {"FLOODMIND_MODEL": "model-b"}, clear=False):
            rm = resolve_model()
            assert rm.id == "model-b"

    def test_env_api_key_override(self):
        """FLOODMIND_API_KEY 优先于 provider 配置。"""
        with patch.dict(os.environ, {"FLOODMIND_API_KEY": "sk-env"}, clear=False):
            assert resolve_model().api_key == "sk-env"

    def test_unknown_model_key_falls_back(self):
        """未知 model_key → 回退到 catalog 第一个（不抛错）。"""
        rm = resolve_model(model_key="does-not-exist")
        assert rm.id == "model-a"

    def test_returns_frozen_dataclass(self):
        rm = resolve_model()
        assert isinstance(rm, ResolvedModel)
        with pytest.raises(Exception):
            rm.api_key = "mutate"  # frozen=True


class TestLegacyFallback:
    def test_legacy_provider_singular_supported(self):
        """旧 provider（单数）+ options + dict-models 也能解析。"""
        legacy_cfg = {
            "provider": {
                "deepseek": {
                    "options": {"apiKey": "sk-ds", "baseURL": "https://ds/v1"},
                    "models": {"ds-chat": {"name": "DS", "maxTokens": 32768}},
                }
            }
        }
        with patch("floodmind.config.model_resolver.get_config", return_value=legacy_cfg):
            rm = resolve_model()
        assert rm.provider == "deepseek"
        assert rm.id == "ds-chat"
        assert rm.api_key == "sk-ds"
        assert rm.base_url == "https://ds/v1"

    def test_empty_catalog_raises(self):
        with patch("floodmind.config.model_resolver.get_config", return_value={}):
            with pytest.raises(ValueError):
                resolve_model()


class TestListModels:
    def test_order_preserved(self):
        items = list_models()
        ids = [(pid, m["id"]) for pid, _, m in items]
        assert ids[0] == ("dashscope", "model-a")
        assert ids[-1] == ("openai", "gpt-test")
