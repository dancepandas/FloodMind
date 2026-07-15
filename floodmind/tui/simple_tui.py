"""FloodMind TUI — 精简终端界面。

架构：直连 Agent，内联渲染所有事件（参考 OpenCode 样式）。
"""

import logging
import uuid

from rich.text import Text

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Header, Static, TextArea
from textual.widget import Widget

from floodmind.config.settings import settings
from floodmind.agent.native.model_client import ModelClient
from floodmind.memory import DualMemory
from floodmind.agent import create_flood_agent

logger = logging.getLogger(__name__)


class AgentBlock(Vertical):
    """可折叠的子代理执行块。默认收起，点击标题展开/折叠。"""

    DEFAULT_CSS = """
    AgentBlock {
        height: auto;
        margin: 0 0 0 5;
        border-left: solid #7c6fae;
    }
    AgentBlock > .agent-header {
        height: 1;
        padding: 0 1;
        color: #7c6fae;
    }
    AgentBlock > .agent-header:hover {
        color: #a08fd4;
    }
    AgentBlock > .agent-body {
        display: none;
        height: auto;
        padding: 0 1;
    }
    AgentBlock.-expanded > .agent-body {
        display: block;
    }
    """

    def __init__(self, title: str, **kwargs):
        super().__init__(**kwargs)
        self._title = title

    def compose(self) -> ComposeResult:
        yield Static(f"▸ Sub: {self._title}", classes="agent-header")
        yield VerticalScroll(classes="agent-body")

    def on_click(self) -> None:
        expanded = self.has_class("-expanded")
        self.set_class(not expanded, "-expanded")
        header = self.query_one(".agent-header", Static)
        icon = "▾" if not expanded else "▸"
        header.update(f"{icon} Sub: {self._title}")

    def set_done(self, status: str) -> None:
        icon = "[done]" if status == "completed" else "[err]"
        self._title = f"{icon} {self._title}"
        header = self.query_one(".agent-header", Static)
        cur = header.renderable.plain if hasattr(header, 'renderable') else ""
        header.update(f"▸ Sub: {self._title}")

    @property
    def body(self) -> VerticalScroll:
        return self.query_one(".agent-body", VerticalScroll)


