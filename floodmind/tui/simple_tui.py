"""FloodMind TUI — 通过 HTTP 客户端与 web server 交互

架构：TUI 作为前端，连接后台 web server 的 /api/* 端点，共享会话数据。
"""

import logging
import uuid

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static, TextArea

from floodmind.tui.server_manager import ensure_web_server, stop_web_server
from floodmind.tui.web_client import FloodMindClient


class SectionHeader(Static):
    """可折叠区域的标题行"""

    DEFAULT_CSS = """
    SectionHeader {
        height: 1;
        padding: 0 1;
        background: #1e1e1e;
    }
    """

    def __init__(self, title: str, color: str, section_key: str, **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._color = color
        self._section_key = section_key
        self._icon = "▸"
        self._body_key = f"{section_key}-body"

    def _render_text(self) -> Text:
        t = Text()
        t.append(f" {self._icon} ", style=f"bold {self._color}")
        t.append(self._title, style=f"bold {self._color}")
        return t

    def on_mount(self) -> None:
        self.update(self._render_text())

    def on_click(self) -> None:
        try:
            body = self.screen.query_one(f"#{self._body_key}", Static)
            is_visible = body.display
            body.display = not is_visible
            self._icon = "▾" if not is_visible else "▸"
            self.update(self._render_text())
        except Exception:
            pass

    def set_title(self, title: str) -> None:
        self._title = title
        self.update(self._render_text())


class SectionBody(Static):
    """可折叠区域的内容"""

    DEFAULT_CSS = """
    SectionBody {
        display: none;
        height: auto;
        padding: 0 3;
        color: #808080;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._content = ""

    def append_text(self, text: str) -> None:
        self._content += text
        self.update(Text(self._content, style="#808080"))

    def set_text(self, text: str) -> None:
        self._content = text
        self.update(Text(text, style="#808080"))


class SimpleTUI(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #chat-log {
        height: 1fr;
        border: solid #484848;
        padding: 0;
        background: #121212;
    }

    #input-box {
        height: 5;
        border: solid #484848;
    }

    #input-box:focus {
        border: solid #9d7cd8;
    }

    .user-msg {
        padding: 1 1;
        background: #1a1a1a;
    }

    .answer-msg {
        padding: 1 1;
        color: #81c784;
    }

    #round-anchor {
        height: 0;
    }

    .thinking-msg { padding: 1 1; color: #5c9cf5; }
    .tools-msg { padding: 1 1; color: #f5a742; }
    .agents-msg { padding: 1 1; color: #d783ec; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "退出"),
        Binding("ctrl+d", "quit", "退出"),
    ]

    def __init__(
        self,
        host: str = "localhost",
        port: int = 13014,
        client: FloodMindClient = None,
        model: str = "",
    ):
        super().__init__()
        self._host = host
        self._port = port
        self._client = client  # 可在测试中注入
        self._counter = 0

        self._thinking_text = ""
        self._thinking_key = None
        self._tools_list = []
        self._tools_key = None
        self._agents_list = []
        self._agents_key = None
        self._answer_text = ""
        self._answer_key = None
        self._anchor_key = None

        self._started_server_here = False
        self._model_hint = model

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat-log")
        yield TextArea(id="input-box")
        yield Footer()

    def on_mount(self) -> None:
        logging.getLogger("root").setLevel(logging.CRITICAL)
        self.title = "FloodMind"
        self.sub_title = "正在连接 web server..."
        self._connect_and_init()

    def _connect_and_init(self) -> None:
        """连接 web server，初始化会话"""
        chat_log = self.query_one("#chat-log", VerticalScroll)

        # 1. 如果没有注入的 client，自行连接/启动 web server
        if self._client is None:
            if not self._startup_web_server():
                self._add_system_message(
                    f"无法连接到 web server ({self._host}:{self._port})\n"
                    "请检查端口是否可用，或先手动执行: floodmind serve"
                )
                return

            self._client = FloodMindClient(base_url=f"http://{self._host}:{self._port}")

        # 2. 健康检查
        if not self._client.health_check():
            self._add_system_message("web server 健康检查失败")
            return

        # 3. 初始化会话
        if not self._client.init_session(
            enable_search=False,
            enable_rag=True,
            enable_reasoning=True,
            model_key=self._model_hint,
        ):
            self._add_system_message("会话初始化失败")
            return

        self.sub_title = f"会话: {self._client.session_id}  |  {self._client.model_name}"

        sys_t = Text()
        sys_t.append("系统: ", style="bold yellow")
        sys_t.append(
            f"欢迎使用 FloodMind v1.0.0  |  模型: {self._client.model_name}\n"
            f"会话: {self._client.session_id}  |  输入消息，按 Enter 发送  |  Ctrl+C 退出",
            style="yellow",
        )
        chat_log.mount(Static(sys_t))

        input_box = self.query_one("#input-box", TextArea)
        input_box.focus()

    def _startup_web_server(self) -> bool:
        """启动 web server（如未运行），返回是否成功"""
        ok, started_here = ensure_web_server(self._host, self._port)
        if ok:
            self._started_server_here = started_here
            return True
        return False

    def action_quit(self) -> None:
        if self._client:
            self._client.close()
        if self._started_server_here:
            stop_web_server()
        self.exit()

    def _next_key(self) -> str:
        self._counter += 1
        return f"r{self._counter}-{uuid.uuid4().hex[:4]}"

    def _add_system_message(self, text: str) -> None:
        chat_log = self.query_one("#chat-log", VerticalScroll)
        t = Text()
        t.append("系统: ", style="bold yellow")
        t.append(text, style="yellow")
        chat_log.mount(Static(t))
        chat_log.scroll_end(animate=False)

    def _add_user_message(self, text: str) -> None:
        chat_log = self.query_one("#chat-log", VerticalScroll)
        t = Text()
        t.append("你: ", style="bold cyan")
        t.append(text, style="cyan")
        chat_log.mount(Static(t, classes="user-msg"))
        chat_log.scroll_end(animate=False)

    def scroll_end(self) -> None:
        try:
            chat_log = self.query_one("#chat-log", VerticalScroll)
            chat_log.scroll_end(animate=False)
        except Exception:
            pass

    def _start_round(self) -> None:
        """开始新一轮对话：先挂载锚点和回答 widget，所有过程 widget 插在回答之前"""
        chat_log = self.query_one("#chat-log", VerticalScroll)

        self._thinking_text = ""
        self._thinking_key = None
        self._tools_list = []
        self._tools_key = None
        self._agents_list = []
        self._agents_key = None
        self._answer_text = ""

        round_key = self._next_key()
        self._answer_key = f"{round_key}-answer"
        self._anchor_key = f"{round_key}-anchor"

        anchor = Static("", id=self._anchor_key)
        chat_log.mount(anchor)

        t = Text()
        t.append("AI: ", style="bold green")
        chat_log.mount(
            Static(t, id=self._answer_key, classes="answer-msg"),
            before=anchor,
        )
        chat_log.scroll_end(animate=False)

    def _mount_before_anchor(self, *widgets) -> None:
        chat_log = self.query_one("#chat-log", VerticalScroll)
        try:
            answer_widget = self.query_one(f"#{self._answer_key}", Static)
            chat_log.mount(*widgets, before=answer_widget)
        except Exception:
            chat_log.mount(*widgets)

    def _ensure_thinking_block(self) -> None:
        if self._thinking_key:
            return
        self._thinking_key = self._next_key()
        header = SectionHeader(
            "思考中...", "#5c9cf5", self._thinking_key, id=f"{self._thinking_key}-header"
        )
        body = SectionBody(id=f"{self._thinking_key}-body")
        self._mount_before_anchor(header, body)

    def _append_thinking(self, text: str) -> None:
        self._ensure_thinking_block()
        self._thinking_text += text
        try:
            body = self.query_one(f"#{self._thinking_key}-body", SectionBody)
            body.set_text(self._thinking_text)
            lines = self._thinking_text.count("\n") + 1
            header = self.query_one(f"#{self._thinking_key}-header", SectionHeader)
            header.set_title(f"思考 ({lines}行)")
            self.scroll_end()
        except Exception:
            pass

    def _ensure_tools_block(self) -> None:
        if self._tools_key:
            return
        self._tools_key = self._next_key()
        header = SectionHeader(
            "工具调用 (0)", "#f5a742", self._tools_key, id=f"{self._tools_key}-header"
        )
        body = SectionBody(id=f"{self._tools_key}-body")
        self._mount_before_anchor(header, body)

    def _append_tool(self, tool_name: str, status: str) -> None:
        self._ensure_tools_block()
        if status == "running":
            self._tools_list.append(f"● {tool_name} 运行中...")
        elif status == "completed":
            for i in range(len(self._tools_list) - 1, -1, -1):
                if tool_name in self._tools_list[i] and "●" in self._tools_list[i]:
                    self._tools_list[i] = f"✓ {tool_name}"
                    break
            else:
                self._tools_list.append(f"✓ {tool_name}")
        else:
            self._tools_list.append(f"✗ {tool_name}")

        try:
            header = self.query_one(f"#{self._tools_key}-header", SectionHeader)
            body = self.query_one(f"#{self._tools_key}-body", SectionBody)
            body.set_text("  " + "\n  ".join(self._tools_list))
            header.set_title(f"工具调用 ({len(self._tools_list)})")
            self.scroll_end()
        except Exception:
            pass

    def _ensure_agents_block(self) -> None:
        if self._agents_key:
            return
        self._agents_key = self._next_key()
        header = SectionHeader(
            "子代理 (0)", "#d783ec", self._agents_key, id=f"{self._agents_key}-header"
        )
        body = SectionBody(id=f"{self._agents_key}-body")
        self._mount_before_anchor(header, body)

    def _append_agent(self, title: str, status: str, label: str = "") -> None:
        self._ensure_agents_block()
        display = label or title or "(未命名)"
        icon = "●" if status == "running" else "✓" if status == "completed" else "✗"
        self._agents_list.append(f"{icon} {display}")
        try:
            header = self.query_one(f"#{self._agents_key}-header", SectionHeader)
            body = self.query_one(f"#{self._agents_key}-body", SectionBody)
            body.set_text("  " + "\n  ".join(self._agents_list))
            header.set_title(f"子代理 ({len(self._agents_list)})")
            self.scroll_end()
        except Exception:
            pass

    def _append_answer(self, text: str) -> None:
        if not self._answer_key:
            return
        self._answer_text += text
        try:
            widget = self.query_one(f"#{self._answer_key}", Static)
            t = Text()
            t.append("AI: ", style="bold green")
            t.append(self._answer_text, style="green")
            widget.update(t)
            self.scroll_end()
        except Exception:
            pass

    def _dispatch_event(self, event: dict) -> None:
        """分发从 web server 收到的事件到对应的 UI 更新函数"""
        t = event.get("type", "")
        content = event.get("content", "")

        if t == "thought_delta":
            if content:
                self._append_thinking(content)

        elif t in ("action_start", "tool_status"):
            tool = event.get("tool_name") or ""
            if tool:
                self._append_tool(tool, "running")

        elif t in ("action_end", "tool_result"):
            tool = event.get("tool_name") or ""
            if tool:
                self._append_tool(tool, "completed")

        elif t == "workflow_step":
            self._append_agent(
                event.get("title", ""),
                event.get("status", ""),
                event.get("label", ""),
            )

        elif t == "answer_delta":
            if content:
                self._append_answer(content)

        elif t == "error":
            if content:
                self._append_answer(f"\n⚠ 错误: {content}")

        elif t in ("final", "stream_end", "heartbeat", "workflow_plan",
                   "permission_ask", "memory_status", "artifact_warning"):
            pass  # 这些事件 TUI 暂不展示

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
        """如果以 / 开头，返回完整命令文本（不含 /，小写）；否则返回 None"""
        if not text.startswith("/"):
            return None
        return text[1:].strip().lower()

    def _handle_command(self, cmd: str) -> None:
        if cmd in ("exit", "quit", "q"):
            self.action_quit()
        elif cmd == "clear":
            if self._client:
                self._client.clear_memory()
                self._add_system_message("会话记忆已清空")
        elif cmd == "models":
            from floodmind.tui.dialogs.models import ModelsDialog
            self.app.push_screen(ModelsDialog(), callback=self._on_model_selected)
        elif cmd.startswith("model "):
            model_key = cmd[6:].strip()
            if self._client and model_key:
                ok = self._client.update_config(model_key=model_key)
                if ok:
                    self._client.model_name = model_key
                    self.sub_title = f"会话: {self._client.session_id}  |  {model_key}"
                    self._add_system_message(f"已切换模型: {model_key}")
                else:
                    self._add_system_message(f"切换模型失败: {model_key}")
        elif cmd in ("help", "h"):
            self._add_system_message(
                "可用命令：\n"
                "  /clear     - 清空会话记忆\n"
                "  /models    - 查看可用模型\n"
                "  /model <k> - 切换模型\n"
                "  /exit      - 退出\n"
                "  /help      - 显示帮助"
            )
        else:
            self._add_system_message(f"未知命令: /{cmd}  (输入 /help 查看帮助)")

    def _on_model_selected(self, model_key: str) -> None:
        """ModelsDialog 选择回调"""
        if not model_key or not self._client:
            return
        ok = self._client.update_config(model_key=model_key)
        if ok:
            self._client.model_name = model_key
            self.sub_title = f"会话: {self._client.session_id}  |  {model_key}"
            self._add_system_message(f"已切换模型: {model_key}")
        else:
            self._add_system_message(f"切换模型失败: {model_key}")

    @work(thread=True)
    async def _send_message(self, message: str):
        if not self._client:
            self.app.call_from_thread(self._add_system_message, "web server 未连接")
            return

        self.app.call_from_thread(self._disable_input)
        self.app.call_from_thread(self._add_user_message, message)
        self.app.call_from_thread(self._start_round)

        try:
            for event in self._client.stream_chat(message):
                try:
                    self.app.call_from_thread(self._dispatch_event, event)
                except Exception as e:
                    self.app.call_from_thread(
                        self._add_system_message, f"事件处理异常: {e}"
                    )
                    break
        except Exception as e:
            self.app.call_from_thread(self._append_answer, f"\n⚠ 异常: {e}")
        finally:
            self.app.call_from_thread(self._enable_input)
            self.app.call_from_thread(self.scroll_end)

    def _disable_input(self) -> None:
        input_box = self.query_one("#input-box", TextArea)
        input_box.read_only = True

    def _enable_input(self) -> None:
        input_box = self.query_one("#input-box", TextArea)
        input_box.read_only = False


def run_tui(
    host: str = "localhost",
    port: int = 13014,
    model: str = "",
    reasoning: bool = False,
):
    """启动 TUI（后台自动启动 web server）"""
    # reasoning 参数保留兼容（web server 端会处理 enable_reasoning 配置）
    app = SimpleTUI(host=host, port=port, model=model)
    app.run()


if __name__ == "__main__":
    run_tui()
