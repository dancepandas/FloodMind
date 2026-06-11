"""FloodMind TUI — DetailPanel widget.

可折叠的过程面板，显示 Thinking / Tool / Agent 活动。
遵循 tui-design-skill: 颜色+符号双重编码，可折叠减少视觉噪音。
使用 ThemeManager 语义颜色系统。
"""

from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static, Collapsible
from rich.text import Text

from floodmind.tui.theme_manager import ThemeManager


class DetailPanel(VerticalScroll):
    """详情面板 — 显示 AI 思考过程、工具调用、子代理活动。"""

    can_focus = True

    # reactive 状态列表
    thinking_items = reactive(list)
    tool_items = reactive(list)
    agent_items = reactive(list)

    DEFAULT_CSS = """
    DetailPanel {
        width: 30;
        min-width: 25;
        max-width: 45;
        background: #0a0a0f;
        border-left: solid #2d2d3d;
        padding: 0 1;
    }
    DetailPanel .empty-hint {
        color: #808090;
        text-align: center;
        padding: 1 0;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._theme = ThemeManager()

    def watch_thinking_items(self) -> None:
        self._refresh()

    def watch_tool_items(self) -> None:
        self._refresh()

    def watch_agent_items(self) -> None:
        self._refresh()

    def _status_icon(self, status: str) -> str:
        icons = {
            "pending": "○",
            "running": "●",
            "completed": "✓",
            "done": "✓",
            "error": "✗",
            "failed": "✗",
        }
        return icons.get(status.lower(), "?")

    def _refresh(self) -> None:
        try:
            self.remove_children()
        except Exception:
            pass

        has_content = bool(
            self.thinking_items or self.tool_items or self.agent_items
        )
        if not has_content:
            self.mount(
                Static("(无活动)\n按 3 隐藏", classes="empty-hint")
            )
            return

        # Thinking 区块
        if self.thinking_items:
            content = Text()
            thinking_color = self._theme.get("warning", "#e5a443")
            for item in self.thinking_items:
                content.append(f"{item}\n", style=thinking_color)
            self.mount(
                Collapsible(
                    Static(content),
                    title=f"思考 ({len(self.thinking_items)})",
                    collapsed=False,
                )
            )

        # Tool 区块
        if self.tool_items:
            content = Text()
            for item in self.tool_items:
                icon = self._status_icon(item.get("status", "pending"))
                name = item.get("name", "?")
                if item.get("status") == "completed":
                    color = self._theme.get("success", "#4caf7d")
                else:
                    color = self._theme.get("warning", "#e5a443")
                content.append(f"{icon} {name}\n", style=color)
            self.mount(
                Collapsible(
                    Static(content),
                    title=f"工具 ({len(self.tool_items)})",
                    collapsed=False,
                )
            )

        # Agent 区块
        if self.agent_items:
            content = Text()
            for item in self.agent_items:
                icon = self._status_icon(item.get("status", "pending"))
                title = item.get("title", "?")
                if item.get("status") == "completed":
                    color = self._theme.get("success", "#4caf7d")
                else:
                    color = self._theme.get("accent", "#7c6fae")
                content.append(f"{icon} {title}\n", style=color)
            self.mount(
                Collapsible(
                    Static(content),
                    title=f"代理 ({len(self.agent_items)})",
                    collapsed=False,
                )
            )

    def on_mount(self) -> None:
        self._refresh()

    def clear_all(self) -> None:
        """清空所有活动记录。"""
        self.thinking_items = []
        self.tool_items = []
        self.agent_items = []

    def add_thinking(self, text: str) -> None:
        """追加思考内容。"""
        self.thinking_items = self.thinking_items + [text]

    def add_tool(self, name: str, status: str = "running", output: str = "") -> None:
        """添加/更新工具记录。"""
        items = list(self.tool_items)
        for i, item in enumerate(items):
            if item.get("name") == name:
                items[i] = {"name": name, "status": status, "output": output}
                self.tool_items = items
                return
        items.append({"name": name, "status": status, "output": output})
        self.tool_items = items

    def add_agent(
        self, title: str, status: str = "running", label: str = ""
    ) -> None:
        """添加/更新代理记录。"""
        items = list(self.agent_items)
        display = label or title or "(未命名)"
        for i, item in enumerate(items):
            if item.get("title") == title:
                items[i] = {"title": title, "status": status, "label": display}
                self.agent_items = items
                return
        items.append({"title": title, "status": status, "label": display})
        self.agent_items = items
