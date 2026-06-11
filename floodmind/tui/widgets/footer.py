"""FloodMind TUI — StatusBar widget.

使用 ThemeManager 语义颜色系统。
"""

from pathlib import Path

from textual.widget import Widget
from textual.reactive import reactive
from rich.text import Text

from floodmind.tui.theme import get_color


class StatusBar(Widget):
    can_focus = False
    model_name = reactive("")
    version = "v1.0.0"

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: #14141f;
        color: #808090;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cwd = Path.cwd().resolve()
        home = Path.home()
        try:
            if self._cwd.is_relative_to(home):
                rel = self._cwd.relative_to(home)
                self._display_path = f"~/{rel}" if rel != Path(".") else "~"
            else:
                self._display_path = self._cwd
        except Exception:
            self._display_path = self._cwd

    def on_mount(self) -> None:
        from floodmind.config.settings import settings
        self.model_name = settings.model.model_name

    def watch_model_name(self, name: str) -> None:
        self.refresh()

    def render(self):
        left = str(self._display_path)
        if len(left) > 40:
            left = left[:37] + "..."

        right = f"● {self.model_name} · {self.version}"

        text = Text()
        text.append(left, style=get_color("textMuted"))
        text.append(" " * max(1, self.size.width - len(left) - len(right)), style="")
        text.append(right, style=get_color("textMuted"))
        return text
