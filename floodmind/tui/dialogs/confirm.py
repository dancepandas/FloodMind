"""
FloodMind TUI — confirm dialog.
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Button, Header
from textual.containers import Center, Vertical, Horizontal


class ConfirmDialog(ModalScreen[bool]):
    """A simple confirm/cancel dialog."""

    def __init__(self, title: str, message: str, cancel_label: str = "Cancel"):
        super().__init__()
        self._title = title
        self._message = message
        self._cancel_label = cancel_label

    def compose(self) -> ComposeResult:
        yield Header()
        with Center():
            with Vertical(id="confirm-box"):
                yield Static(f"[bold]{self._title}[/bold]\n\n{self._message}", id="confirm-msg")
                with Horizontal(id="confirm-btns"):
                    yield Button(self._cancel_label, variant="default", id="btn-cancel")
                    yield Button("OK", variant="primary", id="btn-ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-ok":
            self.dismiss(True)
        else:
            self.dismiss(False)

    CSS = """
    #confirm-box {
        margin-top: 8;
        padding: 2 4;
        border: solid $accent;
        background: $surface;
        min-width: 50;
    }
    #confirm-msg { margin-bottom: 2; }
    #confirm-btns { align-horizontal: right; }
    Button { margin-right: 1; }
    """
