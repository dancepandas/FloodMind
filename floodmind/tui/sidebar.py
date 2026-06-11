"""FloodMind TUI — SessionSidebar (OpenCode-style).

使用 ThemeManager 语义颜色系统。
"""

from rich.text import Text
from textual.widgets import Static
from textual.containers import VerticalScroll

from floodmind.config.settings import settings
from floodmind.tui.theme import get_color


class SessionSidebar(VerticalScroll):

    DEFAULT_CSS = """
    SessionSidebar {
        width: 38;
        background: #14141f;
        padding: 1 2;
        border-left: solid #2d2d3d;
    }
    """

    def __init__(self, session_title: str = "", **kwargs):
        super().__init__(**kwargs)
        self._title = session_title
        self._model = settings.model.model_name
        self._sessions = []

    def update_title(self, title: str) -> None:
        self._title = title
        self._refresh()

    def update_model(self, model: str) -> None:
        self._model = model
        self._refresh()

    def refresh_sessions(self, sessions: list) -> None:
        self._sessions = sessions
        self._refresh()

    def _refresh(self) -> None:
        try:
            self.remove_children()
        except Exception:
            pass

        title_text = Text()
        title_text.append(self._title or "Untitled", style=f"bold {get_color('text')}")

        model_text = Text()
        model_text.append("Model: ", style=get_color("textMuted"))
        model_text.append(self._model, style=get_color("text"))

        version_text = Text()
        version_text.append("● ", style=get_color("success"))
        version_text.append("FloodMind ", style=f"bold {get_color('text')}")
        version_text.append("v1.0.0", style=get_color("textMuted"))

        # 使用唯一 ID 避免重复
        import uuid
        uid = str(uuid.uuid4())[:8]

        self.mount(Static(title_text, id=f"sidebar-title-{uid}"))
        self.mount(Static("", id=f"sidebar-sep-{uid}"))
        self.mount(Static(model_text, id=f"sidebar-info-{uid}"))

        if self._sessions:
            self.mount(Static("", id=f"sidebar-sessions-sep-{uid}"))
            lines = []
            for s in self._sessions[:10]:
                icon = "●" if s.get("msg_count", 0) > 0 else "○"
                t = s.get("title", "Untitled")[:25]
                lines.append(f"  {icon} {t}")
            self.mount(Static("\n".join(lines), id=f"sidebar-sessions-{uid}"))

        self.mount(Static("", id=f"sidebar-sep2-{uid}"))
        self.mount(Static(version_text, id=f"sidebar-footer-{uid}"))
