"""Background Kill 验证链 + Journal 事件 + Reducer（§12）。"""
import sys
import time
from pathlib import Path

from floodmind.agent.runtime.contracts.run_state import RunStatus
from floodmind.agent.runtime.reducer import initial_run_state, reduce
from floodmind.agent.runtime.services.background_task_service import BackgroundTaskService
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def _sleep_cmd(seconds: float) -> list:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


class _RecordingAuthority:
    def __init__(self):
        self.events = []

    def emit(self, event_type, payload):
        self.events.append(event_type)


class _Sink:
    def __init__(self):
        self.events = []

    def __call__(self, event_type, payload):
        self.events.append((event_type, dict(payload)))


def test_start_emits_requested_and_started(tmp_path):
    sink = _Sink()
    svc = BackgroundTaskService(base_dir=str(tmp_path), event_sink=sink)
    task = svc.start("sess_1", "sleep 0.5", _sleep_cmd(0.5), cwd=str(tmp_path))
    types = [et for et, _ in sink.events]
    assert "background.start.requested" in types
    assert "background.started" in types
    requested = dict(sink.events[0][1])
    assert requested["task_id"] == task.task_id
    assert requested["command_sha256"]


def test_kill_verification_chain(tmp_path):
    sink = _Sink()
    svc = BackgroundTaskService(base_dir=str(tmp_path), event_sink=sink)
    task = svc.start("sess_1", "sleep 30", _sleep_cmd(30), cwd=str(tmp_path))
    assert task.status == "running"
    ok = svc.kill("sess_1", task.task_id)
    assert ok is True
    assert task.status == "killed"  # 确认退出后置 killed
    # 事件顺序：start.requested -> started -> kill.requested -> killed
    types = [et for et, _ in sink.events]
    i_req = types.index("background.kill.requested")
    i_killed = types.index("background.killed")
    assert i_req < i_killed
    assert task.exit_code is not None  # 退出码已确认


def test_kill_missing_task_returns_false(tmp_path):
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    assert svc.kill("sess_1", "bg_nope") is False


def test_kill_subscriber_never_observes_intermediate_status(tmp_path):
    """F1: kill() 收尾时订阅者不得收到 kill_requested/terminating 中间态。"""
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    seen = []
    svc.subscribe(lambda t: seen.append(t.status))
    task = svc.start("sess_1", "sleep 30", _sleep_cmd(30), cwd=str(tmp_path))
    assert svc.kill("sess_1", task.task_id) is True
    # 给 _watch 线程 finally 一个完成窗口；_finalize 幂等，不改变已收尾状态
    time.sleep(0.2)
    assert seen, "kill() 应立即可达订阅者"
    assert all(s not in ("kill_requested", "terminating") for s in seen)
    assert all(s in ("killed", "kill_failed", "completed", "failed") for s in seen)


def test_task_captures_thread_authority_and_meta_excludes_it(tmp_path):
    authority = object()
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    svc.bind_thread_authority(authority)
    task = svc.start("sess_1", "true", _sleep_cmd(0.1), cwd=str(tmp_path))
    svc.unbind_thread_authority()
    assert task.journal_authority is authority
    assert "journal_authority" not in task.to_meta_dict()


def test_nested_bind_restores_parent_authority(tmp_path):
    svc = BackgroundTaskService(base_dir=str(tmp_path))
    parent = _RecordingAuthority()
    child = _RecordingAuthority()
    svc.bind_thread_authority(parent)
    try:
        svc.bind_thread_authority(child)
        svc.unbind_thread_authority()

        task = svc.start("sess_1", "true", _sleep_cmd(0.1), cwd=str(tmp_path))
        assert task.journal_authority is parent
        assert "background.started" in parent.events

        deadline = time.time() + 10
        while "background.completed" not in parent.events and time.time() < deadline:
            time.sleep(0.05)

        assert parent.events.index("background.started") < parent.events.index("background.completed")
        assert child.events == []
    finally:
        svc.unbind_thread_authority()


def test_completion_emits_completed_event(tmp_path):
    sink = _Sink()
    svc = BackgroundTaskService(base_dir=str(tmp_path), event_sink=sink)
    task = svc.start("sess_1", "true", _sleep_cmd(0.1), cwd=str(tmp_path))
    deadline = time.time() + 10
    # 事件在 _watch 线程 finally 中发射，先于状态轮询完成——轮询事件而非状态
    while "background.completed" not in [et for et, _ in sink.events] and time.time() < deadline:
        time.sleep(0.05)
    assert "background.completed" in [et for et, _ in sink.events]
    done = dict(sink.events[-1][1])
    assert done["task_id"] == task.task_id
    assert done["status"] == "completed"


def test_completion_event_precedes_subscriber_notification(tmp_path):
    ordering = []
    svc = BackgroundTaskService(
        base_dir=str(tmp_path),
        event_sink=lambda event_type, payload: ordering.append(event_type),
    )
    svc.subscribe(lambda task: ordering.append("subscriber"), session_id="sess_1")
    svc.start("sess_1", "true", _sleep_cmd(0.1), cwd=str(tmp_path))
    deadline = time.time() + 10
    while "subscriber" not in ordering and time.time() < deadline:
        time.sleep(0.05)
    assert ordering.index("background.completed") < ordering.index("subscriber")


def test_reducer_tracks_active_background_tasks(tmp_path):
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    s = initial_run_state("run_1", thread_id="th")
    started = auth.new_envelope("background.started", {"task_id": "bg_1", "session_id": "sess_1"})
    s = reduce(s, started)
    assert "bg_1" in s.active_background_tasks
    done = auth.new_envelope("background.completed", {"task_id": "bg_1", "status": "completed"})
    s = reduce(s, done)
    assert s.active_background_tasks == []


def test_reduce_background_events_idempotent(tmp_path):
    auth = open_journal_authority(tmp_path / "j", conversation_id="c", task_id="t",
                                   run_id="run_1", thread_id="th", turn_id="tu")
    s = initial_run_state("run_1", thread_id="th")
    for _ in range(2):
        s = reduce(s, auth.new_envelope("background.started", {"task_id": "bg_1"}))
    assert s.active_background_tasks == ["bg_1"]  # 去重
