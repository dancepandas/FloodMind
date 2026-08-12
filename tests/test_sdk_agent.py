"""
Tests for FloodMind SDK — embedded Agent API.

覆盖:
  1. Agent 创建 (bare 模式)
  2. 自定义工具注册与调用
  3. 流式输出事件类型
  4. register_skill 编程式注册
  5. system_prompt 自定义
  6. DualMemory 自动创建
  7. 向后兼容 (NativeFloodAgent 默认路径)
  8. 多工具注册
  9. ToolSpec 兼容
  10. Agent.run() 非流式
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from floodmind.agent.api import Agent
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent
from floodmind.tools.agent_tool import build_agent_tool, AgentTool
from floodmind.skills.base import Skill, register_skill
from floodmind.memory.dual_memory import DualMemory
from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stream_text(text="Mock response from agent."):
    """Helper: mock stream_chat to return a simple text + done."""
    def side_effect(self, messages, **kwargs):
        yield ModelEvent(type="token", content=text)
        yield ModelEvent(type="done")
    return side_effect


def _stream_text_event(event_text, extra_content=None):
    """Helper: mock stream_chat to return specific content in a specific event type."""
    def side_effect(self, messages, **kwargs):
        ev = ModelEvent(type="token", content=event_text)
        if extra_content:
            ev.content_extras = extra_content
        yield ev
        yield ModelEvent(type="done")
    return side_effect


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def llm():
    """Real ModelClient with dummy credentials."""
    return ModelClient(
        api_key="mock-key",
        base_url="https://mock.api/v1",
        model_name="mock-model",
    )


@pytest.fixture
def sample_tools():
    """Build a set of sample tools for testing."""
    def echo(text: str = "") -> str:
        """Echo back the input."""
        return f"Echo: {text}"

    def add(a: int = 0, b: int = 0) -> str:
        """Add two numbers."""
        return f"{a} + {b} = {a + b}"

    return [
        build_agent_tool(func=echo, name="Echo", description="Echo back text"),
        build_agent_tool(func=add, name="Add", description="Add two numbers"),
    ]


# ---------------------------------------------------------------------------
# 1. Agent 创建 (bare 模式)
# ---------------------------------------------------------------------------

class TestAgentCreation:
    def test_create_with_minimal_args(self, llm):
        """最简创建：只传 llm。"""
        agent = Agent(llm=llm)
        assert agent is not None
        assert agent.raw._bare is True
        assert agent.raw.session_id.startswith("sdk-")
        assert len(agent.raw.session_id) == 36

    def test_create_with_tools(self, llm, sample_tools):
        """传入自定义工具。"""
        agent = Agent(llm=llm, tools=sample_tools)
        registry = agent.raw._orchestrator_registry
        tool_names = [t.name for t in registry.all()]
        # 4 = Echo/Add + GetSkill/GetTool；+3 后台任务工具（TaskOutput/TaskList/TaskKill）
        assert len(registry.all()) == 7
        assert "Echo" in tool_names
        assert "Add" in tool_names
        assert "GetSkill" in tool_names
        assert "GetTool" in tool_names
        assert {"TaskOutput", "TaskList", "TaskKill"} <= set(tool_names)

    def test_create_with_system_prompt(self, llm):
        """自定义提示词。"""
        prompt = "You are a hydrology expert."
        agent = Agent(llm=llm, system_prompt=prompt)
        prompts = agent.raw._orchestrator_executor._system_prompts
        assert any("hydrology expert" in p for p in prompts)

    def test_create_with_custom_memory(self, llm):
        """传入自定义 DualMemory。"""
        mem = DualMemory(session_id="custom-id")
        agent = Agent(llm=llm, memory=mem)
        assert agent.raw.memory is mem
        assert agent.raw.memory.session_id == "custom-id"

    def test_create_auto_creates_memory(self, llm):
        """不传 memory 时自动创建。"""
        agent = Agent(llm=llm)
        assert agent.raw.memory is not None
        assert agent.raw.memory.session_id == agent.session_id
        assert agent.session_id.startswith("sdk-")

    def test_default_session_id_is_generated_once_and_propagated(self, llm, tmp_path, monkeypatch):
        """生成的 canonical ID 同时用于 Agent、memory 与 workspace 命名空间。"""
        monkeypatch.chdir(tmp_path)
        generated_hex = "0123456789abcdef0123456789abcdef"
        with patch(
            "floodmind.agent.api.uuid.uuid4",
            return_value=SimpleNamespace(hex=generated_hex),
        ) as uuid4:
            agent = Agent(llm=llm)

        expected = f"sdk-{generated_hex}"
        assert uuid4.call_count == 1
        assert agent.session_id == expected
        assert agent.memory.session_id == expected
        workspace = agent.raw._effective_workspace()
        assert workspace.artifact_dir == tmp_path / ".floodmind" / "artifacts" / expected
        assert workspace.tmp_dir == tmp_path / ".floodmind" / "tmp" / expected
        assert workspace.scripts_dir == tmp_path / ".floodmind" / "scripts" / expected

    def test_two_default_agents_use_isolated_session_namespaces(self, llm, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        generated = [
            SimpleNamespace(hex="a" * 32),
            SimpleNamespace(hex="b" * 32),
        ]
        with patch("floodmind.agent.api.uuid.uuid4", side_effect=generated) as uuid4:
            first = Agent(llm=llm)
            second = Agent(llm=llm)

        assert uuid4.call_count == 2
        assert first.session_id == f"sdk-{'a' * 32}"
        assert second.session_id == f"sdk-{'b' * 32}"
        assert first.session_id != second.session_id
        assert first.memory.session_id == first.session_id
        assert second.memory.session_id == second.session_id
        assert first.raw._effective_workspace().artifact_dir != second.raw._effective_workspace().artifact_dir
        assert first.raw._effective_workspace().tmp_dir != second.raw._effective_workspace().tmp_dir
        assert first.raw._effective_workspace().scripts_dir != second.raw._effective_workspace().scripts_dir

    def test_explicit_sdk_agent_session_id_remains_unchanged(self, llm):
        with patch("floodmind.agent.api.uuid.uuid4") as uuid4:
            agent = Agent(llm=llm, session_id="sdk-agent")

        uuid4.assert_not_called()
        assert agent.session_id == "sdk-agent"
        assert agent.memory.session_id == "sdk-agent"

    def test_default_workspace_uses_current_cwd(self, llm, tmp_path, monkeypatch):
        """SDK 默认以启动目录作为 folder-first workspace。"""
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)

        agent = Agent(llm=llm, session_id="sdk-default")
        ws = agent.raw._effective_workspace()

        assert ws is not None
        assert ws.is_folder_first
        assert ws.workspace_dir == project.resolve()
        assert ws.default_cwd == project.resolve()
        assert (project / ".floodmind" / "sessions").is_dir()
        assert (project / ".floodmind" / "artifacts" / "sdk-default").is_dir()
        assert (project / ".floodmind" / "tmp" / "sdk-default").is_dir()
        assert (project / ".floodmind" / "scripts" / "sdk-default").is_dir()
        assert (project / ".floodmind" / "sandboxes").is_dir()

    def test_explicit_workspace_is_not_overridden(self, llm, tmp_path, monkeypatch):
        """显式 workspace 仍保持最高优先级。"""
        invocation = tmp_path / "invocation"
        explicit_root = tmp_path / "explicit"
        invocation.mkdir()
        monkeypatch.chdir(invocation)
        explicit = Workspace.from_folder(explicit_root, session_id="explicit").ensure()

        agent = Agent(llm=llm, session_id="sdk-default", workspace=explicit)

        assert agent.raw._effective_workspace() is explicit
        assert agent.raw._effective_workspace().workspace_dir == explicit_root.resolve()


# ---------------------------------------------------------------------------
# 2. 自定义工具注册与 schema
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def test_tool_registered_in_registry(self, llm, sample_tools):
        agent = Agent(llm=llm, tools=sample_tools)
        reg = agent.raw._orchestrator_registry
        assert reg.get("Echo") is not None
        assert reg.get("Add") is not None
        assert reg.get("NonExistent") is None

    def test_tool_schema_for_openai(self, llm):
        """工具 schema 符合 OpenAI function calling 格式。"""
        tool = build_agent_tool(
            func=lambda text="": text,
            name="TestTool",
            description="A test tool",
        )
        agent = Agent(llm=llm, tools=[tool])
        schemas = [
            s for s in agent.raw._orchestrator_registry.tools_schema()
            if s["function"]["name"] == "TestTool"
        ]
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "TestTool"
        assert schema["function"]["description"] == "A test tool"
        assert "parameters" in schema["function"]

    def test_accepts_both_agent_tool_and_tool_spec(self, llm):
        """同时接受 AgentTool 和 ToolSpec 两种格式。"""
        from floodmind.agent.runtime.contracts.tools import ToolSpec

        agent_tool = build_agent_tool(
            func=lambda x="": x, name="ToolA", description="Desc A",
        )
        tool_spec = ToolSpec(
            name="ToolB", description="Desc B",
            parameters={"type": "object", "properties": {}},
            func=lambda **kw: "ok",
        )
        agent = Agent(llm=llm, tools=[agent_tool, tool_spec])
        names = [t.name for t in agent.raw._orchestrator_registry.all()]
        assert "ToolA" in names
        assert "ToolB" in names

    def test_empty_tools(self, llm):
        """不传工具 — 只注册 catalog/skill 基础工具 + 后台任务工具。"""
        agent = Agent(llm=llm)
        names = agent.raw._orchestrator_registry.names()
        assert set(names) == {"GetSkill", "GetTool", "TaskOutput", "TaskList", "TaskKill"}


# ---------------------------------------------------------------------------
# 3. Agent.run() 非流式
# ---------------------------------------------------------------------------

class TestAgentRun:
    def test_run_returns_string(self, llm):
        """非流式 run 返回最终回答字符串。"""
        with patch.object(ModelClient, "stream_chat", _stream_text()):
            agent = Agent(llm=llm)
            result = agent.run("hello")
            assert isinstance(result, str)
            assert "Mock response" in result

    def test_run_with_tools_in_context(self, llm, sample_tools):
        """带工具时的 run 不崩溃。"""
        with patch.object(ModelClient, "stream_chat", _stream_text()):
            agent = Agent(llm=llm, tools=sample_tools)
            result = agent.run("echo hello")
            assert isinstance(result, str)

    def test_chat_is_alias_for_run(self, llm):
        """chat() 是 run() 的别名。"""
        with patch.object(ModelClient, "stream_chat", _stream_text()):
            agent = Agent(llm=llm)
            assert agent.run("hello") == agent.chat("hello")


# ---------------------------------------------------------------------------
# 4. Agent.stream() 流式输出
# ---------------------------------------------------------------------------

class TestAgentStream:
    def test_stream_yields_events(self, llm):
        """流式输出包含 answer_delta 事件。"""
        with patch.object(ModelClient, "stream_chat", _stream_text("Hello World")):
            agent = Agent(llm=llm)
            events = list(agent.stream("hello"))
            assert len(events) > 0
            types = [e["type"] for e in events]
            assert "answer_delta" in types or "final_text" in types

    def test_stream_event_structure(self, llm):
        """事件 dict 包含 type 字段。"""
        with patch.object(ModelClient, "stream_chat", _stream_text()):
            agent = Agent(llm=llm)
            for event in agent.stream("test"):
                assert "type" in event
                assert isinstance(event["type"], str)
                break

    def test_stream_with_tools(self, llm, sample_tools):
        """流式输出包含工具调用事件。"""
        from floodmind.agent.native.types import ToolCall

        calls = {"count": 0}

        def tool_then_text(self, messages, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                yield ModelEvent(type="token", content="Let me check...")
                yield ModelEvent(
                    type="tool_call_done",
                    tool_call=ToolCall(id="tc1", name="Echo", arguments={"text": "hello"}),
                )
                yield ModelEvent(type="done")
                return
            yield ModelEvent(type="token", content="Done")
            yield ModelEvent(type="done")

        with patch.object(ModelClient, "stream_chat", tool_then_text):
            agent = Agent(llm=llm, tools=sample_tools)
            events = list(agent.stream("echo hello"))
            types = [e["type"] for e in events]
            assert any(t in types for t in ("action_start", "answer_delta", "final_text"))


# ---------------------------------------------------------------------------
# 5. register_skill 编程式注册
# ---------------------------------------------------------------------------

class TestRegisterSkill:
    def test_register_new_skill(self):
        skill = Skill(name="test-skill-1", description="A test skill", prompt="Do test.")
        register_skill(skill)
        from floodmind.skills import SKILL_REGISTRY
        names = [s.name for s in SKILL_REGISTRY]
        assert "test-skill-1" in names

    def test_register_duplicate_replaces(self):
        skill_v1 = Skill(name="test-skill-2", description="V1", prompt="v1")
        skill_v2 = Skill(name="test-skill-2", description="V2", prompt="v2")
        register_skill(skill_v1)
        register_skill(skill_v2)
        from floodmind.skills import SKILL_REGISTRY
        matches = [s for s in SKILL_REGISTRY if s.name == "test-skill-2"]
        assert len(matches) == 1
        assert matches[0].description == "V2"

    def test_register_skill_from_sdk_import(self):
        """通过 floodmind 顶层 import 调用 register_skill。"""
        from floodmind import register_skill, Skill
        skill = Skill(name="sdk-test-skill", description="SDK test", prompt="OK")
        register_skill(skill)
        from floodmind.skills import SKILL_REGISTRY
        assert any(s.name == "sdk-test-skill" for s in SKILL_REGISTRY)


# ---------------------------------------------------------------------------
# 6. 向后兼容
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_native_flood_agent_default_not_bare(self):
        """不传 bare 时 NativeFloodAgent 保持原有行为。"""
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        import inspect
        sig = inspect.signature(NativeFloodAgent.__init__)
        assert sig.parameters["bare"].default is False

    def test_native_flood_agent_accepts_bare_kwargs(self):
        """kwargs 仍然可用。"""
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        import inspect
        sig = inspect.signature(NativeFloodAgent.__init__)
        for param in ["bare", "tools", "system_prompt"]:
            assert param in sig.parameters

    def test_model_client_unchanged(self):
        """ModelClient 接口不变。"""
        client = ModelClient(
            api_key="sk-test",
            base_url="https://test.api/v1",
            model_name="test-model",
        )
        assert client.api_key == "sk-test"
        assert client.model_name == "test-model"

    def test_build_agent_tool_unchanged(self):
        """build_agent_tool 接口不变。"""
        tool = build_agent_tool(
            func=lambda x="": x,
            name="LegacyTool",
            description="Legacy",
        )
        assert isinstance(tool, AgentTool)
        assert tool.name == "LegacyTool"

    def test_skill_registry_still_works(self):
        """原有 SKILL_REGISTRY 正常加载。"""
        from floodmind.skills import SKILL_REGISTRY
        assert isinstance(SKILL_REGISTRY, list)
        assert len(SKILL_REGISTRY) >= 0


# ---------------------------------------------------------------------------
# 7. 边界情况
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_agent_with_no_tools_no_prompt(self, llm):
        """零工具 + 默认提示词 — 不崩溃。"""
        with patch.object(ModelClient, "stream_chat", _stream_text()):
            agent = Agent(llm=llm)
            result = agent.run("test")
            assert result is not None

    def test_agent_repr(self, llm, sample_tools):
        agent = Agent(llm=llm, tools=sample_tools)
        rep = repr(agent)
        assert "Agent" in rep
        assert f"tools={len(agent.raw._orchestrator_registry.all())}" in rep

    def test_raw_property_access(self, llm):
        agent = Agent(llm=llm)
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        assert isinstance(agent.raw, NativeFloodAgent)

    def test_custom_session_id(self, llm):
        agent = Agent(llm=llm, session_id="my-custom-session")
        assert agent.raw.session_id == "my-custom-session"
        assert agent.raw.memory.session_id == "my-custom-session"

    def test_session_id_is_canonicalized_at_sdk_boundary(self, llm):
        agent = Agent(llm=llm, session_id="  sub-worker-123  ")
        assert agent.session_id == "sub-worker-123"
        assert agent.memory.session_id == "sub-worker-123"

    @pytest.mark.parametrize(
        "session_id",
        [".", "..", "name.", "../escape", "..\\escape", "/absolute", "C:\\escape", "bad\nid", "NUL.txt"],
    )
    def test_unsafe_explicit_session_id_is_rejected(self, llm, session_id):
        with pytest.raises(ValueError, match="session_id"):
            Agent(llm=llm, session_id=session_id)

    def test_stream_handles_empty_input(self, llm):
        """空输入不 crash。"""
        with patch.object(ModelClient, "stream_chat", _stream_text()):
            agent = Agent(llm=llm)
            events = list(agent.stream(""))
            assert len(events) >= 0


# ---------------------------------------------------------------------------
# 8. enable_search / enable_reasoning 透传
# ---------------------------------------------------------------------------

class TestAgentOptions:
    def test_enable_search(self, llm):
        agent = Agent(llm=llm, enable_search=True)
        assert agent.raw._enable_search is True

    def test_enable_search_default_false(self, llm):
        agent = Agent(llm=llm)
        assert agent.raw._enable_search is False

    def test_enable_reasoning(self, llm):
        agent = Agent(llm=llm, enable_reasoning=True)
        assert agent.raw._enable_reasoning is True


# ---------------------------------------------------------------------------
# 9. SDK 能力增强：on_event / last_usage / artifacts / permission / max_iter
# ---------------------------------------------------------------------------

class TestSdkEnhancements:
    def test_on_event_called_on_stream(self, llm):
        received = []
        agent = Agent(llm=llm, on_event=lambda e: received.append(e))
        with patch.object(ModelClient, "stream_chat", _stream_text("Hello")):
            list(agent.stream("hi"))
        assert len(received) > 0
        assert any(e["type"] in ("answer_delta", "final_text") for e in received)

    def test_on_event_called_on_run(self, llm):
        received = []
        agent = Agent(llm=llm, on_event=lambda e: received.append(e))
        with patch.object(ModelClient, "stream_chat", _stream_text()):
            result = agent.run("hi")
        assert "Mock response" in result
        assert len(received) > 0

    def test_on_event_exception_does_not_break_stream(self, llm):
        def bad_handler(event):
            raise RuntimeError("boom")
        agent = Agent(llm=llm, on_event=bad_handler)
        with patch.object(ModelClient, "stream_chat", _stream_text("Hello")):
            events = list(agent.stream("hi"))
        assert len(events) > 0  # 回调异常不中断流

    def test_last_usage_accumulated(self, llm):
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        agent = Agent(llm=llm)
        fake = iter([
            {"type": "token_usage", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            {"type": "token_usage", "prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            {"type": "final_text", "content": "done"},
        ])
        with patch.object(NativeFloodAgent, "stream", lambda self, msg: fake):
            list(agent.stream("hi"))
        usage = agent.last_usage
        assert usage["prompt_tokens"] == 30
        assert usage["completion_tokens"] == 10
        assert usage["total_tokens"] == 40

    def test_artifacts_collected(self, llm):
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        agent = Agent(llm=llm)
        fake = iter([
            {"type": "file_generated", "filename": "out.csv", "download_url": "/x/out.csv"},
            {"type": "image_generated", "filename": "plot.png", "image_url": "/x/plot.png"},
            {"type": "final_text", "content": "done"},
        ])
        with patch.object(NativeFloodAgent, "stream", lambda self, msg: fake):
            list(agent.stream("hi"))
        assert len(agent.artifacts) == 2
        assert agent.artifacts[0]["filename"] == "out.csv"
        assert agent.artifacts[1]["filename"] == "plot.png"

    def test_results_reset_each_call(self, llm):
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        agent = Agent(llm=llm)
        first = iter([{"type": "token_usage", "prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100}, {"type": "final_text", "content": "a"}])
        second = iter([{"type": "token_usage", "prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5}, {"type": "final_text", "content": "b"}])
        with patch.object(NativeFloodAgent, "stream", lambda self, msg: first):
            list(agent.stream("hi"))
        assert agent.last_usage["prompt_tokens"] == 100
        with patch.object(NativeFloodAgent, "stream", lambda self, msg: second):
            list(agent.stream("hi"))
        assert agent.last_usage["prompt_tokens"] == 5  # 每次调用重置，非跨调用累加

    def test_permission_handler_passthrough(self, llm, sample_tools):
        handler = lambda name, inp: True
        agent = Agent(llm=llm, tools=sample_tools, permission_handler=handler)
        assert agent.raw._tool_executor._permission_handler is handler

    def test_permission_decision_hook_passthrough(self, llm, sample_tools):
        hook = lambda name, inp, decision, policy: decision
        agent = Agent(llm=llm, tools=sample_tools, permission_decision_hook=hook)
        assert agent.raw._tool_executor._permission_decision_hook is hook

    def test_permission_decision_hook_upgrades_allow_to_ask(self, llm):
        """bare 模式：hook 把 ALLOW 升级为 ASK → 工具不执行，走 permission_ask 流程。

        后台线程模拟宿主（desktop）拒绝授权；executor 收到拒绝后回到 LLM，
        第二轮 mock 只出文本，流正常结束。
        """
        import threading
        import time

        from floodmind.agent.native.types import ToolCall
        from floodmind.agent.runtime.contracts.permissions import (
            PermissionAskResponse,
            PermissionBehavior,
            PermissionDecision,
        )
        from floodmind.agent.runtime.services.ask_service import get_ask_service

        called = {"count": 0}

        def echo(text=""):
            called["count"] += 1
            return f"Echo: {text}"

        def force_ask(tool_name, tool_input, sdk_decision, policy):
            if sdk_decision.behavior == PermissionBehavior.DENY:
                return sdk_decision
            return PermissionDecision(behavior=PermissionBehavior.ASK, reason="需要用户确认")

        tool = build_agent_tool(func=echo, name="Echo", description="echo")
        # tool_loading=False（eager）：避免 progressive fail-closed 先于权限层拦截工具，
        # 使本测试聚焦 permission_decision_hook 行为本身。
        agent = Agent(
            llm=llm,
            tools=[tool],
            permission_decision_hook=force_ask,
            max_iterations=5,
            tool_loading=False,
        )

        llm_calls = {"n": 0}

        def tool_then_text(self, messages, **kwargs):
            llm_calls["n"] += 1
            if llm_calls["n"] == 1:
                yield ModelEvent(type="token", content="checking")
                yield ModelEvent(type="tool_call_done", tool_call=ToolCall(id="tc1", name="Echo", arguments={"text": "hi"}))
                yield ModelEvent(type="done")
            else:
                yield ModelEvent(type="token", content="done without tool")
                yield ModelEvent(type="done")

        # 模拟宿主 UI：发现 pending ASK 后拒绝
        def _deny_pending():
            svc = get_ask_service()
            for _ in range(200):
                pending = svc.pending(session_id=agent.session_id)
                if pending:
                    svc.respond(PermissionAskResponse(
                        session_id=pending[0].session_id,
                        ask_id=pending[0].ask_id,
                        approved=False,
                    ))
                    return
                time.sleep(0.05)

        responder = threading.Thread(target=_deny_pending, daemon=True)
        responder.start()

        with patch.object(ModelClient, "stream_chat", tool_then_text):
            events = list(agent.stream("echo"))

        responder.join(timeout=2)

        assert called["count"] == 0  # ASK 挂起 → 用户拒绝 → 工具未执行
        ask_events = [e for e in events if e.get("type") == "permission_ask"]
        assert len(ask_events) == 1
        assert ask_events[0]["tool_name"] == "Echo"

    # ── v1.1.0 desktop-driven capabilities（#1 bare=False / #2 proxies / #3 kwargs） ──

    def test_bare_false_loads_full_runtime_builtin_tools(self, llm):
        """bare=False → 公共 Agent 走完整 runtime，注册表含内置工具。"""
        agent = Agent(llm=llm, bare=False)
        names = {t.name for t in agent.raw._orchestrator_registry.all()}
        assert any(n in names for n in ("Read", "Write", "Bash", "Glob", "Grep"))

    def test_bare_false_registers_unclassified_host_tools_only_for_orchestrator(self, llm, sample_tools):
        """Full mode preserves host tools on orchestrator but fails closed for specialist."""
        agent = Agent(llm=llm, bare=False, tools=sample_tools)
        orch = {t.name for t in agent.raw._orchestrator_registry.all()}
        spec = {t.name for t in agent.raw._specialist_registry.all()}
        assert "Echo" in orch and "Add" in orch
        assert "Echo" not in spec and "Add" not in spec

    def test_bare_false_registers_only_safe_host_tools_for_specialist(self, llm):
        readonly = build_agent_tool(
            func=lambda: "read",
            name="HostRead",
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
        )
        state_write = build_agent_tool(
            func=lambda: "write",
            name="HostStateWrite",
            is_readonly=False,
            permission_policy=ToolPermissionPolicy(policy_type="state_write"),
        )
        agent = Agent(llm=llm, bare=False, tools=[readonly, state_write])

        orch = {t.name for t in agent.raw._orchestrator_registry.all()}
        spec = {t.name for t in agent.raw._specialist_registry.all()}
        assert {"HostRead", "HostStateWrite"} <= orch
        assert "HostRead" in spec
        assert "HostStateWrite" not in spec

    def test_full_specialist_excludes_state_write_and_destructive_tools(self, llm):
        agent = Agent(llm=llm, bare=False)
        specialist = agent.raw._specialist_registry.all()
        names = {tool.name for tool in specialist}

        assert "GetSkill" in names
        assert {"Read", "Glob", "Grep"} <= names
        assert "CoreMemoryAppend" not in names
        assert "AddTaskExperience" not in names
        assert "CreateScheduledTask" not in names
        assert "CancelScheduledTask" not in names
        assert "TaskKill" not in names
        assert all(not tool.is_destructive for tool in specialist)
        assert all(
            getattr(getattr(tool, "permission_policy", None), "policy_type", None)
            != "state_write"
            for tool in specialist
        )

    def test_bare_false_keeps_host_system_prompt(self, llm):
        """P1-2：完整模式保留宿主 system_prompt，且 skill 刷新重建后仍在。"""
        host_prompt = "你是水文领域专用助手，只回答洪水预报相关问题。"
        agent = Agent(llm=llm, bare=False, system_prompt=host_prompt)
        prompts = agent.raw._orchestrator_executor.system_prompts
        assert host_prompt in prompts
        # skill 热插拔重建提示词后宿主段不丢
        agent.raw._rebuild_system_prompts()
        prompts_after = agent.raw._orchestrator_executor.system_prompts
        assert host_prompt in prompts_after


    def test_bare_true_default_keeps_legacy_behavior(self, llm, sample_tools):
        """默认 bare=True 行为不变：不含内置工具（仅自定义 + catalog 工具）。"""
        agent = Agent(llm=llm, tools=sample_tools)
        names = {t.name for t in agent.raw._orchestrator_registry.all()}
        assert "Read" not in names and "Write" not in names
        assert any("Echo" == n for n in names)

    def test_memory_session_id_clear_memory_proxies(self, llm):
        agent = Agent(llm=llm)
        assert agent.memory is agent.raw.memory
        assert agent.session_id.startswith("sdk-")
        assert agent.memory.session_id == agent.session_id
        agent.clear_memory()  # 委托底层，不抛异常

    def test_stream_forwards_kwargs_to_native(self, llm):
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent

        agent = Agent(llm=llm)
        captured = {}

        def fake_stream(self, msg, **kwargs):
            captured.update(kwargs)
            return iter([{"type": "final_text", "content": "ok"}])

        with patch.object(NativeFloodAgent, "stream", fake_stream):
            events = list(agent.stream("hi", abort_check=lambda: False, attachments=[]))
        assert events == [{"type": "final_text", "content": "ok"}]
        assert "abort_check" in captured
        assert captured["attachments"] == []

    def test_build_model_info_names_agent_model_client(self, llm):
        """#8：_build_model_info 优先使用 agent 已路由的 ModelClient.model_name。"""
        agent = Agent(llm=llm)
        agent.raw._model_client.model_name = "kimi-k2.7-code"
        info = agent.raw._build_model_info()
        assert "kimi-k2.7-code" in info
        assert info.startswith("当前模型:")

    def test_bare_mode_loads_skill_catalog_and_getskill(self, llm):
        """bare 模式也感知 skill：catalog 非空 + GetSkill 可用（CRUD 管理工具不进 bare）。"""
        agent = Agent(llm=llm)  # bare=True default
        assert agent.raw._skill_catalog  # 内置 skills 被发现
        names = {t.name for t in agent.raw._orchestrator_registry.all()}
        assert "GetSkill" in names
        assert "ListSkills" not in names  # skill CRUD 管理工具仅完整 runtime

    def test_bare_prompt_includes_skill_catalog(self, llm):
        """bare 模式 system prompt 注入"可用 skills"，与完整 runtime 一致。"""
        agent = Agent(llm=llm)
        prompt = agent.raw._orchestrator_executor.system_prompts[0]
        assert "## 可用 skills" in prompt
        assert "## 可用工具" in prompt

    def test_create_scheduled_task_description_not_misleading(self):
        """CreateScheduledTask 描述不把『后台』误导为立即运行进程，并指向 Bash。"""
        from floodmind.tools.base_tools import create_scheduled_task
        desc = create_scheduled_task.description
        assert "到点调度" in desc
        assert "Bash" in desc
        assert not desc.startswith("创建后台")

    def test_effective_workspace_auto_creates_folder_first(self):
        """无注入 workspace 时懒创建 folder-first cwd workspace（修复调度 workspace unknown）。"""
        from floodmind.agent.runtime.services.workspace_service import reset_workspace, set_workspace
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        from floodmind.agent.native.model_client import ModelClient

        token = set_workspace(None)  # 确保 contextvar 无 workspace
        try:
            agent = NativeFloodAgent(
                llm_service=ModelClient(api_key="k", base_url="http://mock/v1", model_name="m"),
                memory=None,
                session_id="sched-1",
                bare=False,
                workspace=None,
            )
            ws = agent._effective_workspace()  # 无注入时解析到 folder-first workspace
            assert ws is not None
            assert ws.mode == "folder_first"
            assert ".floodmind" in str(ws.state_dir)
        finally:
            reset_workspace(token)

    def test_permission_handler_denies_tool(self, llm):
        from floodmind.agent.native.types import ToolCall
        called = {"count": 0}

        def echo(text=""):
            called["count"] += 1
            return f"Echo: {text}"

        tool = build_agent_tool(func=echo, name="Echo", description="echo")
        agent = Agent(llm=llm, tools=[tool], permission_handler=lambda name, inp: False, max_iterations=3)

        def tool_then_text(self, messages, **kwargs):
            yield ModelEvent(type="token", content="checking")
            yield ModelEvent(type="tool_call_done", tool_call=ToolCall(id="tc1", name="Echo", arguments={"text": "hi"}))
            yield ModelEvent(type="done")

        with patch.object(ModelClient, "stream_chat", tool_then_text):
            list(agent.stream("echo"))
        assert called["count"] == 0  # permission_handler 拒绝 → 工具函数未执行

    def test_max_iterations_passthrough(self, llm):
        agent = Agent(llm=llm, max_iterations=7)
        assert agent.raw._max_iterations == 7
        assert agent.raw._orchestrator_executor.max_iterations == 7
        assert agent.raw._specialist_executor.max_iterations == 7

    @pytest.mark.parametrize("injected_window", [8192, 1_000_000])
    def test_injected_model_window_overrides_global_default_in_both_directions(
        self, llm, injected_window
    ):
        """The injected model preset wins whether its window is smaller or larger."""
        with patch(
            "floodmind.config.model_presets.get_preset",
            return_value={"max_context_tokens": injected_window},
        ):
            agent = Agent(llm=llm)
            assert agent.raw._resolve_context_window() == injected_window

        assert agent.raw._orchestrator_executor.context_window == injected_window
        assert agent.raw._specialist_executor.context_window == injected_window

    def test_bare_executors_have_isolated_compressors(self, llm):
        agent = Agent(llm=llm)
        orchestrator = agent.raw._orchestrator_executor._context_compressor
        specialist = agent.raw._specialist_executor._context_compressor

        assert orchestrator is not specialist
        orchestrator._last_summary = "orchestrator only"
        orchestrator._summary_coverage = (1, 2, "digest")
        assert specialist._last_summary is None
        assert specialist._summary_coverage is None

    def test_full_runtime_uses_resolved_window_iterations_and_isolated_compressors(self, llm):
        resolved_window = 123_456
        with patch(
            "floodmind.config.model_presets.get_preset",
            return_value={"max_context_tokens": resolved_window},
        ):
            agent = Agent(llm=llm, bare=False, max_iterations=7)

        raw = agent.raw
        orchestrator = raw._orchestrator_executor._context_compressor
        specialist = raw._specialist_executor._context_compressor
        assert raw._context_runtime.context_window == resolved_window
        assert raw._orchestrator_executor.context_window == resolved_window
        assert raw._specialist_executor.context_window == resolved_window
        assert raw._orchestrator_executor.max_iterations == 7
        assert raw._specialist_executor.max_iterations == 7
        assert orchestrator is not specialist

        orchestrator._last_summary = "must survive prompt rebuild"
        raw._rebuild_system_prompts()
        assert raw._orchestrator_executor._context_compressor is orchestrator
        assert raw._specialist_executor._context_compressor is specialist
        assert orchestrator._last_summary == "must survive prompt rebuild"
        assert specialist._last_summary is None


