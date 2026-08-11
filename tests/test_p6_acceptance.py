"""P6 验收（§25.7 Background 相关项 + §23.2 OPEN 项关闭）。"""
import json
import sys
import time
from pathlib import Path

from floodmind.agent.runtime.services.artifact_service import ArtifactService
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.reconciliation_service import ReconciliationService
from floodmind.agent.runtime.services.sandbox_service import SandboxService
from floodmind.agent.runtime.contracts.artifacts import ArtifactDeclaration


def _sleep_cmd(seconds: float) -> list:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_acceptance_background_session_isolation(tmp_path):
    """§25.7 Background Session 隔离：跨 Session 不可查询/不可杀。"""
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    t = svc.start("sess_a", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
    assert svc.get("sess_a", t.task_id) is not None
    assert svc.get("sess_b", t.task_id) is None  # 跨 session 不可见
    assert svc.kill("sess_b", t.task_id) is False  # 跨 session 不可杀
    svc.kill_session("sess_a")


def test_acceptance_cleanup_no_residual_subscriptions(tmp_path):
    """§25.7 Cleanup 无残留订阅：unsubscribe 后不再收到回调。"""
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    got = []
    unsub = svc.subscribe(lambda t: got.append(t.task_id), session_id="sess_a")
    unsub()
    t = svc.start("sess_a", "true", _sleep_cmd(0.1), cwd=str(tmp_path))
    deadline = time.time() + 10
    while t.status == "running" and time.time() < deadline:
        time.sleep(0.05)
    settle_deadline = time.time() + 0.2
    while t.task_id not in got and time.time() < settle_deadline:
        time.sleep(0.01)
    assert t.task_id not in got  # 已退订，无残留


def test_acceptance_kill_verification_chain(tmp_path):
    """§23.2 OPEN 关闭：kill 验证链 kill_requested->terminating->killed/kill_failed。"""
    events = []
    svc = BackgroundTaskService(base_dir=str(tmp_path), event_sink=lambda et, p: events.append(et))
    t = svc.start("sess_a", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
    assert t.status == "running"
    ok = svc.kill("sess_a", t.task_id)
    assert ok is True
    assert t.status == "killed"
    chain = [e for e in events if e.startswith("background.")]
    # start.requested, started, kill.requested, killed 顺序成立
    assert chain.index("background.kill.requested") < chain.index("background.killed")
    # killed 前必须经过 terminating（服务内部状态，非事件）；这里断言 killed 事件存在且 exit_code 已确认
    assert t.exit_code is not None


def test_acceptance_restart_pid_reconcile(tmp_path, monkeypatch):
    """§25.7 Host 重启后可 Reconcile PID/Meta。"""
    from floodmind.agent.runtime.services import process_identity
    monkeypatch.setattr(process_identity, "process_exists", lambda pid: False)
    monkeypatch.setattr(process_identity, "pid_identity_matches", lambda pid, create_time: False)
    # 模拟重启后重建服务：遗留 running meta，PID 已死 -> orphaned
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    task_dir = tmp_path / "sess_a" / "background" / "bg_old"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "out.log").write_bytes(b"")
    (task_dir / "err.log").write_bytes(b"")
    (task_dir / "meta.json").write_text(json.dumps({
        "task_id": "bg_old", "session_id": "sess_a", "command": "sleep",
        "pid": 99999999, "status": "running", "exit_code": None,
        "process_identity": {"pid": 99999999, "create_time": None},
    }), encoding="utf-8")
    res = svc.reconcile_background()
    assert res["orphaned"] == 1
    meta = json.loads((task_dir / "meta.json").read_text())
    assert meta["status"] == "orphaned"


def test_acceptance_artifact_survives_sandbox_destroy(tmp_path):
    """§25.7 Artifact 不因 Sandbox 销毁丢失。"""
    base = tmp_path / "sessions"
    sb = SandboxService(base_dir=base)
    ctx = sb.create("sub_1")
    src = ctx.workspace_dir / "result.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    store = tmp_path / "artifacts"
    svc = ArtifactService(store, allowed_roots=[str(ctx.workspace_dir)])
    m = svc.publish(ArtifactDeclaration(
        logical_name="result.csv", source_path=str(src),
        producer_thread_id="th", producer_call_id="call_1",
    ))
    sb.destroy(ctx)
    assert not src.exists()
    assert svc.resolve(m.artifact_id).logical_name == "result.csv"
    assert svc.read_path(m.artifact_id).read_text() == "a,b\n1,2\n"


def test_acceptance_reconcile_does_not_raise(tmp_path):
    """§25.7/§16.4：对账路径对无后台任务场景不抛异常。"""
    from floodmind.agent.runtime.reducer import initial_run_state
    from floodmind.agent.runtime.services.journal_authority import open_journal_authority

    svc = BackgroundTaskService(base_dir=str(tmp_path))
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    rs = ReconciliationService(background_task_service=svc)
    res = rs.reconcile(auth, initial_run_state("run_1", thread_id="th"))
    assert res.background_killed == 0
