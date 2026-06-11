"""FloodMind TUI — main App (OpenCode-style architecture).

重构要点：
- 集成 Router 路由系统
- 集成 SlotRegistry 插槽系统
- 集成 ThemeManager 主题管理
- 全局命令面板（/ 触发）
"""

from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.reactive import reactive

from floodmind.config.settings import settings
from floodmind.tui.router import Router
from floodmind.tui.slots import SlotRegistry
from floodmind.tui.theme_manager import ThemeManager
from floodmind.tui.screens.home import HomeScreen


class FloodMindTui(App):
    """FloodMind TUI 主应用 — 参考 OpenCode 的插件化架构。"""

    CSS_PATH = Path(__file__).parent / "tui.css"

    BINDINGS = [
        Binding("ctrl+p", "command_palette", "Commands", show=False),
        Binding("f1", "help", "Help", show=False),
        Binding("ctrl+shift+t", "toggle_theme", "切换主题", show=False),
    ]

    # reactive 状态
    theme_name = reactive("default")
    current_route = reactive("")

    def __init__(self, model: str = "", reasoning: bool = False, host: str = "localhost", port: int = 13014):
        super().__init__()
        self._cli_model = model
        self._cli_reasoning = reasoning
        self._host = host
        self._port = port

        # 核心子系统（OpenCode-style）
        self.router = Router(self)
        self.slots = SlotRegistry()
        self.theme_mgr = ThemeManager()

    def on_mount(self) -> None:
        self.title = "FloodMind"
        self.sub_title = settings.model.model_name
        if self._cli_model:
            settings.model.model_name = self._cli_model

        # 注册路由
        self._register_routes()

        # 推入首页
        self.push_screen(HomeScreen())

    def _register_routes(self) -> None:
        """注册所有路由。"""
        from floodmind.tui.screens.main import MainScreen

        self.router.register("home", HomeScreen)

        # MainScreen 需要 host/port 连接 web server
        def _main_screen_factory(session_id: str = ""):
            return MainScreen(
                host=self._host,
                port=self._port,
                model_hint=self._cli_model,
                session_id=session_id,
            )

        self.router.register("chat/:session_id", _main_screen_factory)

    def action_help(self) -> None:
        from floodmind.tui.dialogs.help import HelpDialog
        self.push_screen(HelpDialog())

    def action_command_palette(self) -> None:
        """打开命令面板 — OpenCode 风格。"""
        from floodmind.tui.dialogs.command_palette import CommandPaletteDialog
        self.push_screen(CommandPaletteDialog())

    def action_toggle_theme(self) -> None:
        """切换主题。"""
        themes = self.theme_mgr.list_themes()
        if not themes:
            return
        idx = themes.index(self.theme_name) if self.theme_name in themes else 0
        next_theme = themes[(idx + 1) % len(themes)]
        if self.theme_mgr.load(next_theme):
            self.theme_name = next_theme
            self._refresh_all_widgets()
            self.notify(f"主题已切换: {next_theme}")

    def _refresh_all_widgets(self) -> None:
        """刷新所有 widget，使主题变更生效。"""
        for screen in self.screens:
            for widget in screen.walk_children():
                try:
                    widget.refresh()
                except Exception:
                    pass

    def navigate(self, route: str, **params) -> None:
        """全局导航入口。"""
        self.router.navigate(route, **params)
        self.current_route = route

    def get_color(self, token: str, fallback: str = "") -> str:
        """获取当前主题颜色。"""
        return self.theme_mgr.get(token, fallback)


def run_tui(model: str = "", reasoning: bool = False, host: str = "localhost", port: int = 13014) -> None:
    if model:
        settings.model.model_name = model
    if reasoning:
        settings.model.enable_reasoning = reasoning
    FloodMindTui(model=model, reasoning=reasoning, host=host, port=port).run()
