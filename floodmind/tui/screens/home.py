"""FloodMind TUI - HomeScreen (DEPRECATED: use simple_tui.py instead)."""

import uuid

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from floodmind.tui.widgets.logo import LogoWidget
from floodmind.tui.widgets.welcome import TipsWidget
from floodmind.tui.widgets.prompt import PromptInput
from floodmind.tui.widgets.footer import StatusBar
from floodmind.tui.screens.main import MainScreen


class HomePromptInput(PromptInput):
    BINDINGS = [Binding("escape", "app.quit", "Quit", key_display="esc")]


class HomeScreen(Screen[None]):
    BINDINGS = [
        Binding("ctrl+j,alt+enter", "send", "Send", key_display="^j"),
        Binding("ctrl+p", "command_palette", "Commands"),
        Binding("ctrl+o", "options", "Options"),
    ]

    CSS = """
    #home-content {
        height: 1fr;
        align: center middle;
    }
    #logo-container {
        width: 80; height: 5;
        content-align: center middle; margin-bottom: 2;
    }
    #tip-container {
        width: 72; height: 1;
        content-align: center middle; margin-bottom: 1;
    }
    #prompt-container {
        width: 72; height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield StatusBar()
        with Vertical(id="home-content"):
            with Vertical(id="logo-container"):
                yield LogoWidget()
            with Vertical(id="tip-container"):
                yield TipsWidget()
            with Vertical(id="prompt-container"):
                yield HomePromptInput()

    @on(PromptInput.PromptSubmitted)
    async def _on_submit(self, event: PromptInput.PromptSubmitted) -> None:
        session_id = uuid.uuid4().hex[:12]
        app = self.app
        await self.app.push_screen(
            MainScreen(
                host=getattr(app, "_host", "localhost"),
                port=getattr(app, "_port", 13014),
                model_hint=getattr(app, "_cli_model", ""),
                session_id=session_id,
                initial_text=event.text,
            )
        )

    def action_send(self) -> None:
        self.query_one(HomePromptInput).action_submit()

    def action_options(self) -> None:
        from floodmind.tui.dialogs.models import ModelsDialog
        self.app.push_screen(ModelsDialog())

    def action_command_palette(self) -> None:
        from floodmind.tui.dialogs.command_palette import CommandPaletteDialog
        self.app.push_screen(CommandPaletteDialog())
