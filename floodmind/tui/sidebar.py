"""FloodMind TUI — SessionSidebar (OpenCode-style)."""

from rich.text import Text
from textual.widgets import Static
from textual.containers import VerticalScroll

from floodmind.config.settings import settings
from floodmind.tui.theme import C


class SessionSidebar(VerticalScroll):

    CSS = """
    SessionSidebar {
        width: 38;
        background: #141414;
        padding: 1 2;
        border-left: solid #3c3c3c;
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
        title_text.append(self._title or "Untitled", style=f"bold {C['text']}")

        model_text = Text()
        model_text.append("Model: ", style=C["text_muted"])
        model_text.append(self._model, style=C["text"])

        version_text = Text()
        version_text.append("● ", style=C["success"])
        version_text.append("FloodMind ", style=f"bold {C['text']}")
        version_text.append("v1.0.0", style=C["text_muted"])

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
