"""FloodMind TUI — Sidebar widget (Slot-based).

重构要点：
- 从硬编码改为 SlotRegistry 插槽容器
- 支持动态注册/注销内容块
- 固定位置，空间记忆稳定
- 语义化颜色系统
"""

from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static
from rich.text import Text

from floodmind.tui.theme_manager import ThemeManager


class Sidebar(VerticalScroll):
    """侧边栏 — 插槽容器，支持动态扩展。"""

    can_focus = True

    session_title = reactive("Session")
    model_name = reactive("")
    version = reactive("v1.0.0")
    # Token 统计
    usage_total = reactive(0)
    usage_input = reactive(0)
    usage_output = reactive(0)
    usage_reasoning = reactive(0)

    DEFAULT_CSS = """
    Sidebar {
        width: 25;
        min-width: 20;
        max-width: 35;
        border-right: solid #2d2d3d;
        background: #1a1a2e;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._theme = ThemeManager()

    def on_mount(self) -> None:
        self._refresh()

    def watch_session_title(self) -> None:
        self._refresh()

    def watch_model_name(self) -> None:
        self._refresh()

    def watch_version(self) -> None:
        self._refresh()

    def watch_usage_total(self) -> None:
        self._refresh()

    def watch_usage_input(self) -> None:
        self._refresh()

    def watch_usage_output(self) -> None:
        self._refresh()

    def watch_usage_reasoning(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        try:
            self.remove_children()
        except Exception:
            pass

        # 从 SlotRegistry 获取插槽内容
        app = self.app
        if hasattr(app, "slots"):
            # sidebar_top
            for widget in app.slots.get("sidebar_top"):
                self.mount(widget)
            # sidebar_content
            for widget in app.slots.get("sidebar_content"):
                self.mount(widget)
            # sidebar_bottom
            for widget in app.slots.get("sidebar_bottom"):
                self.mount(widget)
        else:
            # fallback：渲染默认内容
            self._render_default()

    def _render_default(self) -> None:
        """渲染默认内容（无插槽时）。"""
        self.mount(self._build_header())
        self.mount(self._build_session_list())
        self.mount(self._build_footer())

    def _build_header(self) -> Static:
        """构建标题块。"""
        title = Text()
        title.append("◆ FloodMind\n", style=f"bold {self._theme.color('primary')}")
        title.append(f"  {self.model_name or 'No Model'}\n", style=self._theme.color("textMuted"))
        title.append(f"  {self.session_title}", style=self._theme.color("text"))
        return Static(title)

    def _build_session_list(self) -> Static:
        """构建会话列表块。"""
        text = Text()
        text.append("\n【会话】\n", style=f"bold {self._theme.color('primary')}")
        text.append("  • 当前会话\n", style=self._theme.color("text"))
        text.append("  ○ 历史会话\n", style=self._theme.color("textMuted"))
        return Static(text)

    def _build_footer(self) -> Static:
        """构建底部统计块。"""
        text = Text()
        text.append("\n【Token】\n", style=f"bold {self._theme.color('primary')}")
        text.append(f"  ↑ {self.usage_input}\n", style=self._theme.color("info"))
        text.append(f"  ↓ {self.usage_output}\n", style=self._theme.color("success"))
        text.append(f"  Σ {self.usage_total}\n", style=self._theme.color("text"))
        return Static(text)
