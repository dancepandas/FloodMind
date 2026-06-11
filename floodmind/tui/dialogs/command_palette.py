"""FloodMind TUI — Command Palette dialog (OpenCode-style).

Features:
- Categorized commands (Session, Model, Agent, System, Navigation)
- Fuzzy search across name, title, slash, shortcut, description
- Recent commands prioritized
- Semantic color coding via ThemeManager
- Slash-name support (/new, /models, etc.)
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option
from rich.text import Text

from floodmind.tui.theme_manager import ThemeManager


# ── Command Registry ────────────────────────────────────────────
class Command:
    """命令定义。"""

    def __init__(
        self,
        name: str,
        title: str,
        category: str,
        slash: str = "",
        shortcut: str = "",
        description: str = "",
    ):
        self.name = name
        self.title = title
        self.category = category
        self.slash = slash
        self.shortcut = shortcut
        self.description = description


COMMANDS: list[Command] = [
    # Session
    Command("session.new", "新建会话", "Session", "/new", "", "创建一个新的聊天会话"),
    Command("session.list", "切换会话", "Session", "/sessions", "", "查看并切换历史会话"),
    Command("session.clear", "清空当前会话", "Session", "/clear", "", "清空当前会话的消息记录"),
    # Model
    Command("model.list", "切换模型", "Model", "/models", "", "切换当前使用的 AI 模型"),
    # Agent
    Command("mcp.list", "管理 MCP 服务器", "Agent", "/mcp", "", "查看和管理 MCP 工具服务器"),
    # Navigation
    Command("nav.home", "返回主页", "Navigation", "/home", "Esc", "返回到欢迎主页"),
    Command("nav.chat", "进入聊天", "Navigation", "/chat", "2", "聚焦到聊天面板"),
    # System
    Command("help.show", "帮助", "System", "/help", "?", "显示快捷键和命令帮助"),
    Command("palette.open", "命令面板", "System", "/", "Ctrl+P", "打开此命令面板"),
    Command("theme.toggle", "切换主题", "System", "/theme", "Ctrl+Shift+T", "切换明暗主题"),
    Command("app.exit", "退出", "System", "/exit", "q", "退出 FloodMind"),
]

# 分类颜色映射（与 ThemeManager token 对应）
CATEGORY_COLORS: dict[str, str] = {
    "Session": "primary",
    "Model": "info",
    "Agent": "accent",
    "Navigation": "success",
    "System": "textMuted",
}


class CommandPaletteDialog(ModalScreen):
    """命令面板 — 模态对话框。"""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    CSS = """
    CommandPaletteDialog {
        align: center middle;
    }
    #palette-box {
        width: 70;
        min-width: 50;
        max-width: 90%;
        background: #1a1a2e;
        border: solid #5f87ff;
        padding: 1 2;
    }
    #palette-search {
        margin: 0 0 1 0;
        border: solid #2d2d3d;
        background: #0a0a0f;
        color: #e0e0e0;
    }
    #palette-search:focus {
        border: solid #5f87ff;
    }
    #palette-list {
        max-height: 20;
        border: none;
        background: transparent;
    }
    OptionList > .option-list--option {
        color: #e0e0e0;
        padding: 0 1;
    }
    OptionList:focus > .option-list--option-highlight {
        background: #3a5a8c;
        color: #5f87ff;
    }
    #palette-hint {
        color: #808090;
        margin-top: 1;
    }
    #palette-empty {
        color: #808090;
        text-align: center;
        padding: 1 0;
    }
    """

    # 最近使用的命令（最多 8 个）
    recent_commands: reactive[list[str]] = reactive([])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._theme = ThemeManager()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Input(placeholder="输入命令或 /slash...", id="palette-search"),
            OptionList(id="palette-list"),
            Static(
                "  ↑↓ 导航 · Enter 执行 · Esc 关闭",
                id="palette-hint",
            ),
            id="palette-box",
        )

    def on_mount(self) -> None:
        self._filter_list("")
        self.query_one("#palette-search", Input).focus()

    # ── 搜索与过滤 ────────────────────────────────────────────────

    @on(Input.Changed, "#palette-search")
    def _on_input_changed(self, event: Input.Changed) -> None:
        self._filter_list(event.value)

    def _filter_list(self, query: str) -> None:
        """按查询过滤命令列表，支持模糊匹配。"""
        ol = self.query_one("#palette-list", OptionList)
        ol.clear_options()

        q = query.strip().lower()
        scored: list[tuple[int, Command]] = []

        for cmd in COMMANDS:
            score = self._match_score(cmd, q)
            if score > 0 or not q:
                scored.append((score, cmd))

        # 排序：分数降序 → 最近使用优先 → 分类顺序 → 名称
        scored.sort(key=lambda x: (
            -x[0],
            -(x[1].name in self.recent_commands),
            self._category_order(x[1].category),
            x[1].title,
        ))

        if not scored:
            ol.add_option(
                Option("未找到命令", id="__empty__", disabled=True)
            )
            return

        last_category = ""
        for _, cmd in scored:
            # 分类分隔线
            if cmd.category != last_category and not q:
                if last_category:
                    ol.add_option(
                        Option("", id=f"__sep__{cmd.name}", disabled=True)
                    )
                last_category = cmd.category

            label = self._format_option(cmd)
            ol.add_option(Option(label, id=cmd.name))

    def _match_score(self, cmd: Command, q: str) -> int:
        """返回匹配分数，0 表示不匹配。"""
        if not q:
            return 1
        # 精确匹配 slash 命令
        if cmd.slash.lower() == q:
            return 100
        # 前缀匹配 slash
        if cmd.slash.lower().startswith(q):
            return 80
        # 前缀匹配标题
        if cmd.title.lower().startswith(q):
            return 70
        # 前缀匹配名称
        if cmd.name.lower().startswith(q):
            return 60
        # 包含匹配
        if q in cmd.title.lower():
            return 50
        if q in cmd.name.lower():
            return 40
        if q in cmd.description.lower():
            return 30
        if q in cmd.category.lower():
            return 20
        return 0

    def _category_order(self, category: str) -> int:
        order = ["Session", "Model", "Agent", "Navigation", "System"]
        try:
            return order.index(category)
        except ValueError:
            return 99

    def _format_option(self, cmd: Command) -> Text:
        """格式化命令选项为 Rich Text。"""
        text = Text()
        # 分类标签
        cat_color = self._theme.get(CATEGORY_COLORS.get(cmd.category, "textMuted"), "dim")
        text.append(f"[{cmd.category}]  ", style=f"dim {cat_color}")
        # 标题
        text.append(cmd.title, style="bold")
        # slash 命令
        if cmd.slash:
            text.append(f"    {cmd.slash}", style=self._theme.get("textMuted", "dim"))
        # 快捷键提示
        if cmd.shortcut:
            text.append(f"  ({cmd.shortcut})", style=self._theme.get("textDim", "dim"))
        return text

    # ── 选择与执行 ────────────────────────────────────────────────

    @on(OptionList.OptionSelected, "#palette-list")
    def _on_option_selected(self, event: OptionList.OptionSelected) -> None:
        cmd_name = event.option.id
        if cmd_name and not cmd_name.startswith("__"):
            self.dismiss()
            self._record_recent(cmd_name)
            self._dispatch(cmd_name)

    @on(Input.Submitted, "#palette-search")
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        ol = self.query_one("#palette-list", OptionList)
        if ol.highlighted is not None:
            opt = ol.get_option_at_index(ol.highlighted)
            if opt.id and not opt.id.startswith("__"):
                self.dismiss()
                self._record_recent(opt.id)
                self._dispatch(opt.id)

    def _record_recent(self, cmd_name: str) -> None:
        """记录最近使用的命令。"""
        recent = list(self.recent_commands)
        if cmd_name in recent:
            recent.remove(cmd_name)
        recent.insert(0, cmd_name)
        self.recent_commands = recent[:8]

    def _dispatch(self, cmd_name: str) -> None:
        """分发命令到对应的动作。"""
        if not cmd_name:
            return
        app = self.app

        if cmd_name == "session.new":
            app.pop_screen()
        elif cmd_name == "session.list":
            from floodmind.tui.dialogs.sessions import SessionsDialog
            app.push_screen(SessionsDialog())
        elif cmd_name == "session.clear":
            from floodmind.tui.screens.main import MainScreen
            if isinstance(app.screen, MainScreen):
                chat = app.screen.query_one("#chat")
                if hasattr(chat, "clear_chat"):
                    chat.clear_chat()
        elif cmd_name == "model.list":
            from floodmind.tui.dialogs.models import ModelsDialog
            app.push_screen(ModelsDialog())
        elif cmd_name == "mcp.list":
            from floodmind.tui.dialogs.mcp import McpDialog
            app.push_screen(McpDialog())
        elif cmd_name == "nav.home":
            while len(app.screen_stack) > 1:
                app.pop_screen()
        elif cmd_name == "nav.chat":
            # 如果已经在 MainScreen，聚焦聊天面板
            from floodmind.tui.screens.main import MainScreen
            if isinstance(app.screen, MainScreen):
                app.screen.action_focus_chat()
        elif cmd_name == "help.show":
            from floodmind.tui.dialogs.help import HelpDialog
            app.push_screen(HelpDialog())
        elif cmd_name == "palette.open":
            app.push_screen(CommandPaletteDialog())
        elif cmd_name == "theme.toggle":
            app.action_toggle_theme()
        elif cmd_name == "app.exit":
            app.exit()

    def action_close(self) -> None:
        self.dismiss()

    def action_cursor_up(self) -> None:
        ol = self.query_one("#palette-list", OptionList)
        ol.action_cursor_up()

    def action_cursor_down(self) -> None:
        ol = self.query_one("#palette-list", OptionList)
        ol.action_cursor_down()
