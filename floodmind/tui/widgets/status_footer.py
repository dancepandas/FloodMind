"""FloodMind TUI — StatusFooter widget.

底部状态栏，显示上下文相关的快捷键提示和状态信息。
支持 SlotRegistry 插槽注入。
遵循 tui-design-skill: Footer hints 是功能可发现性的核心工具。
使用 ThemeManager 语义颜色系统。
"""

from textual.widget import Widget
from textual.reactive import reactive
from rich.text import Text

from floodmind.tui.theme_manager import ThemeManager


class StatusFooter(Widget):
    """状态栏 — 始终显示 3-5 个最常用快捷键。"""

    can_focus = False

    hint = reactive("? 帮助 · / 命令 · 1-3 切换面板")
    status = reactive("")
    model_name = reactive("")

    DEFAULT_CSS = """
    StatusFooter {
        dock: bottom;
        height: 1;
        background: #14141f;
        color: #808090;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._theme = ThemeManager()

    def watch_hint(self) -> None:
        self.refresh()

    def watch_status(self) -> None:
        self.refresh()

    def watch_model_name(self) -> None:
        self.refresh()

    def render(self) -> Text:
        text = Text()

        # 左侧: 状态信息
        if self.status:
            text.append(self.status, style=self._theme.get("warning", "yellow"))
            text.append("  ", style="")

        # 中间: 快捷键提示 (核心可发现性)
        text.append(self.hint, style=self._theme.get("textMuted", "dim"))

        # 右侧: 模型名
        if self.model_name:
            left_len = (
                len(self.status) + 2 + len(self.hint)
                if self.status
                else len(self.hint)
            )
            right_str = f"● {self.model_name}"
            pad = max(1, self.size.width - left_len - len(right_str))
            text.append(" " * pad, style="")
            text.append(
                right_str,
                style=self._theme.get("textMuted", "dim"),
            )

        return text
