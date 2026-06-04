"""FloodMind TUI — Tips widget."""

import random
from rich.text import Text
from textual.widgets import Static

from floodmind.tui.theme import C

TIPS = [
    "输入 / 查看所有可用命令",
    "按 ctrl+j 或 alt+enter 发送消息",
    "使用 ! 前缀运行 shell 命令",
    "输入 /models 切换 AI 模型",
    "输入 /sessions 查看历史会话",
    "按 ctrl+p 打开命令面板",
    "输入 /help 查看完整帮助",
    "按 esc 返回主页或关闭对话框",
]


class TipsWidget(Static):
    can_focus = False
    BORDER_TITLE = None

    DEFAULT_CSS = """
    TipsWidget {
        width: 100%;
        height: 1;
        content-align: center middle;
        text-align: center;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tip = random.choice(TIPS)

    def render(self):
        text = Text()
        text.append("● Tip ", style=C["warning"])
        text.append(self._tip, style=C["text_muted"])
        return text
