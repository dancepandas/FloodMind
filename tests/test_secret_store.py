"""G-06b：配置文件敏感值静态加密（DPAPI / Fernet）。"""

import json
import os
from unittest.mock import patch

import pytest

from floodmind.config import search_config as sc
from floodmind.config.secret_store import (
    ENC_PREFIX,
    is_encrypted,
    protect,
    protect_tree,
    unprotect,
    unprotect_tree,
)


@pytest.fixture
def floodmind_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOODMIND_HOME", str(tmp_path))
    return tmp_path


class TestValueEncryption:
    def test_roundtrip(self, floodmind_home):
        cipher = protect("sk-live-secret")
        assert cipher.startswith(ENC_PREFIX)
        assert "sk-live-secret" not in cipher
        assert unprotect(cipher) == "sk-live-secret"

    def test_plaintext_passthrough(self, floodmind_home):
        assert protect("") == ""
        assert unprotect("plain") == "plain"

    def test_protect_is_idempotent(self, floodmind_home):
        cipher = protect("value")
        assert protect(cipher) == cipher

    def test_corrupt_ciphertext_is_fail_closed(self, floodmind_home):
        assert not is_encrypted("plain")
        assert unprotect(ENC_PREFIX + "not-valid-base64!!") == ""


class TestConfigTree:
    def test_protect_tree_only_touches_sensitive_fields(self, floodmind_home):
        cfg = {
            "providers": {
                "p": {"api_key": "sk", "base_url": "https://x/v1", "models": [{"id": "m"}]},
            },
            "gateway": {"auth_token": "fmgt-abc", "port": 8000},
        }

        protected = protect_tree(cfg)

        assert protected["providers"]["p"]["api_key"].startswith(ENC_PREFIX)
        assert protected["providers"]["p"]["base_url"] == "https://x/v1"
        assert protected["gateway"]["auth_token"].startswith(ENC_PREFIX)
        assert protected["gateway"]["port"] == 8000
        # 不修改入参
        assert cfg["providers"]["p"]["api_key"] == "sk"

        restored = unprotect_tree(protected)
        assert restored == cfg

    def test_headers_and_env_are_protected_by_name(self, floodmind_home):
        cfg = {
            "servers": [{
                "name": "s",
                "transport": "stdio",
                "command": "python",
                "headers": {
                    "Authorization": "Bearer abc",
                    "X-Api-Key": "k",
                    "Accept": "application/json",
                },
                "env": {"GITHUB_TOKEN": "gh", "NODE_ENV": "production"},
            }]
        }

        protected = protect_tree(cfg)["servers"][0]

        assert protected["headers"]["Authorization"].startswith(ENC_PREFIX)
        assert protected["headers"]["X-Api-Key"].startswith(ENC_PREFIX)
        assert protected["headers"]["Accept"] == "application/json"
        assert protected["env"]["GITHUB_TOKEN"].startswith(ENC_PREFIX)
        assert protected["env"]["NODE_ENV"] == "production"

        restored = unprotect_tree({"servers": [protected]})["servers"][0]
        assert restored["headers"]["Authorization"] == "Bearer abc"
        assert restored["env"]["GITHUB_TOKEN"] == "gh"


class TestSettingsPersistence:
    def test_save_encrypts_and_load_decrypts(self, floodmind_home):
        from floodmind.config.settings import reload_config, save_config

        save_config({
            "providers": {"p": {"api_key": "sk-live", "base_url": "https://x/v1"}},
        })

        raw = json.loads((floodmind_home / "settings.json").read_text(encoding="utf-8"))
        assert raw["providers"]["p"]["api_key"].startswith(ENC_PREFIX)
        assert raw["providers"]["p"]["base_url"] == "https://x/v1"

        cfg = reload_config()
        assert cfg["providers"]["p"]["api_key"] == "sk-live"


class TestSearchPersistence:
    def test_search_key_encrypted_at_rest(self, floodmind_home, tmp_path, monkeypatch):
        # 隔离宿主 .env / CLI 的 load_dotenv()：搜索配置允许环境变量覆盖，
        # 否则测试结果会取决于此前是否导入过 floodmind.cli。
        monkeypatch.delenv("BAIDU_API_KEY", raising=False)
        monkeypatch.delenv("FLOODMIND_SEARCH_API_KEY", raising=False)
        path = tmp_path / "search.json"
        sc.invalidate_search_config()
        with patch.object(sc, "_GLOBAL_SEARCH_PATH", path):
            sc.write_search_config({"engine": "baidu_qianfan", "api_key": "baidu-key"})
            raw = json.loads(path.read_text(encoding="utf-8"))
            assert raw["api_key"].startswith(ENC_PREFIX)
            sc.invalidate_search_config()
            assert sc.load_search_config()["api_key"] == "baidu-key"


class TestMcpPersistence:
    def test_headers_and_env_encrypted_at_rest(self, floodmind_home):
        from floodmind.config.settings import load_mcp_config, save_mcp_config

        save_mcp_config({"servers": [
            {
                "name": "remote",
                "transport": "sse",
                "url": "https://example.com/sse",
                "headers": {"Authorization": "Bearer abc", "Accept": "application/json"},
            },
            {
                "name": "local",
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "server"],
                "env": {"GITHUB_TOKEN": "gh", "NODE_ENV": "production"},
            },
        ]})

        raw = json.loads((floodmind_home / "mcp.json").read_text(encoding="utf-8"))
        remote, local = raw["servers"]
        assert remote["headers"]["Authorization"].startswith(ENC_PREFIX)
        assert remote["headers"]["Accept"] == "application/json"
        assert local["env"]["GITHUB_TOKEN"].startswith(ENC_PREFIX)
        assert local["env"]["NODE_ENV"] == "production"

        loaded = load_mcp_config()["servers"]
        assert loaded[0]["headers"]["Authorization"] == "Bearer abc"
        assert loaded[1]["env"]["GITHUB_TOKEN"] == "gh"


class TestGatewayToken:
    def test_token_encrypted_at_rest_and_stable_across_calls(self, floodmind_home, tmp_path):
        from floodmind.gateway import state

        settings_file = tmp_path / "settings.json"
        with patch.object(state, "_SETTINGS_FILE", settings_file), \
                patch.dict(os.environ, {"FLOODMIND_GATEWAY_TOKEN": ""}):
            first = state.resolve_auth_token()
            raw = json.loads(settings_file.read_text(encoding="utf-8"))
            assert raw["gateway"]["auth_token"].startswith(ENC_PREFIX)
            assert raw["gateway"]["auth_token"] != first
            assert state.resolve_auth_token() == first
