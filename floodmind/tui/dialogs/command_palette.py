"""FloodMind TUI — Command Palette dialog."""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static, Header
from textual.widgets.option_list import Option

from floodmind.tui.theme import C

COMMANDS = [
    {"name": "session.new", "title": "新建会话", "category": "Session", "slash": "/new"},
    {"name": "session.list", "title": "切换会话", "category": "Session", "slash": "/sessions"},
    {"name": "session.clear", "title": "清空当前会话", "category": "Session", "slash": "/clear"},
    {"name": "model.list", "title": "切换模型", "category": "Model", "slash": "/models"},
    {"name": "mcp.list", "title": "管理 MCP 服务器", "category": "Agent", "slash": "/mcp"},
    {"name": "help.show", "title": "帮助", "category": "System", "slash": "/help"},
    {"name": "app.exit", "title": "退出", "category": "System", "slash": "/exit"},
]


class CommandPaletteDialog(ModalScreen):
    BINDINGS = [Binding("escape", "close", "Close")]

    CSS = """
    CommandPaletteDialog {
        align: center middle;
    }
    #palette-box {
        width: 60;
        background: #141414;
        border: solid #9d7cd8;
        padding: 1 2;
    }
    #palette-search {
        margin: 0;
        border: solid #606060;
        background: #0a0a0a;
        color: #eeeeee;
    }
    #palette-search:focus {
        border: solid #9d7cd8;
    }
    #palette-list {
        max-height: 18;
        border: none;
        background: #141414;
    }
    OptionList > .option-list--option {
        color: #eeeeee;
    }
    OptionList:focus > .option-list--option-highlight {
        background: #1e1e1e;
        color: #9d7cd8;
    }
    #palette-hint {
        color: #808080;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="palette-box"):
            yield Input(placeholder="输入命令...", id="palette-search")
            yield OptionList(id="palette-list")
            yield Static(
                "  ↑↓ 导航 · Enter 执行 · Esc 关闭",
                id="palette-hint",
            )

    def on_mount(self) -> None:
        self._filter_list("")
        self.query_one("#palette-search", Input).focus()

    @on(Input.Changed, "#palette-search")
    def _on_input_changed(self, event: Input.Changed) -> None:
        self._filter_list(event.value)

    def _filter_list(self, query: str) -> None:
        ol = self.query_one("#palette-list", OptionList)
        ol.clear_options()
        q = query.strip().lower()
        for cmd in COMMANDS:
            if q and q not in cmd["title"].lower() and q not in cmd["name"].lower() and q not in cmd.get("slash", "").lower():
                continue
            label = f"  [{cmd['category']}]  {cmd['title']}"
            if cmd.get("slash"):
                label += f"    {cmd['slash']}"
            ol.add_option(Option(label, id=cmd["name"]))

    @on(OptionList.OptionSelected, "#palette-list")
    def _on_option_selected(self, event: OptionList.OptionSelected) -> None:
        cmd_name = event.option.id
        self.dismiss()
        self._dispatch(cmd_name)

    @on(Input.Submitted, "#palette-search")
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        ol = self.query_one("#palette-list", OptionList)
        if ol.highlighted is not None:
            highlighted_id = ol.get_option_at_index(ol.highlighted).id
            self.dismiss()
            self._dispatch(highlighted_id)

    def _dispatch(self, cmd_name: str) -> None:
        if not cmd_name:
            return
        app = self.app
        if cmd_name == "session.new":
            app.pop_screen()
        elif cmd_name == "session.list":
            from floodmind.tui.dialogs.sessions import SessionsDialog
            app.push_screen(SessionsDialog())
        elif cmd_name == "session.clear":
            app.pop_screen()
        elif cmd_name == "model.list":
            from floodmind.tui.dialogs.models import ModelsDialog
            app.push_screen(ModelsDialog())
        elif cmd_name == "mcp.list":
            from floodmind.tui.dialogs.mcp import McpDialog
            app.push_screen(McpDialog())
        elif cmd_name == "help.show":
            from floodmind.tui.dialogs.help import HelpDialog
            app.push_screen(HelpDialog())
        elif cmd_name == "app.exit":
            app.exit()

    def action_close(self) -> None:
        self.dismiss()
