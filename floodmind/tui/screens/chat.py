"""FloodMind TUI - ChatScreen (DEPRECATED: use simple_tui.py instead)."""

import time

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll, Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Static

from floodmind.config.settings import settings
from floodmind.agent.native.model_client import ModelClient
from floodmind.memory import DualMemory, create_session as store_create_session
from floodmind.agent import create_flood_agent
from floodmind.tui.widgets.prompt import PromptInput
from floodmind.tui.widgets.message import UserMessage, AssistantMessage, AssistantMeta, ToolCard
from floodmind.tui.widgets.footer import StatusBar
from floodmind.tui.sidebar import SessionSidebar
from floodmind.tui.history import save_entry


class ChatPromptInput(PromptInput):
    BINDINGS = [Binding("escape", "app.pop_screen", "Close", key_display="esc")]


class ChatScreen(Screen[None]):

    CSS = """
    #chat-spacer-top { height: 1; }
    #chat-scroll { height: 1fr; padding: 0 1; }
    """

    def __init__(self, session_id: str, initial_text: str = ""):
        super().__init__()
        self._sid = session_id
        self._initial = initial_text
        self._agent = None
        self._model_name = settings.model.model_name
        self._current_msg = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="chat-main"):
                yield Vertical(id="chat-spacer-top")
                yield VerticalScroll(id="chat-scroll")
                yield Static(id="response-status")
                yield ChatPromptInput(id="prompt")
                yield StatusBar()
            yield SessionSidebar()

    async def on_mount(self) -> None:
        self._init_agent()
        sidebar = self.query_one(SessionSidebar)
        sidebar.update_title(f"Session {self._sid}")
        sidebar.update_model(self._model_name)
        prompt = self.query_one(ChatPromptInput)
        prompt.submit_ready = True
        prompt.focus()
        if self._initial:
            prompt.submit_ready = False
            self._set_status("Sending...")
            save_entry(self._initial)
            await self._add_user_message(self._initial)
            self._stream(self._initial)

    def _init_agent(self, model_name: str = ""):
        try:
            memory = None
            store_create_session(session_id=self._sid)
            if model_name:
                from floodmind.config.model_presets import get_preset
                preset = get_preset(model_name)
                if preset:
                    llm = ModelClient(
                        api_key=preset.get("api_key") or settings.model.api_key,
                        model_name=preset["model_name"],
                        base_url=preset.get("default_base_url") or "",
                        temperature=preset.get("default_temperature", 0.3),
                        max_tokens=preset.get("default_max_tokens", 8192),
                        enable_thinking=bool(settings.model.enable_reasoning),
                    )
                else:
                    llm = ModelClient.from_settings(
                        model_name=model_name,
                        temperature=settings.model.temperature,
                        max_tokens=settings.model.max_tokens,
                    )
            else:
                llm = ModelClient.from_settings(
                    temperature=settings.model.temperature,
                    max_tokens=settings.model.max_tokens,
                    enable_thinking=bool(settings.model.enable_reasoning),
                )
            if self._agent is not None and hasattr(self._agent, 'memory'):
                memory = self._agent.memory
                if hasattr(memory, 'set_llm'):
                    memory.set_llm(llm)
            if memory is None:
                memory = DualMemory(
                    session_id=self._sid,
                    context_window=settings.model.context_window,
                    llm=llm,
                )
            self._agent = create_flood_agent(
                llm_service=llm, memory=memory, session_id=self._sid,
            )
        except Exception as e:
            self.notify(str(e), title="Agent Error", severity="error")

    @on(PromptInput.PromptSubmitted)
    async def _on_submit(self, event: PromptInput.PromptSubmitted) -> None:
        text = event.text.strip()
        if text.startswith("/"):
            cmd = text[1:].split()[0].lower()
            if cmd in ("help", "h"):
                from floodmind.tui.dialogs.help import HelpDialog
                await self.app.push_screen(HelpDialog())
            elif cmd == "models":
                from floodmind.tui.dialogs.models import ModelsDialog
                chosen = await self.app.push_screen(ModelsDialog())
                if chosen:
                    settings.model.model_name = chosen
                    self._model_name = chosen
                    self._init_agent(model_name=chosen)
                    sidebar = self.query_one(SessionSidebar)
                    sidebar.update_model(chosen)
                    self.notify(f"已切换为 {chosen}", title="模型切换")
            elif cmd == "sessions":
                from floodmind.tui.dialogs.sessions import SessionsDialog
                await self.app.push_screen(SessionsDialog())
            elif cmd == "mcp":
                from floodmind.tui.dialogs.mcp import McpDialog
                await self.app.push_screen(McpDialog())
            elif cmd in ("new", "clear"):
                self.app.pop_screen()
            elif cmd in ("exit", "quit", "q"):
                self.app.exit()
            return

        save_entry(text)
        await self._add_user_message(text)
        prompt = self.query_one(ChatPromptInput)
        prompt.submit_ready = False
        self._set_status("Assistant is responding...")
        self._stream(text)

    async def _add_user_message(self, text: str) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        w = UserMessage(text)
        await scroll.mount(w)
        scroll.scroll_end(animate=False)

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#response-status", Static).update(f"  {text}")
        except Exception:
            pass

    @work(thread=True, group="agent")
    def _stream(self, user_input: str) -> None:
        if not self._agent:
            return
        t0 = time.time()
        try:
            for chunk in self._agent.stream(user_input):
                t = chunk.get("type", "")
                if t == "answer_delta":
                    self.app.call_from_thread(self._on_token, chunk.get("content", ""))
                elif t == "thought_delta":
                    self.app.call_from_thread(self._set_status, f"Thinking: {chunk.get('content', '')[:80]}...")
                elif t == "action_start":
                    name = chunk.get("tool_name", "?")
                    self.app.call_from_thread(self._add_tool, name)
                elif t == "action_end":
                    name = chunk.get("tool_name", "")
                    out = chunk.get("content", "")
                    self.app.call_from_thread(self._add_tool_done, name, out)
                elif t == "llm_step_start":
                    model = chunk.get("model", self._model_name)
                    iteration = chunk.get("iteration", 0)
                    self.app.call_from_thread(self._set_status, f"LLM: {model} (round {iteration + 1})")
                elif t == "llm_step_end":
                    reason = chunk.get("finish_reason", "?")
                    self.app.call_from_thread(self._set_status, f"Step done: {reason}")
                elif t == "retry_attempt":
                    attempt = chunk.get("attempt", 0)
                    self.app.call_from_thread(self._set_status, f"Retrying... (attempt {attempt})")
                elif t in ("error", "llm_token_error"):
                    self.app.call_from_thread(
                        self._set_status, f"Error: {chunk.get('content', '')}"
                    )
        except Exception as e:
            self.app.call_from_thread(self._set_status, f"Error: {e}")
        finally:
            dur = time.time() - t0
            self.app.call_from_thread(self._on_done, dur)

    def _on_token(self, text: str) -> None:
        try:
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            if self._current_msg is None:
                self._current_msg = AssistantMessage(text, model_name=self._model_name)
                scroll.mount(self._current_msg)
            else:
                self._current_msg.append_chunk(text)
            if scroll.scroll_y >= scroll.max_scroll_y - 3:
                scroll.scroll_end(animate=False)
        except Exception:
            pass

    def _add_tool(self, name: str) -> None:
        try:
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            scroll.mount(ToolCard(tool_name=name))
            scroll.scroll_end(animate=False)
        except Exception:
            pass

    def _add_tool_done(self, name: str, output: str) -> None:
        try:
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            scroll.mount(ToolCard(tool_name=name, output=output, is_done=True))
            scroll.scroll_end(animate=False)
        except Exception:
            pass

    def _on_done(self, dur: float) -> None:
        try:
            if self._current_msg:
                self._current_msg.finalize()
                scroll = self.query_one("#chat-scroll", VerticalScroll)
                meta = AssistantMeta(
                    model_name=self._model_name,
                    duration=f"{dur:.1f}s",
                )
                scroll.mount(meta)
                self._current_msg = None
            prompt = self.query_one(ChatPromptInput)
            prompt.submit_ready = True
            prompt.focus()
            self._set_status("")
        except Exception:
            pass
