"""FloodMind legacy TUI package.

HTTP-only helpers such as ``floodmind.tui.web_client`` must remain importable
without importing Textual-backed UI modules. UI entrypoints are exposed lazily.
"""

__all__ = ["run_tui", "FloodMindTui"]

_TUI_INSTALL_HINT = "Legacy TUI requires the optional extra: pip install 'floodmind[tui]'"


def __getattr__(name: str):
    if name == "run_tui":
        try:
            from floodmind.tui.simple_tui import run_tui
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(_TUI_INSTALL_HINT) from exc
        return run_tui

    if name == "FloodMindTui":
        try:
            from floodmind.tui.app import FloodMindTui
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(_TUI_INSTALL_HINT) from exc
        return FloodMindTui

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
