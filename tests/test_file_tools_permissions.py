from pathlib import Path

from floodmind.agent.runtime.services.path_service import PathService, set_path_service
from floodmind.agent.runtime.services.workspace_service import build_folder_workspace
from floodmind.tools.file_tools import _impl_glob, _impl_grep
from floodmind.tools.session_context import set_session_context


def test_glob_default_path_uses_workspace_cwd(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    (ws.workspace_dir / "src").mkdir()
    (ws.workspace_dir / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
    set_path_service(PathService(project_root=tmp_path, workspace=ws))
    set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
    try:
        result = _impl_glob("**/*.py")
    finally:
        set_session_context("", output_dir="")
        set_path_service(PathService(project_root=tmp_path))

    assert "src" in result
    assert "app.py" in result
    assert str(ws.workspace_dir) in result


def test_glob_external_path_rejected_without_fallback(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("x", encoding="utf-8")
    set_path_service(PathService(project_root=tmp_path, workspace=ws))
    set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
    try:
        result = _impl_glob("**/*.py", path=str(outside))
    finally:
        set_session_context("", output_dir="")
        set_path_service(PathService(project_root=tmp_path))

    assert "搜索文件失败" in result
    assert "secret.py" not in result


def test_grep_external_path_rejected_without_fallback(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    (ws.workspace_dir / "allowed.txt").write_text("needle", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("needle", encoding="utf-8")
    set_path_service(PathService(project_root=tmp_path, workspace=ws))
    set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
    try:
        result = _impl_grep("needle", path=str(outside))
    finally:
        set_session_context("", output_dir="")
        set_path_service(PathService(project_root=tmp_path))

    assert "搜索内容失败" in result
    assert "secret.txt" not in result


def test_grep_default_path_uses_workspace_cwd(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    (ws.workspace_dir / "allowed.txt").write_text("needle", encoding="utf-8")
    set_path_service(PathService(project_root=tmp_path, workspace=ws))
    set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
    try:
        result = _impl_grep("needle")
    finally:
        set_session_context("", output_dir="")
        set_path_service(PathService(project_root=tmp_path))

    assert "allowed.txt" in result
    assert "needle" in result
