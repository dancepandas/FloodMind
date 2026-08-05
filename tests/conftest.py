"""Shared pytest fixtures for FloodMind tests."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_llm_service():
    llm = MagicMock()
    llm.api_key = "mock-key"
    llm.base_url = "https://mock.api/v1"
    llm.model_name = "mock-model"
    llm.temperature = 0.3
    llm.max_tokens = 8192
    llm.enable_reasoning = False

    def _invoke(prompt="", system_prompt="", **kwargs):
        resp = MagicMock()
        resp.content = "mock response"
        resp.reasoning_content = None
        return resp

    llm.invoke = MagicMock(side_effect=_invoke)
    llm.chat = MagicMock(side_effect=lambda messages, **kw: _invoke())
    llm.stream = MagicMock(return_value=iter([]))
    return llm


@pytest.fixture
def mock_tool_registry():
    from floodmind.agent.runtime.contracts.tools import ToolSpec
    reg = MagicMock()
    dummy_tool = ToolSpec(
        name="test_tool",
        description="test",
        parameters={"type": "object", "properties": {}},
        func=lambda **kw: "ok",
    )
    reg.get = MagicMock(return_value=dummy_tool)
    reg.all = MagicMock(return_value=[dummy_tool])
    reg.tools_schema = MagicMock(return_value=[{"type": "function", "function": {"name": "test_tool"}}])
    return reg


@pytest.fixture(autouse=True)
def _no_mcp_servers_by_default(monkeypatch):
    """SDK 测试默认不连接真实本地 MCP server（保持 hermetic/可移植）。

    Agent/NativeFloodAgent 构造时（bare 与完整 runtime）会按 settings.mcp.servers 自动接入
    MCP 外部工具；测试环境里的 mcp.json 指向本机脚本，不应作为测试依赖。
    显式测试（如 test_mcp_client 的 MCP 接入用例）可在测试体内用 monkeypatch 覆盖
    ``settings.mcp.servers``，本 fixture 与之一致（monkeypatch 后写覆盖，teardown 各自恢复）。
    """
    from floodmind.config.settings import settings
    monkeypatch.setattr(settings.mcp, "servers", [])
