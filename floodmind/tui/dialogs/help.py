"""FloodMind TUI — /help dialog."""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Header, Button
from textual.containers import VerticalScroll, Horizontal
from textual.binding import Binding


HELP_TEXT = """
[bold]斜杠命令[/bold]

  /new, /clear      新建会话
  /models           切换模型
  /sessions         切换会话
  /help             显示此帮助
  /exit, /q         退出 FloodMind
  /mcp              管理 MCP 服务器

[bold]快捷键[/bold]

  ctrl+p            命令面板
  ctrl+j            发送消息
  alt+enter         发送消息
  esc               返回/关闭
  f1                帮助

[bold]输入提示[/bold]

  输入 /            触发斜杠命令
  输入 !            运行 shell 命令（未实现）
"""


class HelpDialog(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close")]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="help-scroll"):
            yield Static(HELP_TEXT, id="help-content")
        with Horizontal(id="help-btns"):
            yield Button("关闭 [esc]", variant="primary")

    def on_button_pressed(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    CSS = """
    HelpDialog { align: center middle; }
    #help-scroll {
        width: 58;
        max-height: 24;
        padding: 1 2;
        border: solid #9d7cd8;
        background: #141414;
    }
    #help-content {
        padding: 1 0;
        color: #eeeeee;
    }
    #help-btns {
        width: 58;
        align-horizontal: right;
        padding: 0 2 0 2;
        margin-top: 1;
    }
    """
