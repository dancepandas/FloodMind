"""FloodMind TUI — OpenCode-style 终端交互界面（simple_tui 为主入口）"""

from floodmind.tui.simple_tui import run_tui
from floodmind.tui.app import FloodMindTui  # 保留兼容

__all__ = ["run_tui", "FloodMindTui"]
