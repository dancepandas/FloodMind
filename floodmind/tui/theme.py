"""FloodMind TUI — theme constants (OpenCode dark palette)."""

C = {
    "bg": "#0a0a0a",
    "surface": "#141414",
    "surface_light": "#1e1e1e",
    "surface_element": "#282828",
    "primary": "#fab283",
    "secondary": "#5c9cf5",
    "accent": "#9d7cd8",
    "text": "#eeeeee",
    "text_muted": "#808080",
    "error": "#e06c75",
    "warning": "#f5a742",
    "success": "#7fd88f",
    "info": "#56b6c2",
    "border": "#484848",
    "border_active": "#606060",
    "border_subtle": "#3c3c3c",
    "user": "#fab283",
    "assistant": "#9d7cd8",
    "tool_run": "#f5a742",
    "tool_done": "#7fd88f",
    "thought": "#f5a742",
}

ICONS = {
    "bash": "$", "shell": "$", "read": "→", "write": "←", "edit": "←",
    "glob": "✱", "grep": "✱", "webfetch": "%", "websearch": "◈",
    "task": "│", "question": "?", "skill": "→", "getskill": "→",
    "todowrite": "⚙", "apply_patch": "%",
    "knowledgesearch": "⌕", "knowledgeadd": "+",
    "memoryadd": "⊞", "memorysearch": "⌕",
    "subagent": "│", "paralleltask": "‖", "loadmcpserver": "⬡",
}

def tool_icon(name: str) -> str:
    return ICONS.get(name.lower().replace("_", ""), "⚙")
