"""
FloodMind TUI — /sessions dialog.
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Header, Button, OptionList
from textual.containers import Horizontal
from textual.binding import Binding

from floodmind.memory import list_sessions as list_sessions_store


class SessionsDialog(ModalScreen[str]):
    """Session list dialog. Returns selected session_id on dismiss."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("[bold]Sessions[/bold]", id="sessions-title")
        yield OptionList(id="sessions-list")
        with Horizontal(id="sessions-btns"):
            yield Button("Close [esc]", variant="default")

    def on_mount(self) -> None:
        ol = self.query_one("#sessions-list", OptionList)
        sessions = list_sessions_store()
        if not sessions:
            ol.add_option(("(no sessions)", ""))
            return
        for s in sessions:
            sid = s.get("id", "")
            title = s.get("title") or "Untitled"
            n = s.get("msg_count", 0)
            ts = s.get("updated_at", "")[:19]
            label = f"{title[:40]}  —  {n} msgs  ·  {ts}"
            ol.add_option((label, sid))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.dismiss(event.option_id)

    def on_button_pressed(self) -> None:
        self.dismiss("")

    def action_close(self) -> None:
        self.dismiss("")

    CSS = """
    SessionsDialog { align: center middle; }
    #sessions-title {
        width: 56;
        padding: 1 2 0 2;
        background: #1a1a2e;
        border: solid #7c6fae;
        border-bottom: none;
    }
    #sessions-list {
        width: 56;
        max-height: 16;
        border: solid #7c6fae;
        border-top: none;
        border-bottom: none;
    }
    #sessions-btns {
        width: 56;
        align-horizontal: right;
        padding: 0 2 1 2;
        background: #1a1a2e;
        border: solid #7c6fae;
        border-top: none;
    }
    """
