"""G-08：工具输出上限与卸载（阈值 + 有界摘要 + full_ref 引用）。"""

from pathlib import Path
from unittest.mock import MagicMock

from floodmind.agent.native.types import RunContext
from floodmind.agent.runtime.contracts.tools import ToolCall
from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
from floodmind.agent.runtime.services.tool_output_service import (
    DEFAULT_MAX_CHARS,
    bound_tool_output,
)


class TestBoundToolOutput:
    def test_small_output_is_unchanged(self, tmp_path):
        content, ref = bound_tool_output(
            "ok", tool_name="T", call_id="c1", state_dir=str(tmp_path)
        )
        assert content == "ok"
        assert ref == ""
        assert not (tmp_path / "tool_outputs").exists()

    def test_oversized_output_is_bounded_and_offloaded(self, tmp_path):
        full = "HEAD" + ("x" * (DEFAULT_MAX_CHARS + 5000)) + "TAIL"
        content, ref = bound_tool_output(
            full, tool_name="BigTool", call_id="c1", state_dir=str(tmp_path)
        )
        assert len(content) <= DEFAULT_MAX_CHARS
        assert content.startswith("HEAD")
        assert content.endswith("TAIL")
        assert "全文已卸载至" in content
        assert ref
        assert Path(ref).read_text(encoding="utf-8") == full

    def test_without_state_dir_output_stays_bounded_and_says_so(self):
        full = "y" * (DEFAULT_MAX_CHARS + 1)
        content, ref = bound_tool_output(
            full, tool_name="BigTool", call_id="c1", state_dir=""
        )
        assert ref == ""
        assert len(content) <= DEFAULT_MAX_CHARS
        assert "无法卸载" in content

    def test_call_id_cannot_escape_offload_dir(self, tmp_path):
        full = "z" * (DEFAULT_MAX_CHARS + 1)
        _, ref = bound_tool_output(
            full, tool_name="../../evil", call_id="a/b", state_dir=str(tmp_path)
        )
        ref_path = Path(ref).resolve()
        assert ref_path.parent == (tmp_path / "tool_outputs").resolve()

    def test_non_string_content_is_coerced(self, tmp_path):
        content, _ = bound_tool_output(
            {"a": 1}, tool_name="T", call_id="c1", state_dir=str(tmp_path)
        )
        assert content == "{'a': 1}"


class TestExecutionServiceOffload:
    def test_oversized_tool_result_reaches_model_with_reference(self, tmp_path):
        svc = ToolExecutionService()
        huge = "r" * (DEFAULT_MAX_CHARS + 10)

        tool = MagicMock()
        tool.name = "BigTool"
        tool.permission_policy = None
        tool.check_permissions.return_value = True
        tool.validate_input.return_value = MagicMock(valid=True)
        tool.args_schema = None
        tool.func = lambda: huge

        reg = MagicMock()
        reg.get.return_value = tool
        ctx = RunContext(
            session_id="s1",
            user_text="test",
            output_dir=str(tmp_path / "out"),
            upload_dir=str(tmp_path / "up"),
            state_dir=str(tmp_path / "state"),
        )
        call = ToolCall(id="call-1", name="BigTool", arguments={})

        result = svc.execute(call, context=ctx, registry=reg, journal_authority=object())

        assert result.status == "completed"
        assert len(result.content) <= DEFAULT_MAX_CHARS
        ref = result.metadata["full_ref"]
        assert Path(ref).read_text(encoding="utf-8") == huge
        assert ref in result.content
