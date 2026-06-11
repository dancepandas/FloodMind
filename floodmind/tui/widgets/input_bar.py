"""FloodMind TUI — InputBar widget.

多行输入框，支持 Enter 发送、Shift+Enter 换行。
遵循 tui-design-skill: 聚焦时视觉反馈，占位符提示当前状态。
使用 ThemeManager 语义颜色系统。
"""

from textual import on
from textual.binding import Binding
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import TextArea

from floodmind.tui.theme_manager import ThemeManager


class InputBar(TextArea):
    """输入框 — 多行文本，Enter 发送，Shift+Enter 换行。"""

    class Submitted(Message):
        """用户提交消息事件。"""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class Cancelled(Message):
        """用户取消输入事件。"""
        pass

    BINDINGS = [
        Binding("ctrl+j", "submit", "发送", show=False),
        Binding("alt+enter", "submit", "发送", show=False),
    ]

    ready = reactive(True)

    DEFAULT_CSS = """
    InputBar {
        border: solid #2d2d3d;
        background: #1a1a2e;
        color: #e0e0e0;
        height: auto;
        max-height: 12;
        padding: 0 1;
    }
    InputBar:focus {
        border: solid #5f87ff;
    }
    InputBar.-busy {
        border: solid #e5a443;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(language="markdown", show_line_numbers=False, **kwargs)
        self._tui_theme = ThemeManager()

    def on_mount(self) -> None:
        self.border_title = "Message"
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        if self.ready:
            self.border_subtitle = "Enter 发送 · Shift+Enter 换行"
        else:
            self.border_subtitle = "等待响应..."

    def watch_ready(self, ready: bool) -> None:
        self.set_class(not ready, "-busy")
        self._update_subtitle()

    def on_key(self, event: Key) -> None:
        # Enter 发送；Shift+Enter 的 key 为 "shift+enter"，自然不拦截
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.action_submit()
            return

    def action_submit(self) -> None:
        text = self.text.strip()
        if not text:
            return
        if not self.ready:
            self.notify("请等待当前响应完成", severity="warning")
            return
        self.clear()
        self.post_message(self.Submitted(text))

    @on(TextArea.Changed)
    def _on_change(self, event: TextArea.Changed) -> None:
        if self.ready:
            has_text = bool(event.text_area.text.strip())
            self.border_subtitle = (
                "Enter 发送 · Shift+Enter 换行" if has_text else ""
            )
