"""FloodMind TUI — ChatPanel widget.

聊天消息流，使用 VerticalScroll + Static widgets 实现流式输出支持。
遵循 tui-design-skill: 虚拟化长列表，颜色编码语义清晰。
使用 ThemeManager 语义颜色系统。
"""

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static
from rich.text import Text
from rich.markdown import Markdown
from rich.rule import Rule

from floodmind.tui.theme_manager import ThemeManager


class ChatPanel(VerticalScroll):
    """聊天面板 — 消息流显示区，支持流式追加。"""

    can_focus = True

    # reactive 状态
    streaming = reactive(False)
    message_count = reactive(0)

    DEFAULT_CSS = """
    ChatPanel {
        background: #0a0a0f;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("g,g", "goto_top", "顶部"),
        Binding("G", "goto_bottom", "底部"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._theme = ThemeManager()
        # 当前流式输出 widget 引用
        self._current_streaming_widget: Static | None = None

    def action_goto_top(self) -> None:
        self.scroll_home(animate=False)

    def action_goto_bottom(self) -> None:
        self.scroll_end(animate=False)

    def _border_color(self) -> str:
        return self._theme.get("border", "#2d2d3d")

    def write_user(self, text: str) -> None:
        """写入用户消息。"""
        border = self._border_color()
        primary = self._theme.get("primary", "#5f87ff")
        header = Text()
        header.append("┌─ ", style=border)
        header.append("You", style=f"bold {primary}")
        header.append(" ")
        header.append("─" * max(1, self.size.width - 12), style=border)
        self.mount(Static(header))
        self.mount(Static(Text(text, style=self._theme.get("text", "#e0e0e0"))))
        self.mount(Static(""))
        self.message_count += 1
        self.scroll_end(animate=False)

    def write_ai_start(self) -> None:
        """开始 AI 回复块，创建一个可流式更新的 Static widget。"""
        border = self._border_color()
        accent = self._theme.get("accent", "#7c6fae")
        header = Text()
        header.append("┌─ ", style=border)
        header.append("AI", style=f"bold {accent}")
        header.append(" ")
        header.append("─" * max(1, self.size.width - 10), style=border)
        self.mount(Static(header))
        self._current_streaming_widget = Static("")
        self.mount(self._current_streaming_widget)
        self.streaming = True
        self.scroll_end(animate=False)

    def append_ai_chunk(self, text: str) -> None:
        """追加 AI 回复内容 (流式) 到当前 widget。"""
        if self._current_streaming_widget is None:
            return
        current = self._current_streaming_widget.renderable
        if isinstance(current, Text):
            current_text = current.plain
        else:
            current_text = str(current or "")
        self._current_streaming_widget.update(Text(current_text + text, style=self._theme.get("text", "#e0e0e0")))
        self.scroll_end(animate=False)

    def write_ai_end(self) -> None:
        """结束 AI 回复块。将累积内容转为 Markdown 渲染。"""
        if self._current_streaming_widget is not None:
            content = str(self._current_streaming_widget.renderable or "")
            # 替换为 Markdown 渲染（如果内容非空）
            if content.strip():
                try:
                    md = Markdown(content.strip(), code_theme="monokai")
                    self._current_streaming_widget.update(md)
                except Exception:
                    pass
            self._current_streaming_widget = None
        self.mount(Static(Rule(style=self._border_color())))
        self.streaming = False
        self.message_count += 1
        self.scroll_end(animate=False)

    def write_system(self, text: str) -> None:
        """写入系统消息。"""
        warning = self._theme.get("warning", "#e5a443")
        t = Text()
        t.append("⚡ ", style=warning)
        t.append(text, style=warning)
        self.mount(Static(t))
        self.mount(Static(""))
        self.scroll_end(animate=False)

    def clear_chat(self) -> None:
        """清空聊天历史。"""
        try:
            self.remove_children()
        except Exception:
            pass
        self._current_streaming_widget = None
        self.message_count = 0
        self.streaming = False
