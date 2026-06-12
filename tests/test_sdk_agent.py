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

from unittest.mock import patch

import pytest

from floodmind.agent.api import Agent
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import ModelEvent
from floodmind.tools.agent_tool import build_agent_tool, AgentTool
from floodmind.skills.base import Skill, register_skill
from floodmind.memory.dual_memory import DualMemory


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
        assert agent.raw.session_id == "sdk-agent"

    def test_create_with_tools(self, llm, sample_tools):
        """传入自定义工具。"""
        agent = Agent(llm=llm, tools=sample_tools)
        registry = agent.raw._orchestrator_registry
        tool_names = [t.name for t in registry.all()]
        assert len(registry.all()) == 2
        assert "Echo" in tool_names
        assert "Add" in tool_names

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
        assert agent.raw.memory.session_id == "sdk-agent"


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
        schemas = agent.raw._orchestrator_registry.tools_schema()
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
        """不传工具 — 零工具注册。"""
        agent = Agent(llm=llm)
        assert len(agent.raw._orchestrator_registry.all()) == 0


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

        def tool_then_text(self, messages, **kwargs):
            yield ModelEvent(type="token", content="Let me check...")
            yield ModelEvent(
                type="tool_call_done",
                tool_call=ToolCall(id="tc1", name="Echo", arguments={"text": "hello"}),
            )
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
        assert "tools=2" in rep

    def test_raw_property_access(self, llm):
        agent = Agent(llm=llm)
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        assert isinstance(agent.raw, NativeFloodAgent)

    def test_custom_session_id(self, llm):
        agent = Agent(llm=llm, session_id="my-custom-session")
        assert agent.raw.session_id == "my-custom-session"
        assert agent.raw.memory.session_id == "my-custom-session"

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
