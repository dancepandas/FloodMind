"""NativeFloodAgent 上下文窗口解析回归：跟随注入模型，而非 catalog 第一个。"""

from unittest.mock import patch

from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.native_flood_agent import NativeFloodAgent

_CFG = {
    "providers": {
        "test-provider": {
            "base_url": "https://mock.api/v1",
            "api_key": "mock-key",
            "models": [
                {"id": "global-default", "context_window": 65536},
                {"id": "injected-model", "context_window": 1_000_000},
            ],
        }
    }
}


def _make_agent(model_name: str) -> NativeFloodAgent:
    return NativeFloodAgent(
        llm_service=ModelClient(
            api_key="mock-key",
            base_url="https://mock.api/v1",
            model_name=model_name,
        ),
        memory=None,
        session_id="native-context-window",
        bare=True,
        workspace=None,
    )


def test_native_context_window_follows_injected_model():
    """注入模型是 catalog 第二个时，窗口取该模型而非 catalog 第一个。"""
    with patch("floodmind.config.model_resolver.get_config", return_value=_CFG):
        agent = _make_agent("injected-model")
        assert agent._resolve_context_window() == 1_000_000

    assert agent._orchestrator_executor.context_window == 1_000_000
    assert agent._specialist_executor.context_window == 1_000_000


def test_native_context_window_unknown_model_uses_catalog_default():
    """模型不在 catalog 时回退 catalog 默认模型窗口。"""
    with patch("floodmind.config.model_resolver.get_config", return_value=_CFG):
        agent = _make_agent("not-in-catalog")
        assert agent._resolve_context_window() == 65536

    assert agent._orchestrator_executor.context_window == 65536
