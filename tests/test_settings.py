"""Tests for Settings configuration (v2: providers-only schema + resolver facade)."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from floodmind.config.settings import (
    Settings, ModelConfig, AgentConfig, TaskExperienceConfig,
    get_config, _deep_merge, _load_json_config, _migrate_legacy_config,
    DEFAULT_CONFIG, DEFAULT_MAX_ITERATIONS,
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
    def test_model_name_default(self):
        """默认激活模型 = catalog 第一个（deepseek-v4-flash）。"""
        cfg = ModelConfig(get_config())
        assert cfg.model_name == "deepseek-v4-flash"

    def test_context_window_from_model(self):
        """记忆窗口取自激活模型，不再来自 agent 段。"""
        cfg = ModelConfig(get_config())
        assert cfg.context_window == 65536

    @patch.dict(os.environ, {"FLOODMIND_MODEL": "qwen3.6-plus"}, clear=False)
    def test_env_model_override(self):
        from floodmind.config.settings import reload_config
        reload_config()
        cfg = ModelConfig(get_config())
        assert cfg.model_name == "qwen3.6-plus"
        assert cfg.context_window == 32768  # qwen3.6-plus 自带窗口


class TestAgentConfig:
    def test_defaults(self):
        """agent 段已精简：无 max_history/context_window；max_iterations 默认 999。"""
        with patch("floodmind.config.settings.get_config", return_value=DEFAULT_CONFIG):
            cfg = AgentConfig(DEFAULT_CONFIG)
        assert cfg.runtime == "native"
        assert cfg.max_iterations == DEFAULT_MAX_ITERATIONS == 999
        # 这些属性已移除
        assert not hasattr(cfg, "max_history")
        assert not hasattr(cfg, "context_window")
        assert not hasattr(cfg, "enable_chronos_warmup")


class TestTaskExperienceConfig:
    def test_always_on(self):
        """经验系统强制常开，不再有 enabled/auto_capture 开关语义。"""
        cfg = TaskExperienceConfig(get_config())
        assert cfg.enabled is True
        assert cfg.auto_capture is True
        assert cfg.seal_threshold == 5


class TestLegacyMigration:
    """旧格式 settings.json → 仅 providers 新格式。"""

    def test_migrate_provider_and_models(self):
        old = {
            "provider": {
                "dashscope": {
                    "name": "DashScope",
                    "options": {"apiKey": "sk-x", "baseURL": "https://x/v1"},
                    "models": {
                        "m1": {"name": "M1", "maxTokens": 4096, "supportsReasoning": True},
                        "m2": {"name": "M2", "maxTokens": 8192},
                    },
                }
            },
            "model": {"provider": "dashscope", "model": "m2", "temperature": 0.5},
            "agent": {"maxHistory": 30, "contextWindow": 131072, "enableChronosWarmup": True},
            "task_experience": {"enabled": False, "autoCapture": False, "persistDir": "./d", "sealThreshold": 7},
        }
        out, migrated = _migrate_legacy_config(old)

        assert migrated is True
        # provider → providers；options 扁平化；models dict → list + id
        prov = out["providers"]["dashscope"]
        assert prov["api_key"] == "sk-x"
        assert prov["base_url"] == "https://x/v1"
        ids = [m["id"] for m in prov["models"]]
        assert ids == ["m1", "m2"]
        # 旧段已丢弃
        assert "model" not in out
        assert "agent" not in out  # 仅含废弃键 → 整段删除
        # task_experience：开关丢弃 + camelCase→snake_case
        assert "enabled" not in out["task_experience"]
        assert out["task_experience"]["persist_dir"] == "./d"
        assert out["task_experience"]["seal_threshold"] == 7

    def test_migrate_idempotent(self):
        old = {"provider": {"p": {"options": {"apiKey": "k"}, "models": {"a": {}}}}}
        out1, _ = _migrate_legacy_config(old)
        out2, m2 = _migrate_legacy_config(out1)
        assert out1 == out2
        assert m2 is False  # 第二次无变化

    def test_new_format_passes_through(self):
        new = {"providers": {"dashscope": {"api_key": "k", "models": [{"id": "x"}]}}}
        out, migrated = _migrate_legacy_config(new)
        assert migrated is False
        assert out == new
