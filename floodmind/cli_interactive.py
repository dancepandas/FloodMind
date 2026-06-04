"""FloodMind CLI — 交互菜单

在用户无子命令、无 --tui/--web 参数时弹出简单菜单，让用户选择 TUI / Web / Chat / Quit。
"""

import sys
from typing import Optional


_MENU_LINES = [
    "",
    "  \033[36m╔═══════════════════════════════════════════════════╗\033[0m",
    "  \033[36m║\033[0m     \033[1;36mFloodMind\033[0m  v1.0.0  洪水预报 Agent 系统       \033[36m║\033[0m",
    "  \033[36m╠═══════════════════════════════════════════════════╣\033[0m",
    "  \033[36m║\033[0m                                                 \033[36m║\033[0m",
    "  \033[36m║\033[0m   \033[1;33m[T]\033[0m  TUI   终端交互界面 (推荐)                \033[36m║\033[0m",
    "  \033[36m║\033[0m   \033[1;33m[W]\033[0m  Web   浏览器访问                          \033[36m║\033[0m",
    "  \033[36m║\033[0m   \033[1;33m[C]\033[0m  Chat  纯文本命令行对话                    \033[36m║\033[0m",
    "  \033[36m║\033[0m   \033[1;33m[Q]\033[0m  Quit  退出                               \033[36m║\033[0m",
    "  \033[36m║\033[0m                                                 \033[36m║\033[0m",
    "  \033[36m╚═══════════════════════════════════════════════════╝\033[0m",
    "",
]


def _enable_ansi_colors() -> None:
    """Windows 终端默认不解析 ANSI 转义序列，需先开启 VT 处理。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(
            kernel32.GetStdHandle(-11),
            7,  # 启用 VT 处理
        )
    except Exception:
        # 失败则去掉所有 ANSI 序列
        for i, line in enumerate(_MENU_LINES):
            _MENU_LINES[i] = (
                line.replace("\033[0m", "")
                    .replace("\033[36m", "")
                    .replace("\033[1;36m", "")
                    .replace("\033[1;33m", "")
            )


def show_menu() -> str:
    """打印菜单并返回用户选择 (t/w/c/q)，默认 't'。"""
    _enable_ansi_colors()
    for line in _MENU_LINES:
        print(line, flush=True)

    while True:
        try:
            raw = input("  \033[1;36m请选择 [T/W/C/Q 默认=T]: \033[0m").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "q"

        if raw in ("", "t"):
            return "t"
        if raw == "w":
            return "w"
        if raw == "c":
            return "c"
        if raw == "q":
            return "q"
        print("  请输入 T、W、C 或 Q", flush=True)


def run_menu(model: Optional[str] = None, port: int = 13014, host: str = "0.0.0.0") -> int:
    """执行交互菜单并根据选择调用对应的子命令实现。返回退出码。"""
    # 避免延迟导入带来的循环：这里导入 cli 内函数
    from floodmind.cli import _run_tui, _run_web, _run_chat_legacy

    choice = show_menu()

    if choice == "q":
        print("\n  再见！")
        return 0
    if choice == "t":
        return _run_tui(model=model or "", port=port)
    if choice == "w":
        return _run_web(host=host, port=port, open_browser=True)
    if choice == "c":
        _run_chat_legacy(model=model)
        return 0

    return 0