# ---------------------------------------------------------------------------
# 10. SDK public exports / tool loading / provider routing
# ---------------------------------------------------------------------------

    def test_import_floodmind_does_not_import_legacy_web_or_tui(self):
        import sys
        for name in ("floodmind.server", "floodmind.tui", "flask", "textual"):
            sys.modules.pop(name, None)

        import floodmind

        assert floodmind.Agent is Agent
        assert "floodmind.server" not in sys.modules
        assert "floodmind.tui" not in sys.modules
        assert "flask" not in sys.modules
        assert "textual" not in sys.modules

    def test_top_level_exports_include_provider_and_tool_loading(self):
        from floodmind import (
            ProviderCodec,
            MiniMaxCodec,
            ToolLoadingConfig,
            ToolLoader,
            SkillRegistry,
            SkillRoot,
            create_skill_registry,
            route_codec,
        )

        codec = route_codec("minimax", "MiniMax-M3", "https://api.minimaxi.com/v1")
        assert isinstance(codec, ProviderCodec)
        assert isinstance(codec, MiniMaxCodec)
        assert ToolLoader(ToolLoadingConfig(mode="eager")).mode == "eager"
        assert SkillRegistry is not None
        assert SkillRoot is not None
        assert callable(create_skill_registry)

    def test_route_codec_exposes_route_context(self):
        from floodmind import route_codec

        codec = route_codec("minimax", "MiniMax-M3", "https://api.minimaxi.com/v1")
        assert codec.name == "minimax"
        assert codec.provider_id == "minimax"
        assert codec.model_id == "MiniMax-M3"
        assert codec.base_url == "https://api.minimaxi.com/v1"

    def test_agent_tool_loading_false_uses_eager_mode(self, llm, sample_tools):
        agent = Agent(llm=llm, tools=sample_tools, tool_loading=False)
        assert agent.raw._tool_loading_config.mode == "eager"
        assert agent.raw._orchestrator_tool_loader.mode == "eager"

    def test_agent_tool_loading_true_registers_catalog_tools(self, llm, sample_tools):
        agent = Agent(llm=llm, tools=sample_tools, tool_loading=True)
        names = agent.raw._orchestrator_registry.names()
        assert agent.raw._tool_loading_config.mode == "progressive"
        assert "GetTool" in names

    def test_agent_accepts_tool_loading_config(self, llm, sample_tools):
        from floodmind import ToolLoadingConfig

        cfg = ToolLoadingConfig(
            mode="progressive",
            core_tools=["GetTool"],
            max_loaded_tools=3,
        )
        agent = Agent(llm=llm, tools=sample_tools, tool_loading=cfg)
        assert agent.raw._tool_loading_config is cfg
        assert agent.raw._orchestrator_tool_loader.config.max_loaded_tools == 3
        assert agent.raw._specialist_tool_loader.config.core_tools == ["GetTool"]


def test_agent_preserves_modelclient_enable_thinking():
    """Agent 不强制关闭 ModelClient 的 enable_thinking（desktop「模型思考」tag 根因）。

    修复前：NativeFloodAgent.stream(enable_reasoning=False 默认) 强制把
    model_client.enable_thinking 覆盖为 False → 请求 thinking:disabled → 无 thought_delta。
    修复后：enable_reasoning 默认 None，尊重 ModelClient 自身 enable_thinking=True →
    请求 thinking:adaptive → 模型流式推理。
    """
    from floodmind.agent.native.model_client import ModelClient

    mc = ModelClient(
        api_key="k", base_url="https://api.minimaxi.chat/v1",
        model_name="MiniMax-M3", enable_thinking=True, provider="minimax",
    )
    sent = []

    def capture_send(params):
        sent.append(dict(params))
        class _Resp:
            def chunks(self):
                return iter([])
        return _Resp()

    mc._transport.send = capture_send
    agent = Agent(llm=mc, session_id="t", bare=True, system_prompt="你是助手。")
    list(agent.stream("hi"))

    assert sent, "agent 应发出模型请求"
    extra = sent[-1].get("extra_body") or {}
    assert extra.get("thinking", {}).get("type") == "adaptive"
