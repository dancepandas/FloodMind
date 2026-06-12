"""
FloodMind TUI — /mcp dialog.
"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Header, Button, OptionList
from textual.containers import Horizontal
from textual.binding import Binding

from floodmind.config.settings import load_mcp_config, save_mcp_config


class McpDialog(ModalScreen[bool]):
    """MCP management dialog."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("[bold]MCP Servers[/bold]", id="mcp-title")
        yield OptionList(id="mcp-list")
        with Horizontal(id="mcp-btns"):
            yield Button("Toggle Selected", variant="primary")
            yield Button("Close [esc]", variant="default")

    def on_mount(self) -> None:
        self._refresh_list()

    def _get_servers(self) -> list:
        return load_mcp_config().get("servers", [])

    def _save_servers(self, servers: list) -> None:
        save_mcp_config({"servers": servers})

    def _refresh_list(self) -> None:
        ol = self.query_one("#mcp-list", OptionList)
        ol.clear_options()
        servers = self._get_servers()
        if not servers:
            ol.add_option(("No MCP servers configured. Add in ~/.floodmind/mcp.json", ""))
            return
        for i, s in enumerate(servers):
            on = "✓" if s.get("enabled", True) else "✗"
            name = s.get("name", "?")
            transport = s.get("transport", "sse")
            url = s.get("url", s.get("command", ""))[:50]
            label = f"[{on}] {name}  ({transport})  —  {url}"
            ol.add_option((label, str(i)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if str(event.button.label).startswith("Close"):
            self.dismiss(True)
        elif str(event.button.label).startswith("Toggle"):
            servers = self._get_servers()
            ol = self.query_one("#mcp-list", OptionList)
            if ol.highlighted is not None:
                opt = ol.get_option_at_index(ol.highlighted)
                if opt and opt.id:
                    idx = int(opt.id)
                    if 0 <= idx < len(servers):
                        servers[idx]["enabled"] = not servers[idx].get("enabled", True)
                        self._save_servers(servers)
                        self._refresh_list()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        servers = self._get_servers()
        if event.option_id and event.option_id.isdigit():
            idx = int(event.option_id)
            if 0 <= idx < len(servers):
                servers[idx]["enabled"] = not servers[idx].get("enabled", True)
                self._save_servers(servers)
                self._refresh_list()

    def action_close(self) -> None:
        self.dismiss(True)

    CSS = """
    McpDialog { align: center middle; }
    #mcp-title {
        width: 62;
        padding: 1 2 0 2;
        background: #1a1a2e;
        border: solid #7c6fae;
        border-bottom: none;
    }
    #mcp-list {
        width: 62;
        max-height: 16;
        border: solid #7c6fae;
        border-top: none;
        border-bottom: none;
    }
    #mcp-btns {
        width: 62;
        align-horizontal: right;
        padding: 0 2 1 2;
        background: #1a1a2e;
        border: solid #7c6fae;
        border-top: none;
    }
    """
