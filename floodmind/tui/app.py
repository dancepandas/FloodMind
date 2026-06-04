"""FloodMind TUI — main App (OpenCode-style)."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding

from floodmind.config.settings import settings
from floodmind.tui.screens.home import HomeScreen


class FloodMindTui(App):
    CSS_PATH = Path(__file__).parent / "tui.css"

    BINDINGS = [
        Binding("ctrl+p", "command_palette", "Commands", show=False),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, model: str = "", reasoning: bool = False):
        super().__init__()
        self._cli_model = model
        self._cli_reasoning = reasoning

    def on_mount(self) -> None:
        self.title = "FloodMind"
        self.sub_title = settings.model.model_name
        if self._cli_model:
            settings.model.model_name = self._cli_model
        self.push_screen(HomeScreen())

    def action_help(self) -> None:
        from floodmind.tui.dialogs.help import HelpDialog
        self.push_screen(HelpDialog())

    def action_command_palette(self) -> None:
        from floodmind.tui.dialogs.command_palette import CommandPaletteDialog
        self.push_screen(CommandPaletteDialog())


def run_tui(model: str = "", reasoning: bool = False) -> None:
    if model:
        settings.model.model_name = model
    if reasoning:
        settings.model.enable_reasoning = reasoning
    FloodMindTui(model=model, reasoning=reasoning).run()
