"""工具输出失败语义：文本前缀约定 → ToolResult.status 归一。"""

from unittest.mock import MagicMock

from floodmind.agent.native.types import RunContext
from floodmind.agent.runtime.contracts.tools import ToolCall
from floodmind.agent.runtime.services.permission_service import PermissionService
from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
from floodmind.common.tool_output import tool_output_is_error


def test_error_output_is_detected_by_leading_prefix():
    assert tool_output_is_error("错误：文件不存在: D:/x.txt")
    assert tool_output_is_error("  命令执行失败（退出码 1）")
    assert tool_output_is_error("[执行失败:TOOL_EXECUTION_ERROR] boom")
    assert tool_output_is_error("搜索文件失败：无法访问目录")


def test_file_content_mentioning_error_word_is_not_misclassified():
    assert not tool_output_is_error("=== 文件: notes.txt ===\n1: 错误：这是文件内容")
    assert not tool_output_is_error("Hello world")
    assert not tool_output_is_error("")


def _execute(output: str):
    tool = MagicMock()
    tool.name = "Read"
    tool.permission_policy = None
    tool.validate_input.return_value = MagicMock(valid=True)
    tool.args_schema = None
    tool.func = lambda **_kwargs: output

    registry = MagicMock()
    registry.get.return_value = tool
    service = ToolExecutionService(permission_service=PermissionService())
    context = RunContext(
        session_id="s1", user_text="test", output_dir="/tmp/out", upload_dir="/tmp/up",
    )
    call = ToolCall(id="c1", name="Read", arguments={"file_path": "x.txt"})
    return service.execute(call, context=context, registry=registry, journal_authority=object())


def test_missing_file_result_is_reported_as_error_status():
    result = _execute("错误：文件不存在: /tmp/x.txt")

    assert result.status == "error"
    assert result.metadata.get("error_code") == "TOOL_REPORTED_ERROR"
    assert "文件不存在" in result.content


def test_successful_result_stays_completed():
    result = _execute("=== 文件: x.txt ===\n1: hello")

    assert result.status == "completed"
    assert "error_code" not in result.metadata
