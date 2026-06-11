"""FloodMind TUI — Message widgets (OpenCode-style cards).

使用 ThemeManager 语义颜色系统。
"""

from rich.markdown import Markdown
from rich.text import Text
from rich.console import RenderableType
from textual.widget import Widget
from textual.reactive import reactive

from floodmind.tui.theme import tool_icon, get_color


class UserMessage(Widget):
    can_focus = False

    CSS = """
    UserMessage {
        margin: 1 0 0 0;
        border-left: thick #5f87ff;
        background: #1a1a2e;
        padding: 1 2;
    }
    """

    def __init__(self, content: str = "", **kwargs):
        super().__init__(**kwargs)
        self._content = content

    @property
    def content(self) -> str:
        return self._content

    def render(self) -> RenderableType:
        return self._content.strip()


class AssistantMessage(Widget):
    can_focus = False
    streaming = reactive(True)

    CSS = """
    AssistantMessage {
        margin: 1 0 0 0;
        border-left: thick #7c6fae;
        background: #0a0a0f;
        padding: 1 2;
    }
    AssistantMessage.-streaming {
        border-left: thick #e5a443;
    }
    """

    def __init__(self, content: str = "", model_name: str = "", **kwargs):
        super().__init__(**kwargs)
        self._content = content
        self._model_name = model_name
        self.add_class("assistant-message")
        if self.streaming:
            self.add_class("streaming")

    @property
    def content(self) -> str:
        return self._content

    def append_chunk(self, chunk: str) -> None:
        self._content += chunk
        self.refresh(layout=True)

    def finalize(self) -> None:
        self.streaming = False
        self.remove_class("streaming")
        self.refresh()

    def watch_streaming(self, val: bool) -> None:
        if not val:
            self.remove_class("streaming")
        else:
            self.add_class("streaming")

    def render(self) -> RenderableType:
        if not self._content.strip():
            return ""
        return Markdown(self._content.strip(), code_theme="monokai")


class AssistantMeta(Widget):
    can_focus = False

    CSS = """
    AssistantMeta {
        height: 1;
        color: #808090;
        padding-left: 4;
        margin: 0;
    }
    """

    def __init__(self, model_name: str = "", duration: str = "", **kwargs):
        super().__init__(**kwargs)
        self._model = model_name
        self._duration = duration

    def render(self):
        text = Text()
        text.append("▣ ", style=get_color("accent"))
        text.append(self._model, style=get_color("text"))
        if self._duration:
            text.append(f" · {self._duration}", style=get_color("textMuted"))
        return text


class ToolCard(Widget):
    can_focus = False
    completed = reactive(False)

    CSS = """
    ToolCard {
        margin: 0 0;
        border-left: thick #e5a443;
        background: #1a1a2e;
        padding: 0 2;
        height: auto;
    }
    ToolCard.-completed {
        border-left: thick #4caf7d;
    }
    #tool-output {
        color: #808090;
        padding-left: 2;
        max-height: 6;
    }
    """

    def __init__(self, tool_name: str = "", output: str = "", is_done: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._output = output
        if is_done:
            self.completed = True
            self.add_class("completed")

    @property
    def content(self) -> str:
        return self._output

    def watch_completed(self, val: bool) -> None:
        if val:
            self.add_class("completed")

    def render(self) -> RenderableType:
        header = Text()
        if self.completed:
            header.append(f"  ✓  ", style=get_color("success"))
        else:
            header.append(f"  ●  ", style=get_color("warning"))
        header.append(self._tool_name, style=f"bold {get_color('text')}")
        result = Text()
        result.append_text(header)
        if self._output and self._output.strip() and self._output.strip() != "Running...":
            result.append("\n")
            preview = self._output[:300]
            result.append(f"    {preview}", style=get_color("textMuted"))
        return result


class MessageWidget(Widget):
    """Legacy compat alias — wraps old usage in chat.py."""
    can_focus = False

    def __init__(self, content: str = "", role: str = "assistant", **kwargs):
        super().__init__(**kwargs)
        self._content = content
        self.role = role
        if role == "user":
            self.add_class("user-message")
        else:
            self.add_class("assistant-message")
            self.add_class("streaming")

    @property
    def content(self) -> str:
        return self._content

    def append_chunk(self, chunk: str) -> None:
        self._content += chunk
        self.refresh(layout=True)

    def finalize(self) -> None:
        self.remove_class("streaming")
        self.refresh()

    def render(self) -> RenderableType:
        if not self._content.strip():
            return ""
        return Markdown(self._content.strip(), code_theme="monokai")


class ToolWidget(Widget):
    """Legacy compat alias."""
    can_focus = False
    completed = reactive(False)

    def __init__(self, tool_name: str = "", output: str = "", **kwargs):
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._output = output

    @property
    def content(self) -> str:
        return self._output

    def render(self) -> RenderableType:
        t = Text()
        t.append(f"  {tool_icon(self._tool_name)}  {self._tool_name}", style=f"bold {get_color('warning')}")
        if self._output and self._output.strip() and self._output.strip() != "Running...":
            t.append("\n")
            t.append(f"    {self._output[:300]}", style=get_color("textMuted"))
        return t
