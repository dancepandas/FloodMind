"""Tests for search_config module.

测试内容:
- Search 配置加载: 默认值、文件覆盖、环境变量覆盖
- Search 配置缓存: get_search_config() 缓存行为、invalidate 清除
- Search 配置写入: write_search_config 创建文件、更新缓存
- 隔离要求: 每个测试前必须清理全局缓存，避免跨测试污染
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from floodmind.config import search_config as sc


class BaseSearchConfigTest:
    """基类: 每个测试前清理 Search 全局缓存和环境变量，确保测试隔离。"""

    def setup_method(self):
        sc.invalidate_search_config()
        # 清理可能影响测试结果的环境变量
        for key in ("FLOODMIND_SEARCH_ENGINE", "FLOODMIND_SEARCH_URL",
                    "BAIDU_API_KEY", "FLOODMIND_SEARCH_API_KEY"):
            os.environ.pop(key, None)


class TestSearchConfigLoading(BaseSearchConfigTest):
    """Test search config load and merge logic."""

    def test_default_config_when_no_file(self):
        """Returns default config when search.json does not exist."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                cfg = sc.load_search_config()
                assert cfg["engine"] == "baidu_qianfan"
                assert "qianfan" in cfg["url"]
                assert cfg["api_key"] == ""

    def test_file_overrides_default(self):
        """search.json values override defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            fake_path.write_text(
                json.dumps({"engine": "custom", "url": "https://example.com/search"}),
                encoding="utf-8",
            )
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                cfg = sc.load_search_config()
                assert cfg["engine"] == "custom"
                assert cfg["url"] == "https://example.com/search"

    def test_env_var_overrides_file(self):
        """Environment variables have highest priority."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            fake_path.write_text(
                json.dumps({"engine": "baidu_qianfan", "api_key": "file_key"}),
                encoding="utf-8",
            )
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                with patch.dict(os.environ, {"BAIDU_API_KEY": "env_key"}):
                    cfg = sc.load_search_config()
                    assert cfg["api_key"] == "env_key"

    def test_floodmind_search_api_key_env(self):
        """FLOODMIND_SEARCH_API_KEY also works."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                with patch.dict(os.environ, {"FLOODMIND_SEARCH_API_KEY": "fm_key"}):
                    cfg = sc.load_search_config()
                    assert cfg["api_key"] == "fm_key"

    def test_baidu_api_key_takes_precedence_over_floodmind(self):
        """BAIDU_API_KEY wins over FLOODMIND_SEARCH_API_KEY when both set."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                env = {"BAIDU_API_KEY": "baidu_val", "FLOODMIND_SEARCH_API_KEY": "fm_val"}
                with patch.dict(os.environ, env):
                    cfg = sc.load_search_config()
                    assert cfg["api_key"] == "baidu_val"

    def test_env_url_override(self):
        """FLOODMIND_SEARCH_URL overrides file url."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            fake_path.write_text(json.dumps({"url": "https://a.com"}), encoding="utf-8")
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                with patch.dict(os.environ, {"FLOODMIND_SEARCH_URL": "https://b.com"}):
                    cfg = sc.load_search_config()
                    assert cfg["url"] == "https://b.com"

    def test_malformed_json_returns_defaults(self):
        """Malformed JSON falls back to defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            fake_path.write_text("not json", encoding="utf-8")
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                cfg = sc.load_search_config()
                assert cfg["engine"] == "baidu_qianfan"

    def test_non_dict_json_returns_defaults(self):
        """JSON array falls back to defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            fake_path.write_text("[1, 2, 3]", encoding="utf-8")
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                cfg = sc.load_search_config()
                assert isinstance(cfg, dict)


class TestSearchConfigCache(BaseSearchConfigTest):
    """Test config caching behavior."""

    def test_get_search_config_caches(self):
        """get_search_config caches result."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            fake_path.write_text(json.dumps({"engine": "cached"}), encoding="utf-8")
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                sc.invalidate_search_config()
                cfg1 = sc.get_search_config()
                cfg2 = sc.get_search_config()
                assert cfg1 is cfg2

    def test_invalidate_clears_cache(self):
        """invalidate_search_config clears cache."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            fake_path.write_text(json.dumps({"engine": "v1"}), encoding="utf-8")
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                sc.invalidate_search_config()
                cfg1 = sc.get_search_config()
                fake_path.write_text(json.dumps({"engine": "v2"}), encoding="utf-8")
                sc.invalidate_search_config()
                cfg2 = sc.get_search_config()
                assert cfg2["engine"] == "v2"


class TestSearchConfigWrite(BaseSearchConfigTest):
    """Test write_search_config."""

    def test_write_creates_file(self):
        """write_search_config creates search.json."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                path = sc.write_search_config({"engine": "bing", "api_key": "key123"})
                assert path.exists()
                data = json.loads(path.read_text("utf-8"))
                assert data["engine"] == "bing"
                assert data["api_key"] == "key123"

    def test_write_invalidate_cache(self):
        """write_search_config invalidates cache."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "search.json"
            with patch.object(sc, "_GLOBAL_SEARCH_PATH", fake_path):
                sc.write_search_config({"engine": "old"})
                sc.get_search_config()
                sc.write_search_config({"engine": "new"})
                cfg = sc.get_search_config()
                assert cfg["engine"] == "new"
