"""FloodMind TUI — Theme compatibility layer (bridges to ThemeManager).

Provides backward-compatible APIs: C dict, get_color(), status_icon(), tool_icon().
All color tokens are mapped to ThemeManager semantic tokens.

Usage:
    from floodmind.tui.theme import get_color, C, status_icon, tool_icon
    color = get_color("primary")
    color = C["text_muted"]   # lazy compatibility
"""

from __future__ import annotations

from textual.app import active_app

from floodmind.tui.theme_manager import ThemeManager


def _theme_mgr() -> ThemeManager:
    """获取当前应用的主题管理器，回退到独立实例。"""
    try:
        app = active_app.get()
        if hasattr(app, "theme_mgr"):
            return app.theme_mgr
    except Exception:
        pass
    return ThemeManager()


# ── Token 映射表（旧 → 新语义化）────────────────────────────────
_TOKEN_MAP: dict[str, str] = {
    # 基础颜色
    "bg": "background",
    "surface": "surface",
    "surface_light": "backgroundPanel",
    "surface_element": "borderFocus",
    # 语义色
    "primary": "primary",
    "secondary": "info",
    "accent": "accent",
    "text": "text",
    "text_muted": "textMuted",
    "text-muted": "textMuted",
    "text_dim": "textDim",
    "text-dim": "textDim",
    "error": "error",
    "warning": "warning",
    "success": "success",
    "info": "info",
    # 边框
    "border": "border",
    "border_active": "borderFocus",
    "border-active": "borderFocus",
    "border_subtle": "textDim",
    "border-subtle": "textDim",
    # 角色色（旧版）
    "user": "primary",
    "role_user": "primary",
    "role-user": "primary",
    "assistant": "accent",
    "role_assistant": "accent",
    "role-assistant": "accent",
    "role_thinking": "warning",
    "role-thinking": "warning",
    "role_tool": "warning",
    "role-tool": "warning",
    # 工具状态
    "tool_run": "warning",
    "tool_done": "success",
    "thought": "warning",
}


def get_color(token: str, fallback: str = "") -> str:
    """获取语义颜色值（兼容旧版 token 名称）。"""
    mapped = _TOKEN_MAP.get(token, token)
    return _theme_mgr().get(mapped, fallback)


def status_icon(status: str) -> str:
    """状态图标映射。"""
    icons = {
        "pending": "○",
        "running": "●",
        "completed": "✓",
        "done": "✓",
        "error": "✗",
        "failed": "✗",
    }
    return icons.get(status.lower(), "?")


def tool_icon(name: str) -> str:
    """工具图标映射。"""
    key = name.lower().replace("_", "").replace("-", "")
    ICONS: dict[str, str] = {
        "bash": "$",
        "shell": "$",
        "read": "→",
        "write": "←",
        "edit": "←",
        "glob": "✱",
        "grep": "✱",
        "webfetch": "%",
        "websearch": "◈",
        "task": "│",
        "question": "?",
        "skill": "→",
        "getskill": "→",
        "todowrite": "⚙",
        "applypatch": "%",
        "memoryadd": "⊞",
        "memorysearch": "⌕",
        "subagent": "│",
        "paralleltask": "‖",
        "loadmcpserver": "⬡",
        "knowledgesearch": "⌕",
        "knowledgeadd": "+",
        "knowledgesearchtool": "⌕",
        "knowledgeaddtool": "+",
    }
    return ICONS.get(key, "⚙")


# ── 兼容 C 字典 ────────────────────────────────────────────────
class _ColorDict(dict):
    """懒加载颜色字典 — 访问任意 key 时自动映射到 ThemeManager。"""

    def __missing__(self, key: str) -> str:
        val = get_color(key, "")
        self[key] = val
        return val


C: dict[str, str] = _ColorDict()
