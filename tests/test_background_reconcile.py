"""Restart PID Reconcile + orphaned/unknown（§12 / §25.7）。"""
import json
import os
import sys
import time
from pathlib import Path

from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService


def _write_meta(background_dir: Path, task_id: str, status: str, pid: int = None,
                create_time: float = None, session_id: str = "sess_1") -> Path:
    task_dir = background_dir / session_id / "background" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "task_id": task_id, "session_id": session_id, "command": "sleep",
        "pid": pid, "status": status, "exit_code": None,
        "stdout_path": str(task_dir / "out.log"), "stderr_path": str(task_dir / "err.log"),
        "meta_path": str(task_dir / "meta.json"), "started_at": time.time(),
        "max_lifetime_seconds": 1800, "finished_at": None,
        "process_identity": {"pid": pid, "create_time": create_time},
    }
    (task_dir / "out.log").write_bytes(b"")
    (task_dir / "err.log").write_bytes(b"")
    p = task_dir / "meta.json"
    p.write_text(json.dumps(meta), encoding="utf-8")
    return p


def test_dead_pid_marked_orphaned(tmp_path):
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    _write_meta(tmp_path, "bg_dead", status="running", pid=99999999)  # 不存在的 pid
    res = svc.reconcile_background()
    assert res["orphaned"] == 1
    assert res["unknown"] == 0
    assert res["kept_running"] == 0
    # meta 被更新为 orphaned
    meta = json.loads((tmp_path / "sess_1" / "background" / "bg_dead" / "meta.json").read_text())
    assert meta["status"] == "orphaned"


def test_identity_mismatch_marked_unknown(tmp_path):
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    # 本进程的 pid 存在，但 create_time 不匹配 -> PID 复用/身份不符 -> unknown
    _write_meta(tmp_path, "bg_mismatch", status="running", pid=os.getpid(), create_time=1.0)
    res = svc.reconcile_background()
    assert res["unknown"] == 1
    assert res["orphaned"] == 0
    assert res["kept_running"] == 0
    meta = json.loads((tmp_path / "sess_1" / "background" / "bg_mismatch" / "meta.json").read_text())
    assert meta["status"] == "unknown"


def test_completed_tasks_untouched(tmp_path):
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    _write_meta(tmp_path, "bg_done", status="completed", pid=None, create_time=None)
    res = svc.reconcile_background()
    assert res["kept_running"] == 0
    assert res["orphaned"] == 0
    assert res["unknown"] == 0
    meta = json.loads((tmp_path / "sess_1" / "background" / "bg_done" / "meta.json").read_text())
    assert meta["status"] == "completed"


def test_reconcile_integrated_in_reconcile_service(tmp_path):
    """§16.4 step5：resume 时对 Background PID 做对账。"""
    from floodmind.agent.runtime.contracts.run_state import RunStatus
    from floodmind.agent.runtime.reducer import initial_run_state
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority
    from floodmind.agent.runtime.services.reconciliation_service import ReconciliationService

    svc = BackgroundTaskService(base_dir=str(tmp_path))
    _write_meta(tmp_path, "bg_orphan", status="running", pid=99999999)
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    rs = ReconciliationService(background_task_service=svc)
    result = rs.reconcile(auth, initial_run_state("run_1", thread_id="th"))
    assert result.background_killed >= 0  # 对账不抛异常，可执行
