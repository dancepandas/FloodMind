from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.workspace_service import build_folder_workspace
from floodmind.tools.file_tools import _impl_apply_patch
from floodmind.tools.session_context import set_runtime_context, set_session_context


def _bind_workspace(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
    set_runtime_context(RuntimeContext("s1", "s1", "run", "thread", "turn", path_service=PathService(project_root=tmp_path, workspace=ws)))
    return ws


def _reset(tmp_path):
    set_session_context("", output_dir="")
    set_runtime_context(RuntimeContext("", "", "", "", "", path_service=PathService(project_root=tmp_path)))


def test_apply_patch_rejects_external_section_before_any_write(tmp_path):
    ws = _bind_workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    patch = f"""*** Begin Patch
*** Add File: ok.txt
+ok
*** Add File: {outside}
+bad
*** End Patch
"""
    try:
        result = _impl_apply_patch(patch=patch)
    finally:
        _reset(tmp_path)

    assert "权限拒绝" in result
    assert not (ws.workspace_dir / "ok.txt").exists()
    assert not outside.exists()


def test_apply_patch_allows_workspace_add(tmp_path):
    ws = _bind_workspace(tmp_path)
    patch = """*** Begin Patch
*** Add File: ok.txt
+ok
*** End Patch
"""
    try:
        result = _impl_apply_patch(patch=patch)
    finally:
        _reset(tmp_path)

    assert "Created" in result
    assert (ws.workspace_dir / "ok.txt").read_text(encoding="utf-8") == "ok\n"


def test_apply_patch_delete_requires_ask_and_does_not_delete(tmp_path):
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "delete-me.txt"
    target.write_text("keep", encoding="utf-8")
    patch = """*** Begin Patch
*** Delete File: delete-me.txt
*** End Patch
"""
    try:
        result = _impl_apply_patch(patch=patch)
    finally:
        _reset(tmp_path)

    assert "权限拒绝" in result
    assert "删除" in result
    assert target.exists()


def test_apply_patch_move_target_is_checked(tmp_path):
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "move-me.txt"
    target.write_text("content", encoding="utf-8")
    outside = tmp_path / "outside" / "moved.txt"
    patch = f"""*** Begin Patch
*** Update File: move-me.txt
*** Move to: {outside}
@@ -1 +1 @@
-content
+changed
*** End Patch
"""
    try:
        result = _impl_apply_patch(patch=patch)
    finally:
        _reset(tmp_path)

    assert "权限拒绝" in result
    assert target.exists()
    assert not outside.exists()