class SimpleTUI(App):
    CSS = """
    Screen { layout: vertical; }

    #chat-log {
        height: 1fr;
        padding: 0 1;
        background: #0a0a0f;
    }

    #input-box {
        height: 5;
        border: solid #2d2d3d;
    }
    #input-box:focus { border: solid #5f87ff; }

    .user-msg { padding: 1 0 0 0; }
    .step-divider { padding: 1 0 0 0; }
    .tool-item { padding: 0 0 0 5; }
    .thinking-item { padding: 0 0 0 3; }
    .answer-text { padding: 0 0 0 3; }
    .system-msg { padding: 0 0 0 0; }
    .file-item { padding: 0 0 0 5; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "退出"),
    ]

    def __init__(self, model: str = ""):
        super().__init__()
        self._agent = None
        self._session_id = ""
        self._model_name = model or settings.model.model_name
        self._model_hint = model
        # Stream state per round
        self._active_thinking = ""
        self._active_tools = []
        self._active_answer = ""
        self._answer_widget = None
        self._thought_widget = None
        self._agent_block = None

    # ── Compose & Init ──

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat-log")
        yield TextArea(id="input-box", language="markdown", show_line_numbers=False)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "FloodMind"
        self.sub_title = f"{self._model_name}"
        self._init_agent()
        self._welcome()
        self.query_one("#input-box", TextArea).focus()

    def _init_agent(self) -> None:
        sid = f"tui-{uuid.uuid4().hex[:12]}"
        self._session_id = sid
        llm = ModelClient.from_settings(
            temperature=settings.model.temperature,
            max_tokens=settings.model.max_tokens,
            enable_thinking=bool(settings.model.enable_reasoning),
        )
        memory = DualMemory(
            session_id=sid,
            context_window=settings.model.context_window,
            llm=llm,
        )
        self._agent = create_flood_agent(llm_service=llm, memory=memory, session_id=sid)

    def _welcome(self) -> None:
        log = self._chat_log()
        t = Text()
        t.append("FloodMind  ", style="bold #5f87ff")
        t.append(f"v1.0.0  |  {self._model_name}", style="#808090")
        t.append("\nEnter 发送  /help 帮助  Ctrl+C 退出", style="#505060")
        log.mount(Static(t, classes="system-msg"))
        log.scroll_end(animate=False)

    # ── Helpers ──

    def _chat_log(self) -> VerticalScroll:
        return self.query_one("#chat-log", VerticalScroll)

    def _scroll(self) -> None:
        try:
            self._chat_log().scroll_end(animate=False)
        except Exception:
            pass

    def _mount_target(self):
        """返回当前 mount 目标（子代理内部或主 chat-log）"""
        if self._agent_block is not None:
            return self._agent_block.body
        return self._chat_log()

    def _mount_rich(self, renderable, *classes: str) -> Static:
        w = Static(renderable, classes=" ".join(classes))
        self._mount_target().mount(w)
        self._scroll()
        return w

    def action_quit(self) -> None:
        self.exit()

    # ── User / System Messages ──

    def _add_user_message(self, text: str) -> None:
        t = Text()
        t.append("▎ You  ", style="bold #5f87ff")
        t.append(text[:200], style="#e0e0e0")
        self._mount_rich(t, "user-msg")

    def _add_system(self, text: str) -> None:
        t = Text()
        t.append("  ", style="#808090")
        t.append(text, style="#808090")
        self._mount_rich(t, "system-msg")

    # ── Round lifecycle ──

    def _start_round(self) -> None:
        self._active_thinking = ""
        self._active_tools = []
        self._active_answer = ""
        self._answer_widget = None
        self._thought_widget = None
        self._agent_block = None

    def _ensure_answer(self) -> Static:
        if self._answer_widget is None:
            t = Text()
            t.append("▎ FloodMind", style="bold #4caf7d")
            self._answer_widget = Static(t, classes="answer-text")
            self._mount_target().mount(self._answer_widget)
            self._scroll()
        return self._answer_widget

    def _append_answer(self, text: str) -> None:
        self._active_answer += text
        w = self._ensure_answer()
        t = Text()
        t.append("▎ FloodMind", style="bold #4caf7d")
        t.append("  ")
        t.append(self._active_answer, style="#e0e0e0")
        w.update(t)
        self._scroll()

    # ── Tool / Thinking / Step ──

    def _add_thought(self, text: str) -> None:
        self._active_thinking += text
        t = Text()
        t.append("  [thinking] ", style="#5fb4ff")
        t.append(self._active_thinking, style="#5fb4ff")
        if self._thought_widget is None:
            self._thought_widget = Static(t, classes="thinking-item")
            self._mount_target().mount(self._thought_widget)
        else:
            self._thought_widget.update(t)
        self._scroll()

    def _add_tool(self, name: str, status: str = "running") -> None:
        icon = "*" if status == "running" else "v" if status == "completed" else "x"
        color = "#e5a443" if status == "running" else "#4caf7d" if status == "completed" else "#e54d4d"
        t = Text()
        t.append(f"  [{icon}] {name}", style=color)
        self._mount_rich(t, "tool-item")

    def _add_step(self, iteration: int, model: str = "") -> None:
        t = Text()
        t.append(f"  ── Step {iteration + 1}", style="bold #3a5a8c")
        if model:
            t.append(f" ({model})", style="#505060")
        t.append(" " + "─" * 40, style="#2d2d3d")
        self._mount_rich(t, "step-divider")

    # ── Event Dispatch ──

    def _dispatch_event(self, event: dict) -> None:
        t = event.get("type", "")
        content = event.get("content", "")

        if t == "answer_delta":
            if content:
                self._append_answer(content)

        elif t == "thought_delta":
            if content:
                self._add_thought(content)

        elif t in ("action_start", "tool_status"):
            name = event.get("tool_name", "")
            if name:
                self._add_tool(name, "running")

        elif t in ("action_end", "tool_result"):
            name = event.get("tool_name", "")
            if name:
                self._add_tool(name, "completed")

        elif t == "llm_step_start":
            # 每个 step 独立的 thinking 和 answer 块
            self._active_thinking = ""
            self._thought_widget = None
            self._active_answer = ""
            self._answer_widget = None
            self._add_step(
                event.get("iteration", 0),
                event.get("model", ""),
            )

        elif t == "llm_step_end":
            reason = event.get("finish_reason", "")
            tokens = event.get("tokens", {})
            p = tokens.get("prompt_tokens", 0)
            c = tokens.get("completion_tokens", 0)
            self.sub_title = f"{self._model_name}  │  Step: {p}+{c} tokens"

        elif t == "token_usage":
            total = event.get("total_tokens", 0)
            self.sub_title = f"{self._model_name}  │  {total} tokens"

        elif t == "retry_attempt":
            self._add_system(f"Retry #{event.get('attempt', 0)}")

        elif t == "workflow_plan":
            steps = event.get("steps", [])
            if steps:
                names = [s.get("label", s.get("title", "?")) for s in steps[:8]]
                self._add_system(f"Plan: {' → '.join(names)}")

        elif t == "workflow_step":
            label = event.get("label", event.get("title", ""))
            status = event.get("status", "")
            if not label:
                return
            if status == "running":
                # 创建可折叠的子代理块
                self._agent_block = AgentBlock(label)
                self._chat_log().mount(self._agent_block)
                self._scroll()
            elif status in ("completed", "error", "done"):
                if self._agent_block is not None:
                    self._agent_block.set_done(status)
                    self._agent_block = None

        elif t == "file_generated":
            fname = event.get("filename", event.get("file_name", ""))
            t2 = Text()
            t2.append(f"  [file] {fname}", style="#4caf7d")
            self._mount_rich(t2, "file-item")

        elif t == "image_generated":
            fname = event.get("filename", event.get("file_name", ""))
            t2 = Text()
            t2.append(f"  [image] {fname}", style="#4caf7d")
            self._mount_rich(t2, "file-item")

        elif t == "context_compress_start":
            self._add_system("记忆压缩中...")

        elif t == "context_compress_done":
            self._add_system("记忆压缩完成")

        elif t == "permission_ask":
            tool = event.get("tool_name", "?")
            reason = event.get("reason", "")
            self._add_system(f"确认: {tool} — {reason}")

        elif t in ("error", "llm_token_error"):
            if content:
                self._append_answer(f"\n  Error: {content}")

        elif t in ("stream_end", "heartbeat", "final",
                    "memory_status", "artifact_warning"):
            pass

    # ── Input & Send ──

    @on(TextArea.Changed, "#input-box")
    def on_input_changed(self, event: TextArea.Changed) -> None:
        text = event.text_area.text
        if text.strip() and "\n" in text:
            lines = text.strip().split("\n")
            message = "\n".join(lines[:-1]) if len(lines) > 1 else lines[0]
            if message.strip():
                event.text_area.text = ""
                cmd = self._parse_command(message)
                if cmd is not None:
                    self._handle_command(cmd)
                else:
                    self._send_message(message)

    def _parse_command(self, text: str):
        if not text.startswith("/"):
            return None
        return text[1:].strip().lower()

    def _handle_command(self, cmd: str) -> None:
        if cmd in ("exit", "quit", "q"):
            self.action_quit()
        elif cmd == "clear":
            if self._agent and hasattr(self._agent, 'memory'):
                self._agent.memory.clear()
            self._add_system("记忆已清空")
        elif cmd == "models":
            from floodmind.tui.dialogs.models import ModelsDialog
            self.app.push_screen(ModelsDialog(), callback=self._on_model_selected)
        elif cmd.startswith("model "):
            model_key = cmd[6:].strip()
            if model_key:
                self._model_name = model_key
                self._model_hint = model_key
                self._init_agent()
                self.sub_title = f"{self._model_name}"
                self._add_system(f"模型: {model_key}")
        elif cmd in ("help", "h"):
            self._add_system(
                "  /clear   清空记忆  /models  选模型\n"
                "  /model X 切换模型  /exit    退出"
            )
        else:
            self._add_system(f"未知: /{cmd}")

    def _on_model_selected(self, model_key: str) -> None:
        if not model_key:
            return
        self._model_name = model_key
        self._model_hint = model_key
        self._init_agent()
        self.sub_title = f"{self._model_name}"
        self._add_system(f"模型: {model_key}")

    # ── Stream ──

    @work(thread=True)
    def _send_message(self, message: str):
        if not self._agent:
            self.app.call_from_thread(self._add_system, "Agent 未初始化")
            return
        box = self.query_one("#input-box", TextArea)
        self.app.call_from_thread(self._disable_input)
        self.app.call_from_thread(self._add_user_message, message)
        self.app.call_from_thread(self._start_round)
        try:
            for chunk in self._agent.stream(message):
                try:
                    self.app.call_from_thread(self._dispatch_event, chunk)
                except Exception:
                    break
        except Exception as e:
            self.app.call_from_thread(self._append_answer, f"\n  Error: {e}")
        finally:
            self.app.call_from_thread(self._enable_input)
            self.app.call_from_thread(self._scroll)

    def _disable_input(self) -> None:
        self.query_one("#input-box", TextArea).read_only = True

    def _enable_input(self) -> None:
        self.query_one("#input-box", TextArea).read_only = False


# ── Entry ──

def run_tui(host: str = "", port: int = 0, model: str = "", reasoning: bool = False):
    if reasoning:
        settings.model.enable_reasoning = True
    SimpleTUI(model=model).run()


if __name__ == "__main__":
    run_tui()
