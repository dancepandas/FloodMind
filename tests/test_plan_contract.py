"""create_plan/update_plan 契约修复回归测试（3 坑：expected_deliverables / status 枚举 / 多余键）。"""

from unittest.mock import MagicMock

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.agent.native.types import AgentLoopState, ModelEvent
from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService


def _bare_agent() -> NativeFloodAgent:
    """不经过完整 __init__ 的 NativeFloodAgent，只设测试需要的字段（与 test_plan_update 同模式）。"""
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent.session_id = ""
    agent._current_run_context = None
    agent._event_bus = EventBus()
    agent._tracing_service = None
    agent._last_loop_state = AgentLoopState(session_id="test-session", run_id="run-1")
    return agent


def _create_plan_spec(agent: NativeFloodAgent):
    """从完整 agent 的工具注册表取 create_plan 的 ToolSpec。"""
    for spec in agent._orchestrator_registry.all():
        if spec.name == "create_plan":
            return spec
    raise AssertionError("create_plan 未注册")


def _full_agent() -> NativeFloodAgent:
    """完整 runtime agent（注册 create_plan/update_plan 等工具），stub 模型不联网。"""
    from floodmind.agent.native.model_client import ModelClient

    mc = ModelClient(api_key="k", base_url="https://mock.api/v1", model_name="mock-model")
    mc.stream_chat = lambda *a, **k: iter([
        ModelEvent(type="token", content="ok"), ModelEvent(type="done"),
    ])
    from floodmind.agent.native.native_flood_agent import NativeFloodAgent

    return NativeFloodAgent(
        llm_service=mc, session_id="t", system_prompt="你是助手。",
        workspace=None, memory=None,
    )


def test_pit1_expected_deliverables_accepts_string():
    """坑1：expected_deliverables 每项可为字符串（SDK 归一化），schema 不再拒 is not of type object。"""
    agent = _full_agent()
    spec = _create_plan_spec(agent)
    err = ToolExecutionService._validate_raw_parameters(spec, {
        "user_goal": "分析水位", "deliverables": "report",
        "steps": [{"title": "读取数据", "expected_deliverables": ["重点站水位时序"]}],
    })
    assert err is None, err


def test_pit3_extra_key_rejected_by_schema():
    """坑3：additionalProperties:false 后，多余键被 schema 干净拒绝（不再透传 tool.func 崩溃）。"""
    agent = _full_agent()
    spec = _create_plan_spec(agent)
    err = ToolExecutionService._validate_raw_parameters(spec, {
        "user_goal": "分析水位", "deliverables": "report",
        "steps": [{"title": "读取数据", "item": "stray"}],
    })
    assert err is not None
    assert "additional" in err.lower() or "properties" in err.lower()


def test_pit3_handler_tolerates_extra_kwargs():
    """坑3：handler 接受 **kwargs，多余键不再 TypeError。"""
    agent = _bare_agent()
    out = agent._handle_create_plan(
        user_goal="分析水位", deliverables="report",
        steps=[{"title": "读取数据", "expected_deliverables": ["重点站水位时序"]}],
        item="stray",  # 修复前：TypeError: unexpected keyword argument 'item'
    )
    assert "执行计划已创建" in out


def test_pit2_in_progress_normalized_to_running():
    """坑2：update_plan status=in_progress 被接受并归一化为 running（两套枚举不打架）。"""
    agent = _bare_agent()
    agent._handle_create_plan(
        user_goal="g", deliverables="report",
        steps=[{"step_id": "a", "title": "步骤A", "expected_deliverables": ["x"]}],
    )
    out = agent._handle_update_plan(action="update_step", step_id="a", status="in_progress")
    assert "错误" not in out, out
    step = agent._last_loop_state.plan.find_step("a")
    assert step["status"] == "running"
