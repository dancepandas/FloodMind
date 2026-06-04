"""
FloodMind TUI — /models dialog.
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Header, Button, OptionList
from textual.containers import Horizontal
from textual.binding import Binding

from floodmind.config.model_presets import get_models_list


class ModelsDialog(ModalScreen[str]):
    """Model selection dialog. Returns selected model_key on dismiss."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self):
        super().__init__()
        self._model_keys = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("[bold]Select Model[/bold]", id="models-title")
        yield OptionList(id="models-list")
        with Horizontal(id="models-btns"):
            yield Button("Close [esc]", variant="default")

    def on_mount(self) -> None:
        ol = self.query_one("#models-list", OptionList)
        models = get_models_list()
        self._model_keys = [m["key"] for m in models]
        for m in models:
            label = m['label']
            if m.get("is_default"):
                label += "  (default)"
            ol.add_option(label)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_index is not None and event.option_index < len(self._model_keys):
            self.dismiss(self._model_keys[event.option_index])
        else:
            self.dismiss("")

    def on_button_pressed(self) -> None:
        self.dismiss("")

    def action_close(self) -> None:
        self.dismiss("")

    CSS = """
    ModelsDialog { align: center middle; }
    #models-title {
        width: 50;
        padding: 1 2 0 2;
        background: $surface;
        border: solid $accent;
        border-bottom: none;
    }
    #models-list {
        width: 50;
        max-height: 16;
        border: solid $accent;
        border-top: none;
        border-bottom: none;
    }
    #models-btns {
        width: 50;
        align-horizontal: right;
        padding: 0 2 1 2;
        background: $surface;
        border: solid $accent;
        border-top: none;
    }
    """
