"""Tests for Settings configuration."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from floodmind.config.settings import (
    Settings, ModelConfig, AgentConfig, RAGConfig, TaskExperienceConfig,
    get_config, _deep_merge, _load_json_config,
)


class TestConfigLoading:
    def test_deep_merge(self):
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        override = {"a": {"b": 10}}
        result = _deep_merge(base, override)
        assert result["a"]["b"] == 10
        assert result["a"]["c"] == 2  # not overridden
        assert result["d"] == 3

    def test_load_json_not_found(self):
        assert _load_json_config(Path("/nonexistent/path.json")) == {}


class TestModelConfig:
    def test_provider_name_default(self):
        cfg = ModelConfig(get_config())
        assert cfg.provider_name in ("dashscope", "openai")

    def test_model_name_set(self):
        cfg = ModelConfig(get_config())
        assert cfg.model_name  # non-empty

    @patch.dict(os.environ, {"FLOODMIND_MODEL": "gpt-4o-mini"}, clear=False)
    def test_env_model_override(self):
        from floodmind.config.settings import reload_config
        reload_config()
        cfg = ModelConfig(get_config())
        assert cfg.model_name == "gpt-4o-mini"


class TestAgentConfig:
    def test_defaults(self):
        cfg = AgentConfig(get_config())
        assert cfg.max_history == 20
        assert cfg.context_window == 32768

    @patch.dict(os.environ, {"AGENT_MAX_HISTORY": "30"}, clear=True)
    def test_env_override(self):
        cfg = AgentConfig(get_config())
        assert cfg.max_history == 30


class TestRAGConfig:
    def test_defaults(self):
        cfg = RAGConfig(get_config())
        assert cfg.enabled is True
        assert cfg.top_k == 10


class TestTaskExperienceConfig:
    def test_defaults(self):
        cfg = TaskExperienceConfig(get_config())
        assert cfg.enabled is True
        assert cfg.seal_threshold == 5
