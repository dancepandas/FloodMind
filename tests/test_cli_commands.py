"""FloodMind CLI 命令解析测试"""

from unittest.mock import patch

import sys

import pytest
from click.testing import CliRunner

from floodmind.cli import main


_LEGACY_MODULES = ("floodmind.tui", "floodmind.server", "flask", "textual")


def _forget_legacy_modules():
    for name in _LEGACY_MODULES:
        sys.modules.pop(name, None)


def _assert_legacy_not_imported():
    for name in _LEGACY_MODULES:
        assert name not in sys.modules


@pytest.fixture(autouse=True)
def clean_legacy_modules():
    _forget_legacy_modules()
    yield


@pytest.fixture
def runner():
    return CliRunner()


class TestMainGroup:
    def test_help_shows_core_and_legacy_commands(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "config" in result.output
        assert "providers" in result.output
        assert "tui" in result.output
        assert "web" in result.output
        assert "serve" in result.output

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "1.1.6" in result.output


class TestTuiCommand:
    @patch("floodmind.cli._validate_api_key")
    def test_tui_reports_legacy_notice(self, mock_validate, runner):
        result = runner.invoke(main, ["tui"])
        assert result.exit_code == 0
        assert "legacy" in result.output
        assert "floodmind run" in result.output
        mock_validate.assert_not_called()
        _assert_legacy_not_imported()

    @patch("floodmind.cli._validate_api_key")
    def test_tui_with_port_reports_legacy_notice(self, mock_validate, runner):
        result = runner.invoke(main, ["tui", "--port", "8080"])
        assert result.exit_code == 0
        assert "legacy" in result.output
        mock_validate.assert_not_called()
        _assert_legacy_not_imported()


class TestWebCommand:
    @patch("floodmind.cli._validate_api_key", return_value=None)
    def test_web_reports_legacy_notice(self, mock_validate, runner):
        result = runner.invoke(main, ["web"])
        assert result.exit_code == 0
        assert "legacy" in result.output
        assert "floodmind run" in result.output
        mock_validate.assert_not_called()
        _assert_legacy_not_imported()

    @patch("floodmind.cli._validate_api_key", return_value=None)
    def test_web_no_browser_reports_legacy_notice(self, mock_validate, runner):
        result = runner.invoke(main, ["web", "--no-browser"])
        assert result.exit_code == 0
        assert "legacy" in result.output
        mock_validate.assert_not_called()
        _assert_legacy_not_imported()


class TestServeCommand:
    @patch("floodmind.cli._validate_api_key")
    def test_serve_reports_legacy_notice(self, mock_validate, runner):
        result = runner.invoke(main, ["serve"])
        assert result.exit_code == 0
        assert "legacy" in result.output
        assert "floodmind run" in result.output
        mock_validate.assert_not_called()
        _assert_legacy_not_imported()


class TestChatCommand:
    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_chat_legacy", return_value=0)
    def test_chat_default_text(self, mock_chat, mock_validate, runner):
        result = runner.invoke(main, ["chat"])
        assert result.exit_code == 0
        mock_chat.assert_called_once()

    @patch("floodmind.cli._validate_api_key")
    def test_chat_with_tui_flag_reports_legacy_notice(self, mock_validate, runner):
        result = runner.invoke(main, ["chat", "--tui"])
        assert result.exit_code == 0
        assert "legacy" in result.output
        assert "floodmind run" in result.output
        mock_validate.assert_not_called()
        _assert_legacy_not_imported()

    @patch("floodmind.cli._validate_api_key")
    def test_chat_with_web_flag_reports_legacy_notice(self, mock_validate, runner):
        result = runner.invoke(main, ["chat", "--web"])
        assert result.exit_code == 0
        assert "legacy" in result.output
        assert "floodmind run" in result.output
        mock_validate.assert_not_called()
        _assert_legacy_not_imported()


class TestRunCommand:
    def test_build_cli_workspace_uses_invocation_cwd(self, tmp_path, monkeypatch):
        from floodmind.cli import _build_cli_workspace

        monkeypatch.chdir(tmp_path)
        ws = _build_cli_workspace("cli-test")

        assert ws.mode == "folder_first"
        assert ws.default_cwd == tmp_path.resolve()
        assert ws.artifact_dir == tmp_path.resolve() / ".floodmind" / "artifacts" / "cli-test"

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._build_cli_workspace")
    @patch("floodmind.agent.native.native_flood_agent.NativeFloodAgent.run_with_resume", return_value="done")
    def test_run_builds_cli_workspace_for_session(self, mock_run, mock_build_workspace, mock_validate, runner, tmp_path):
        from floodmind.agent.runtime.services.workspace_service import build_folder_workspace

        ws = build_folder_workspace("test", primary_dir=tmp_path)
        mock_build_workspace.return_value = ws
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
    @patch("floodmind.cli_interactive.show_menu", return_value="t")
    def test_tui_choice_reports_legacy_notice(self, mock_menu, mock_validate, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert "legacy" in result.output
        _assert_legacy_not_imported()

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli_interactive.show_menu", return_value="w")
    def test_web_choice_reports_legacy_notice(self, mock_menu, mock_validate, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert "legacy" in result.output
        _assert_legacy_not_imported()

    @patch("floodmind.cli._validate_api_key")
    @patch("floodmind.cli._run_chat_legacy", return_value=0)
    @patch("floodmind.cli_interactive.show_menu", return_value="c")
    def test_chat_choice_runs_chat(self, mock_menu, mock_chat, mock_validate, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        mock_chat.assert_called_once()


class TestFlagShortcuts:
    @patch("floodmind.cli._validate_api_key")
    def test_tui_flag_shortcut_reports_legacy_notice(self, mock_validate, runner):
        result = runner.invoke(main, ["--tui"])
        assert result.exit_code == 0
        assert "legacy" in result.output
        assert "floodmind run" in result.output
        mock_validate.assert_not_called()
        _assert_legacy_not_imported()

    @patch("floodmind.cli._validate_api_key")
    def test_web_flag_shortcut_reports_legacy_notice(self, mock_validate, runner):
        result = runner.invoke(main, ["--web"])
        assert result.exit_code == 0
        assert "legacy" in result.output
        assert "floodmind run" in result.output
        mock_validate.assert_not_called()
        _assert_legacy_not_imported()
