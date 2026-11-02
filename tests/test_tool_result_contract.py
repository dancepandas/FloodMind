"""工具生产者、执行服务和重试保护必须共享结果语义。"""
import contextvars

from pydantic import BaseModel, field_validator

from floodmind.agent.runtime.contracts.tools import ToolCall, ToolSpec
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
from floodmind.agent.runtime.services.workspace_service import build_folder_workspace
from floodmind.tools import base_tools
from floodmind.tools.file_tools import ApplyPatch_tool
from floodmind.tools.session_context import set_session_context


def execute(tool, arguments):
    class Registry:
        def get(self, name):
            return tool if name == tool.name else None

    return ToolExecutionService().execute(
        ToolCall(id="contract-call", name=tool.name, arguments=arguments),
        registry=Registry(), journal_authority=object(),
    )


def test_partial_patch_failure_keeps_side_effect_but_reports_error(tmp_path):
    ws = build_folder_workspace("contract", primary_dir=tmp_path / "project").ensure()
    runtime = RuntimeContext(
        "contract", "contract", "run", "thread", "turn",
        path_service=PathService(project_root=tmp_path, workspace=ws),
    )

    def run():
        set_session_context(
            "contract", cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir),
            runtime_context=runtime,
        )
        return execute(ApplyPatch_tool.to_tool_spec(), {"patch": """*** Begin Patch
*** Add File: created.txt
+created
*** Update File: missing.txt
@@ -1 +1 @@
-old
+new
*** End Patch
"""})

    result = contextvars.copy_context().run(run)
    assert (ws.workspace_dir / "created.txt").read_text(encoding="utf-8") == "created\n"
    assert result.status == "error"
    assert "Created" in result.content
    assert "missing.txt" in result.content
    assert result.metadata["partial_side_effects"] is True
    assert result.metadata["do_not_retry_same_call"] is True
    assert result.metadata["sections"][0]["status"] == "completed"
    assert result.metadata["sections"][1]["status"] == "error"


def test_patch_move_failure_reports_content_already_written(tmp_path, monkeypatch):
    from floodmind.tools import file_tools

    ws = build_folder_workspace("contract", primary_dir=tmp_path / "project").ensure()
    target = ws.workspace_dir / "original.txt"
    target.write_text("old\n", encoding="utf-8")
    runtime = RuntimeContext(
        "contract", "contract", "run", "thread", "turn",
        path_service=PathService(project_root=tmp_path, workspace=ws),
    )

    def fail_move(*args, **kwargs):
        raise OSError("move unavailable")

    from floodmind.agent.runtime.contracts.permissions import PermissionBehavior, PermissionDecision

    monkeypatch.setattr(file_tools.shutil, "move", fail_move)
    monkeypatch.setattr(
        file_tools, "_check_patch_permissions",
        lambda sections: PermissionDecision(behavior=PermissionBehavior.ALLOW, reason="test approval"),
    )

    def run():
        set_session_context(
            "contract", cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir),
            runtime_context=runtime,
        )
        return execute(ApplyPatch_tool.to_tool_spec(), {"patch": """*** Begin Patch
*** Update File: original.txt
*** Move to: moved.txt
@@ -1 +1 @@
-old
+new
*** End Patch
"""})

    result = contextvars.copy_context().run(run)
    assert target.read_text(encoding="utf-8") == "new\n"
    assert not (ws.workspace_dir / "moved.txt").exists()
    assert result.status == "error"
    assert result.metadata["partial_side_effects"] is True
    assert result.metadata["sections"][0]["partial_side_effects"] is True
    assert result.metadata["do_not_retry_same_call"] is True
    assert "move unavailable" in result.content


def test_failed_partial_result_is_not_replayed_as_committed_success(tmp_path):
    from floodmind.agent.runtime.services.idempotency import find_committed_result
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority

    authority = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run", thread_id="thread", turn_id="turn",
    )
    authority.emit("tool.execution.failed", {
        "transaction_id": "partial", "call_id": "call", "tool_id": "ApplyPatch",
        "status": "error", "result_summary": "content updated; move failed",
        "idempotency_key": "partial-key", "partial_side_effects": True,
    })
    assert find_committed_result(authority, "partial-key") is None


def test_memory_search_schema_describes_actual_history_scope():
    tool = base_tools.search_memory.to_tool_spec()
    assert "Skills" not in tool.description
    assert "Skills" not in tool.parameters["properties"]["search_type"]["description"]
    assert "对话历史" in tool.description


def test_memory_write_without_runtime_permission_service_is_denied():
    from floodmind.agent.runtime.contracts.permissions import PermissionBehavior

    def run():
        decision = base_tools.add_memory.to_tool_spec().check_permissions({"content": "test"})
        assert decision.behavior == PermissionBehavior.DENY

    contextvars.Context().run(run)


def test_experience_write_without_runtime_permission_service_is_denied():
    from floodmind.agent.runtime.contracts.permissions import PermissionBehavior

    def run():
        tool = base_tools.ToolRegistry.get("AddTaskExperience").to_tool_spec()
        decision = tool.check_permissions({"path": "hydrology/forecast"})
        assert decision.behavior == PermissionBehavior.DENY

    contextvars.Context().run(run)


def test_tool_spec_permission_policy_uses_explicit_service():
    from floodmind.agent.runtime.contracts.permissions import (
        PermissionBehavior, ToolPermissionPolicy,
    )
    from floodmind.agent.runtime.services.permission_service import PermissionService

    tool = ToolSpec(
        name="ExplicitPermission", description="test", parameters={}, func=lambda: None,
        permission_policy=ToolPermissionPolicy(policy_type="ask", reason="approval required"),
    )
    decision = tool.check_permissions({}, permission_service=PermissionService())
    assert decision.behavior == PermissionBehavior.ASK
    assert decision.reason == "approval required"
    assert tool.check_permissions({}).behavior == PermissionBehavior.DENY


def test_schema_internal_error_never_executes_handler():
    class BrokenInput(BaseModel):
        value: str

        @field_validator("value")
        @classmethod
        def validate_value(cls, value):
            raise RuntimeError("validator unavailable")

    executed = []
    tool = ToolSpec(
        name="BrokenInput", description="test", parameters=BrokenInput.model_json_schema(),
        args_schema=BrokenInput, func=lambda **kwargs: executed.append(kwargs),
    )
    result = execute(tool, {"value": "unsafe"})
    assert result.status == "error"
    assert executed == []


def test_success_body_with_error_word_does_not_trigger_retry_block():
    output = "Process finished successfully: 错误：0 个失败"

    def run():
        for _ in range(4):
            assert base_tools._check_retry_guard_before_exec("ContractSuccess", value="same") is None
            assert base_tools._finalize_tool_output("ContractSuccess", output, value="same") == output

    contextvars.Context().run(run)
