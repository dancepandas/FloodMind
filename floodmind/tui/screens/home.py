"""FloodMind TUI - HomeScreen (OpenCode-style centered welcome)."""

import uuid

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Middle, Horizontal
from textual.screen import Screen
from textual.widgets import Static

from floodmind.tui.widgets.logo import LogoWidget
from floodmind.tui.widgets.welcome import TipsWidget
from floodmind.tui.widgets.prompt import PromptInput
from floodmind.tui.widgets.footer import StatusBar
from floodmind.tui.screens.chat import ChatScreen


class HomePromptInput(PromptInput):
    BINDINGS = [Binding("escape", "app.quit", "Quit", key_display="esc")]


class HomeScreen(Screen[None]):
    BINDINGS = [
        Binding("ctrl+j,alt+enter", "send", "Send", key_display="^j"),
        Binding("ctrl+p", "command_palette", "Commands"),
        Binding("ctrl+o", "options", "Options"),
    ]

    CSS = """
    HomeScreen {
        background: #0a0a0a;
    }

    #home-content {
        height: 1fr;
        align: center middle;
    }

    #logo-container {
        width: 72;
        height: 5;
        content-align: center middle;
        margin-bottom: 2;
    }

    #tip-container {
        width: 72;
        height: 1;
        content-align: center middle;
        margin-bottom: 1;
    }

    #prompt-container {
        width: 72;
        height: auto;
    }

    HomePromptInput {
        width: 100%;
        height: auto;
        max-height: 12;
        padding: 1 2;
        border: solid #484848;
        background: #141414;
        color: #eeeeee;
    }

    HomePromptInput:focus {
        border: solid #9d7cd8;
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
        await self.app.push_screen(ChatScreen(session_id, event.text))

    def action_send(self) -> None:
        self.query_one(HomePromptInput).action_submit()

    def action_options(self) -> None:
        from floodmind.tui.dialogs.models import ModelsDialog
        self.app.push_screen(ModelsDialog())

    def action_command_palette(self) -> None:
        from floodmind.tui.dialogs.command_palette import CommandPaletteDialog
        self.app.push_screen(CommandPaletteDialog())
