"""FloodMind TUI — PromptInput widget."""

from textual import on, events
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import TextArea

from floodmind.tui.theme import C


class PromptInput(TextArea):
    class PromptSubmitted(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    class CursorEscapingTop(Message):
        pass

    BINDINGS = [
        Binding("enter", "submit_enter", "Send"),
        Binding("ctrl+j,alt+enter", "submit", "Send", key_display="^j"),
    ]

    submit_ready = reactive(True)

    def __init__(self, **kwargs):
        super().__init__(language="markdown", show_line_numbers=False, **kwargs)

    def on_mount(self) -> None:
        self.border_title = "Message"
        self.border_subtitle = "Enter to send"

    @on(TextArea.Changed)
    def _on_change(self, event: TextArea.Changed) -> None:
        self.border_subtitle = "Enter to send" if event.text_area.text.strip() else ""

    def on_key(self, event: events.Key) -> None:
        if self.cursor_location == (0, 0) and event.key == "up":
            event.prevent_default()
            self.post_message(self.CursorEscapingTop())
            event.stop()

    def watch_submit_ready(self, ready: bool) -> None:
        self.set_class(not ready, "-submit-blocked")

    def action_submit_enter(self) -> None:
        self.action_submit()

    def action_submit(self) -> None:
        text = self.text.strip()
        if not text:
            return
        if self.submit_ready:
            self.clear()
            self.post_message(self.PromptSubmitted(text))
        else:
            self.notify("请等待响应完成", severity="warning")
