"""Documented tools must work through the real execution boundary."""
from pathlib import Path

import pytest

from scripts.check_doc_examples import load_examples

from floodmind.agent.runtime.contracts.tools import ToolCall
from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
from floodmind.agent.runtime.services.permission_service import PermissionService

ROOT = Path(__file__).resolve().parents[1]


def documented_tool():
    code = load_examples(ROOT)["tool-basic"]
    namespace = {}
    exec(compile(code, "adding-a-tool.md", "exec"), namespace)
    return namespace["make_tool"]()


def execute(tool, arguments):
    class Registry:
        def get(self, name):
            return tool if name == tool.name else None

    return ToolExecutionService(permission_service=PermissionService()).execute(
        ToolCall(id="example-1", name=tool.name, arguments=arguments),
        registry=Registry(), journal_authority=object(),
    )


def test_documented_tool_runs_through_execution_service():
    result = execute(documented_tool(), {"values": [2, 4, 6]})
    assert result.status == "completed"
    assert result.name == "mean_flow"
    assert result.tool_call_id == "example-1"
    assert result.content == "4.00"


@pytest.mark.parametrize("arguments", [{"values": []}, {"values": ["bad"]}, {}, {"values": [1], "extra": 1}])
def test_documented_tool_rejects_invalid_input(arguments):
    tool = documented_tool()
    executed = []
    tool.func = lambda **kwargs: executed.append(kwargs)
    result = execute(tool, arguments)
    assert result.status == "error"
    assert "INPUT_VALIDATION_FAILED" in result.content
    assert executed == []


def test_documented_tool_reports_execution_error():
    tool = documented_tool()

    def fail(**kwargs):
        raise RuntimeError("sensor unavailable")

    tool.func = fail
    result = execute(tool, {"values": [1]})
    assert result.status == "error"
    assert "TOOL_EXECUTION_ERROR" in result.content
    assert "sensor unavailable" in result.content
