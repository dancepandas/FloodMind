"""G-11：mcp.json 单一契约——项目级文件不被加载，条目非法在启动点显式报错。"""

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from floodmind.agent.mcp_client import McpClientPool, McpConnectionError
from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.config.settings import (
    McpConfigError,
    McpServerConfig,
    load_mcp_config,
    save_mcp_config,
    settings,
)

STDIO_SERVER = {
    "name": "rag",
    "transport": "stdio",
    "command": "python",
    "args": ["server.py"],
}


def _write_mcp(home: Path, servers) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "mcp.json"
    path.write_text(
        json.dumps({"servers": servers}, ensure_ascii=False), encoding="utf-8"
    )
    return path


def _patched_home(home: Path):
    return patch.dict(os.environ, {"FLOODMIND_HOME": str(home)}, clear=False)


class TestLoadMcpConfig:
    def test_valid_config_is_returned_as_is(self, tmp_path):
        _write_mcp(tmp_path, [STDIO_SERVER])
        with _patched_home(tmp_path):
            cfg = load_mcp_config()
        assert cfg["servers"] == [STDIO_SERVER]

    def test_missing_file_yields_empty_servers(self, tmp_path):
        with _patched_home(tmp_path / "absent"):
            assert load_mcp_config()["servers"] == []

    def test_legacy_type_field_is_rejected_with_transport_hint(self, tmp_path):
        _write_mcp(tmp_path, [{"name": "rag", "type": "stdio", "command": "python"}])
        with _patched_home(tmp_path):
            with pytest.raises(McpConfigError, match="transport"):
                load_mcp_config()

    def test_missing_transport_is_rejected(self, tmp_path):
        _write_mcp(tmp_path, [{"name": "rag", "command": "python"}])
        with _patched_home(tmp_path):
            with pytest.raises(McpConfigError, match="transport"):
                load_mcp_config()

    def test_unknown_field_is_rejected(self, tmp_path):
        _write_mcp(tmp_path, [{**STDIO_SERVER, "timeout": 3}])
        with _patched_home(tmp_path):
            with pytest.raises(McpConfigError, match="未知字段: timeout"):
                load_mcp_config()

    def test_transport_specific_requirements(self, tmp_path):
        _write_mcp(tmp_path, [{"name": "remote", "transport": "sse"}])
        with _patched_home(tmp_path):
            with pytest.raises(McpConfigError, match="url"):
                load_mcp_config()
        _write_mcp(tmp_path, [{"name": "local", "transport": "stdio"}])
        with _patched_home(tmp_path):
            with pytest.raises(McpConfigError, match="command"):
                load_mcp_config()

    def test_duplicate_names_are_rejected(self, tmp_path):
        _write_mcp(tmp_path, [STDIO_SERVER, dict(STDIO_SERVER)])
        with _patched_home(tmp_path):
            with pytest.raises(McpConfigError, match="name 重复"):
                load_mcp_config()

    def test_malformed_json_is_reported_not_silently_ignored(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "mcp.json").write_text("{not json", encoding="utf-8")
        with _patched_home(tmp_path):
            with pytest.raises(McpConfigError, match="合法 JSON"):
                load_mcp_config()

    def test_env_override_is_validated(self, tmp_path):
        with _patched_home(tmp_path):
            with patch.dict(
                os.environ,
                {"MCP_SERVERS": json.dumps([{"name": "x", "type": "sse"}])},
                clear=False,
            ):
                with pytest.raises(McpConfigError, match="MCP_SERVERS"):
                    load_mcp_config()
            with patch.dict(os.environ, {"MCP_SERVERS": "not-json"}, clear=False):
                with pytest.raises(McpConfigError, match="MCP_SERVERS"):
                    load_mcp_config()


class TestMcpServerConfig:
    def test_disabled_servers_are_not_exposed(self, tmp_path):
        _write_mcp(tmp_path, [STDIO_SERVER, {**STDIO_SERVER, "name": "off", "enabled": False}])
        with _patched_home(tmp_path):
            config = McpServerConfig()
        assert config.error == ""
        assert [s["name"] for s in config.servers] == ["rag"]

    def test_invalid_config_sets_error_and_exposes_nothing(self, tmp_path):
        _write_mcp(tmp_path, [{"name": "rag", "type": "stdio", "command": "python"}])
        with _patched_home(tmp_path):
            config = McpServerConfig()
        assert "transport" in config.error
        assert config.servers == []

    def test_agent_init_raises_explicitly_on_invalid_config(self):
        agent = NativeFloodAgent.__new__(NativeFloodAgent)
        broken = SimpleNamespace(error="bad transport", servers=[])
        with patch.object(settings, "mcp", broken):
            with pytest.raises(McpConfigError, match="bad transport"):
                agent._load_mcp_tools()


class TestProjectConfigIsNotLoaded:
    def test_project_mcp_json_is_warned_about(self, tmp_path, monkeypatch, caplog):
        project = tmp_path / "project"
        (project / ".floodmind").mkdir(parents=True)
        (project / ".floodmind" / "mcp.json").write_text(
            json.dumps({"servers": [{"name": "p", "type": "stdio", "command": "x"}]}),
            encoding="utf-8",
        )
        home = tmp_path / "home"
        _write_mcp(home, [STDIO_SERVER])
        monkeypatch.chdir(project)
        with _patched_home(home), caplog.at_level(logging.WARNING):
            cfg = load_mcp_config()
        assert cfg["servers"] == [STDIO_SERVER]
        assert "不会被加载" in caplog.text


class TestSaveMcpConfig:
    def test_invalid_entries_are_rejected_before_write(self, tmp_path):
        with _patched_home(tmp_path):
            with pytest.raises(McpConfigError):
                save_mcp_config({"servers": [{"name": "x", "type": "sse"}]})
            assert not (tmp_path / "mcp.json").exists()

    def test_valid_entries_round_trip(self, tmp_path):
        with _patched_home(tmp_path):
            save_mcp_config({"servers": [STDIO_SERVER]})
            assert load_mcp_config()["servers"] == [STDIO_SERVER]


class TestPoolRequiresExplicitTransport:
    def test_connect_server_without_transport_is_rejected(self):
        with pytest.raises(McpConnectionError, match="transport"):
            McpClientPool().connect_server({"name": "x", "command": "python"})
