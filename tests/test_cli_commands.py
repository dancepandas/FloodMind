"""FloodMind CLI 命令解析测试"""

import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import sys

import pytest
from click.testing import CliRunner

from floodmind import __version__
from floodmind.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestMainGroup:
    def test_help_shows_core_commands(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "config" in result.output
        assert "providers" in result.output
        # Web/TUI 命令已弃用移除：help 文本不应再列出这些入口
        assert "tui" not in result.output
        assert "web" not in result.output
        assert "serve" not in result.output

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output
    def test_help_and_version_do_not_initialize_home(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        script = """
import json, sys
from click.testing import CliRunner
from floodmind.cli import main
help_result = CliRunner().invoke(main, ["--help"])
version_result = CliRunner().invoke(main, ["--version"])
print(json.dumps({"help": help_result.exit_code, "version": version_result.exit_code,
                  "settings_loaded": "floodmind.config.settings" in sys.modules}))
"""
        env = os.environ.copy()
        env.update({"HOME": str(home), "USERPROFILE": str(home), "FLOODMIND_HOME": str(home / ".floodmind")})
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path.cwd(), env=env,
            text=True, capture_output=True, check=True,
        )
        assert json.loads(result.stdout.strip()) == {
            "help": 0, "version": 0, "settings_loaded": False,
        }
        assert not (home / ".floodmind").exists()

    def test_config_show_initializes_and_migrates_once(self, tmp_path):
        home = tmp_path / "state"
        home.mkdir()
        legacy = {
            "provider": {"p": {"options": {"apiKey": "secret"}, "models": {"m": {}}}},
        }
        (home / "settings.json").write_text(json.dumps(legacy), encoding="utf-8")
        script = """
import json
from click.testing import CliRunner
from floodmind.cli import main
runner = CliRunner()
first = runner.invoke(main, ["config", "show"])
second = runner.invoke(main, ["config", "show"])
print(json.dumps({"first": first.exit_code, "second": second.exit_code}))
"""
        env = os.environ.copy()
        env.update({"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"), "FLOODMIND_HOME": str(home)})
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path.cwd(), env=env,
            text=True, capture_output=True, check=True,
        )
        assert json.loads(result.stdout.strip()) == {"first": 0, "second": 0}
        migrated = json.loads((home / "settings.json").read_text(encoding="utf-8"))
        assert "providers" in migrated and "provider" not in migrated
        assert len(list(home.glob("settings.json.bak.*"))) == 1


class TestChatCommand:
    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_chat_legacy", return_value=0)
    def test_chat_default_text(self, mock_chat, mock_validate, runner):
        result = runner.invoke(main, ["chat"])
        assert result.exit_code == 0
        mock_chat.assert_called_once()


class TestRunCommand:
    def test_build_cli_workspace_uses_invocation_cwd(self, tmp_path, monkeypatch):
        from floodmind.cli import _build_cli_workspace

        monkeypatch.chdir(tmp_path)
        ws = _build_cli_workspace("cli-test")

        assert ws.mode == "folder_first"
        assert ws.default_cwd == tmp_path.resolve()
        assert ws.artifact_dir == tmp_path.resolve() / ".floodmind" / "artifacts" / "cli-test"

    def test_run_requires_session_for_checkpoint_resume(self, runner):
        result = runner.invoke(
            main,
            ["run", "继续任务", "--checkpoint", "ckpt-1"],
        )

        assert result.exit_code == 2
        assert "--checkpoint 必须配合 --resume" in result.output

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._build_cli_workspace")
    @patch("floodmind.agent.native.model_client.ModelClient.from_settings")
    @patch("floodmind.agent.create_flood_agent")
    def test_run_builds_cli_workspace_for_session(
        self, mock_create_agent, mock_model_client, mock_build_workspace, mock_validate, runner, tmp_path
    ):
        from floodmind.agent.runtime.services.workspace_service import build_folder_workspace

        ws = build_folder_workspace("test", primary_dir=tmp_path)
        mock_build_workspace.return_value = ws
        mock_create_agent.return_value.run_with_resume.return_value = "done"
        result = runner.invoke(main, ["run", "生成报告"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "done" in result.output
        mock_build_workspace.assert_called_once()
        assert mock_build_workspace.call_args.args[0].startswith("cli-run-")


class TestNoArgsMenu:
    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli_interactive.show_menu", return_value="q")
    def test_quit_choice_exits_0(self, mock_menu, mock_validate, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli_interactive.show_menu", return_value="r")
    def test_run_choice_shows_run_hint(self, mock_menu, mock_validate, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert "floodmind run" in result.output

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_chat_legacy", return_value=0)
    @patch("floodmind.cli_interactive.show_menu", return_value="c")
    def test_chat_choice_runs_chat(self, mock_menu, mock_chat, mock_validate, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        mock_chat.assert_called_once()
