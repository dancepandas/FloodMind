"""P7 Task 4 — 子代理专用 Background Namespace。"""
import sys
from pathlib import Path

from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService


def _sleep_cmd(seconds):
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_child_namespace_isolated_and_journaled_to_child_authority(tmp_path):
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority

    child_auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th_child", turn_id="tu_child",
    )
    svc = BackgroundTaskService(base_dir=str(tmp_path / "sessions"))
    svc.bind_thread_authority(child_auth)
    try:
        t = svc.start("sub-1", "sleep", _sleep_cmd(0.2), cwd=str(tmp_path))
    finally:
        svc.unbind_thread_authority()
    # 命名空间隔离
    assert [x.task_id for x in svc.child_namespace("sub-1")] == [t.task_id]
    assert svc.list("other-session") == []
    assert svc.get("other-session", t.task_id) is None
    # child-auth journaling（P6 线程绑定）
    assert t.journal_authority is child_auth
    svc.kill_session("sub-1")
    assert svc.has_active("sub-1") is False
