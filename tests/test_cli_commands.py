"""FloodMind CLI 命令解析测试"""

from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from floodmind.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestMainGroup:
    def test_help_shows_all_commands(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "tui" in result.output
        assert "web" in result.output
        assert "chat" in result.output
        assert "serve" in result.output

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "1.0.0" in result.output


class TestTuiCommand:
    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_tui", return_value=0)
    def test_tui_invokes_run_tui(self, mock_run, mock_validate, runner):
        result = runner.invoke(main, ["tui"])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_tui", return_value=0)
    def test_tui_with_port(self, mock_run, mock_validate, runner):
        result = runner.invoke(main, ["tui", "--port", "8080"])
        assert result.exit_code == 0
        kwargs = mock_run.call_args.kwargs
        assert kwargs["port"] == 8080


class TestWebCommand:
    @patch("floodmind.cli._validate_api_key", return_value=None)
    @patch("floodmind.cli._run_web", return_value=0)
    def test_web_default(self, mock_run, mock_validate, runner):
        result = runner.invoke(main, ["web"])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    @patch("floodmind.cli._validate_api_key", return_value=None)
    @patch("floodmind.cli._run_web", return_value=0)
    def test_web_no_browser(self, mock_run, mock_validate, runner):
        result = runner.invoke(main, ["web", "--no-browser"])
        assert result.exit_code == 0
        kwargs = mock_run.call_args.kwargs
        assert kwargs["open_browser"] is False


class TestServeCommand:
    @patch("floodmind.cli._run_web", return_value=0)
    def test_serve_no_browser(self, mock_run, runner):
        result = runner.invoke(main, ["serve"])
        assert result.exit_code == 0
        kwargs = mock_run.call_args.kwargs
        assert kwargs["open_browser"] is False


class TestChatCommand:
    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_chat_legacy", return_value=0)
    def test_chat_default_text(self, mock_chat, mock_validate, runner):
        result = runner.invoke(main, ["chat"])
        assert result.exit_code == 0
        mock_chat.assert_called_once()

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_tui", return_value=0)
    def test_chat_with_tui_flag(self, mock_tui, mock_validate, runner):
        result = runner.invoke(main, ["chat", "--tui"])
        assert result.exit_code == 0
        mock_tui.assert_called_once()


class TestNoArgsMenu:
    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli_interactive.show_menu", return_value="q")
    def test_quit_choice_exits_0(self, mock_menu, mock_validate, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_tui", return_value=0)
    @patch("floodmind.cli_interactive.show_menu", return_value="t")
    def test_tui_choice_runs_tui(self, mock_menu, mock_run, mock_validate, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_web", return_value=0)
    @patch("floodmind.cli_interactive.show_menu", return_value="w")
    def test_web_choice_runs_web(self, mock_menu, mock_run, mock_validate, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_chat_legacy", return_value=0)
    @patch("floodmind.cli_interactive.show_menu", return_value="c")
    def test_chat_choice_runs_chat(self, mock_menu, mock_chat, mock_validate, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        mock_chat.assert_called_once()


class TestFlagShortcuts:
    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_tui", return_value=0)
    def test_tui_flag_shortcut(self, mock_run, mock_validate, runner):
        result = runner.invoke(main, ["--tui"])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_web", return_value=0)
    def test_web_flag_shortcut(self, mock_run, mock_validate, runner):
        result = runner.invoke(main, ["--web"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
