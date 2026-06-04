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
