"""FloodMind TUI — MainScreen (DEPRECATED: use simple_tui.py instead).

重构要点：
- 使用 SlotRegistry 插槽系统，支持动态扩展
- 响应式断点: >140/100-140/80-100/60-80/<60
- 键盘导航: Tab/1-3/gg/G/?/q
- @work(thread=True) 处理网络 I/O，不阻塞 UI
- 完整的消息发送/流式接收逻辑
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen

from floodmind.tui.widgets.sidebar import Sidebar
from floodmind.tui.widgets.chat_panel import ChatPanel
from floodmind.tui.widgets.detail_panel import DetailPanel
from floodmind.tui.widgets.input_bar import InputBar
from floodmind.tui.widgets.status_footer import StatusFooter
from floodmind.tui.web_client import FloodMindClient
from floodmind.tui.server_manager import ensure_web_server, stop_web_server


class MainScreen(Screen):
    """主屏幕 — IDE 三面板 + 插槽布局。"""

    BINDINGS = [
        # 面板切换
        Binding("1", "toggle_sidebar", "侧边栏", key_display="1"),
        Binding("2", "focus_chat", "聊天", key_display="2"),
        Binding("3", "toggle_detail", "详情", key_display="3"),
        Binding("tab", "cycle_focus", "切换焦点"),
        # 导航
        Binding("question_mark", "help", "帮助", key_display="?"),
        Binding("slash", "command_palette", "命令", key_display="/"),
        Binding("ctrl+p", "command_palette", "命令面板"),
        # 会话
        Binding("r", "refresh", "刷新"),
        # Vim 风格
        Binding("g,g", "goto_top", "顶部"),
        Binding("G", "goto_bottom", "底部"),
    ]

    # reactive 状态
    sidebar_visible = reactive(True)
    detail_visible = reactive(True)
    terminal_width = reactive(120)

    def __init__(
        self,
        client: FloodMindClient | None = None,
        host: str = "localhost",
        port: int = 13014,
        model_hint: str = "",
        session_id: str = "",
        initial_text: str = "",
    ):
        super().__init__()
        self._client = client
        self._host = host
        self._port = port
        self._model_hint = model_hint
        self._session_id = session_id
        self._initial_text = initial_text
        self._started_server = False
        # 流式状态
        self._is_streaming = False

    def compose(self) -> ComposeResult:
        """组合布局 — 使用插槽系统。"""
        with Horizontal(id="main-layout"):
            # 侧边栏（插槽容器）
            yield Sidebar(id="sidebar")
            # 中心区域
            with Vertical(id="center-column"):
                yield ChatPanel(id="chat")
                yield StatusFooter(id="footer")
                yield InputBar(id="input")
            # 详情面板
            yield DetailPanel(id="detail")

    def on_mount(self) -> None:
        self.title = "FloodMind"
        self._apply_responsive()
        input_bar = self.query_one("#input", InputBar)
        input_bar.focus()
        # 连接完成前禁止发送，避免 _client 为 None 时触发 "未连接到服务器"
        input_bar.ready = False
        # 后台连接，不阻塞 UI 线程
        self._connect_worker()
        # 注册插槽内容
        self._register_slots()

    def _register_slots(self) -> None:
        """注册默认插槽内容。"""
        app = self.app
        if hasattr(app, "slots"):
            # 清空已有插槽，避免重复注册
            app.slots.clear()
            # 侧边栏插槽
            sidebar = self.query_one("#sidebar", Sidebar)
            app.slots.register("sidebar_top", sidebar._build_header(), order=100)
            app.slots.register("sidebar_content", sidebar._build_session_list(), order=200)
            app.slots.register("sidebar_bottom", sidebar._build_footer(), order=900)

    # ── 连接管理（后台线程，不阻塞 UI）────────────

    @work(thread=True, exclusive=True)
    def _connect_worker(self) -> None:
        """后台连接 Web 服务器。"""
        if self._client is not None:
            self.call_from_thread(self._on_connected)
            return
        ok, started = ensure_web_server(self._host, self._port)
        if not ok:
            self.call_from_thread(
                self._on_connect_failed,
                f"无法启动或连接 Web 服务器 ({self._host}:{self._port})"
            )
            return
        self._started_server = started
        self._client = FloodMindClient(
            base_url=f"http://{self._host}:{self._port}",
            session_id=self._session_id,
        )
        self.call_from_thread(self._on_connected)

    def _on_connected(self) -> None:
        """连接成功后的 UI 更新（在主线程）。"""
        footer = self.query_one("#footer", StatusFooter)
        if footer:
            footer.status = "已连接"
            footer.refresh()
        input_bar = self.query_one("#input", InputBar)
        input_bar.ready = True
        input_bar.focus()
        # 如果有从 HomeScreen 传递来的初始消息，自动发送
        if self._initial_text:
            self._send_message(self._initial_text)

    def _on_connect_failed(self, message: str) -> None:
        """连接失败后的 UI 更新（在主线程）。"""
        footer = self.query_one("#footer", StatusFooter)
        if footer:
            footer.status = f"未连接"
            footer.refresh()
        input_bar = self.query_one("#input", InputBar)
        input_bar.ready = False
        self.notify(message, severity="error", timeout=10)

    # ── 消息发送与流式接收 ──────────────────────

    @on(InputBar.Submitted)
    def _on_input_submitted(self, event: InputBar.Submitted) -> None:
        """用户提交消息。"""
        text = event.text.strip()
        if not text:
            return
        self._send_message(text)

    def _send_message(self, text: str) -> None:
        """发送消息并启动流式接收。"""
        if self._is_streaming:
            self.notify("请等待当前响应完成", severity="warning")
            return
        if self._client is None:
            self.notify("未连接到服务器", severity="error")
            return

        # 清空初始文本标记（只发送一次）
        self._initial_text = ""

        # UI 状态更新
        input_bar = self.query_one("#input", InputBar)
        input_bar.ready = False
        input_bar.clear()

        chat = self.query_one("#chat", ChatPanel)
        chat.write_user(text)

        footer = self.query_one("#footer", StatusFooter)
        footer.status = "思考中..."
        footer.refresh()

        detail = self.query_one("#detail", DetailPanel)
        detail.clear_all()

        self._is_streaming = True

        # 后台流式处理
        self._stream_worker(text)

    @work(thread=True, exclusive=True)
    def _stream_worker(self, user_text: str) -> None:
        """后台线程：流式调用 Web API，通过 call_from_thread 更新 UI。"""
        if not self._client:
            return
        try:
            for event in self._client.stream_chat(user_text):
                self._dispatch_stream_event(event)
        except Exception as e:
            self.call_from_thread(
                self._on_stream_error, str(e)
            )
        finally:
            self.call_from_thread(self._on_stream_done)

    def _dispatch_stream_event(self, event: dict) -> None:
        """在后台线程中分发事件到 UI 更新（通过 call_from_thread）。"""
        etype = event.get("type", "")
        if etype == "answer_delta":
            self.call_from_thread(self._on_answer_delta, event.get("content", ""))
        elif etype == "thought_delta":
            self.call_from_thread(self._on_thought_delta, event.get("content", ""))
        elif etype == "action_start":
            self.call_from_thread(
                self._on_action_start,
                event.get("tool_name", ""),
                event.get("call_id", ""),
            )
        elif etype == "action_end":
            self.call_from_thread(
                self._on_action_end,
                event.get("tool_name", ""),
                event.get("content", ""),
            )
        elif etype == "workflow_step":
            self.call_from_thread(
                self._on_workflow_step,
                event.get("title", ""),
                event.get("status", ""),
            )
        elif etype == "error":
            self.call_from_thread(self._on_stream_error, event.get("content", "未知错误"))
        elif etype == "llm_token_error":
            self.call_from_thread(self._on_stream_error, event.get("content", "Token 余额不足"))
        elif etype == "stream_end":
            pass  # finally 中处理

    # ── UI 更新回调（均在主线程执行）─────────────

    def _on_answer_delta(self, text: str) -> None:
        chat = self.query_one("#chat", ChatPanel)
        if not chat.streaming:
            chat.write_ai_start()
        chat.append_ai_chunk(text)

    def _on_thought_delta(self, text: str) -> None:
        detail = self.query_one("#detail", DetailPanel)
        detail.add_thinking(text)

    def _on_action_start(self, tool_name: str, call_id: str) -> None:
        chat = self.query_one("#chat", ChatPanel)
        if not chat.streaming:
            chat.write_ai_start()
        detail = self.query_one("#detail", DetailPanel)
        detail.add_tool(tool_name, status="running")
        footer = self.query_one("#footer", StatusFooter)
        footer.status = f"运行: {tool_name}"
        footer.refresh()

    def _on_action_end(self, tool_name: str, output: str) -> None:
        detail = self.query_one("#detail", DetailPanel)
        detail.add_tool(tool_name, status="completed", output=output)

    def _on_workflow_step(self, title: str, status: str) -> None:
        detail = self.query_one("#detail", DetailPanel)
        detail.add_agent(title, status=status)

    def _on_stream_error(self, message: str) -> None:
        chat = self.query_one("#chat", ChatPanel)
        chat.write_system(f"错误: {message}")
        footer = self.query_one("#footer", StatusFooter)
        footer.status = f"错误: {message[:30]}"
        footer.refresh()

    def _on_stream_done(self) -> None:
        chat = self.query_one("#chat", ChatPanel)
        if chat.streaming:
            chat.write_ai_end()
        footer = self.query_one("#footer", StatusFooter)
        footer.status = "已连接"
        footer.refresh()
        input_bar = self.query_one("#input", InputBar)
        input_bar.ready = True
        input_bar.focus()
        self._is_streaming = False

    # ── 响应式布局 ──────────────────────────────

    def on_resize(self) -> None:
        """响应终端尺寸变化。"""
        self.terminal_width = self.size.width
        self._apply_responsive()

    def _apply_responsive(self) -> None:
        """应用响应式布局规则。

        断点：
        - >140: 三面板全显
        - 100-140: 侧边栏可折叠
        - 80-100: 隐藏详情面板
        - 60-80: 仅聊天面板
        - <60: 极简模式
        """
        w = self.terminal_width
        sidebar = self.query_one("#sidebar")
        detail = self.query_one("#detail")
        chat = self.query_one("#chat")

        if w < 60:
            sidebar.display = False
            detail.display = False
            chat.styles.width = "100%"
        elif w < 80:
            sidebar.display = self.sidebar_visible
            detail.display = False
            chat.styles.width = "1fr"
        elif w < 100:
            sidebar.display = self.sidebar_visible
            detail.display = False
            chat.styles.width = "1fr"
        else:
            sidebar.display = self.sidebar_visible
            detail.display = self.detail_visible
            chat.styles.width = "1fr"

    # ── 动作处理 ────────────────────────────────

    def action_toggle_sidebar(self) -> None:
        self.sidebar_visible = not self.sidebar_visible
        self._apply_responsive()

    def action_toggle_detail(self) -> None:
        self.detail_visible = not self.detail_visible
        self._apply_responsive()

    def action_focus_chat(self) -> None:
        chat = self.query_one("#chat", ChatPanel)
        chat.focus()

    def action_cycle_focus(self) -> None:
        """Tab 循环焦点。"""
        widgets = [
            self.query_one("#sidebar", Sidebar),
            self.query_one("#chat", ChatPanel),
            self.query_one("#detail", DetailPanel),
            self.query_one("#input", InputBar),
        ]
        visible = [w for w in widgets if w.display]
        if not visible:
            return
        try:
            idx = visible.index(self.focused)
        except ValueError:
            idx = -1
        next_widget = visible[(idx + 1) % len(visible)]
        next_widget.focus()

    def action_goto_top(self) -> None:
        chat = self.query_one("#chat", ChatPanel)
        chat.action_goto_top()

    def action_goto_bottom(self) -> None:
        chat = self.query_one("#chat", ChatPanel)
        chat.action_goto_bottom()

    def action_help(self) -> None:
        from floodmind.tui.dialogs.help import HelpDialog
        self.app.push_screen(HelpDialog())

    def action_command_palette(self) -> None:
        from floodmind.tui.dialogs.command_palette import CommandPaletteDialog
        self.app.push_screen(CommandPaletteDialog())

    def action_refresh(self) -> None:
        chat = self.query_one("#chat", ChatPanel)
        chat.refresh()

    def on_unmount(self) -> None:
        if self._started_server:
            stop_web_server()
