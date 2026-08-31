"""ScheduledTaskRuntime 调度循环与僵尸恢复回归测试。"""

import threading
import time
from pathlib import Path

import pytest

from floodmind.agent.scheduled_task_runtime import ScheduledTaskRuntime


@pytest.fixture()
def runtime(tmp_path: Path) -> ScheduledTaskRuntime:
    return ScheduledTaskRuntime(storage_path=tmp_path / "tasks.json")


def _due_task(rt: ScheduledTaskRuntime, command: str = "做日报") -> dict:
    return rt.create_task(
        session_id="s1",
        command=command,
        scheduled_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


class TestSchedulerLoop:
    def test_scheduler_executes_due_task_and_completes(self, runtime):
        """D03 回归：claim_due_tasks 必须有调度循环驱动，任务真实执行并落终态。"""
        task = _due_task(runtime)
        executed = []

        def execute(t):
            executed.append(t["command"])
            return (True, "完成", "", [])

        runtime.start_scheduler(execute, poll_seconds=0.2, lookback_minutes=5)
        try:
            deadline = time.time() + 5
            while not executed and time.time() < deadline:
                time.sleep(0.05)
        finally:
            runtime.stop_scheduler()
        assert executed == ["做日报"]
        after = runtime.get_task(task["id"])
        assert after["status"] == "completed"
        assert after["last_result"] == "完成"

    def test_scheduler_refuses_double_start(self, runtime):
        runtime.start_scheduler(lambda t: None, poll_seconds=5)
        try:
            with pytest.raises(RuntimeError):
                runtime.start_scheduler(lambda t: None, poll_seconds=5)
        finally:
            runtime.stop_scheduler()

    def test_execute_callback_exception_fails_task(self, runtime):
        task = _due_task(runtime)

        def boom(t):
            raise RuntimeError("宿主回调异常")

        runtime.start_scheduler(boom, poll_seconds=0.2, lookback_minutes=5)
        try:
            deadline = time.time() + 5
            while runtime.get_task(task["id"])["status"] == "pending" and time.time() < deadline:
                time.sleep(0.05)
        finally:
            runtime.stop_scheduler()
        after = runtime.get_task(task["id"])
        assert after["status"] == "failed"
        assert "回调异常" in after["last_error"]


class TestStaleRecovery:
    def test_stale_running_recovered_to_pending(self, runtime):
        task = _due_task(runtime)
        claimed = runtime.claim_due_tasks(lookback_minutes=5)
        assert claimed and claimed[0]["status"] == "running"
        time.sleep(1.1)  # _now() 精度为秒，等 age > 0
        recovered = runtime.recover_stale_running(max_age_minutes=0.0)
        assert len(recovered) == 1
        after = runtime.get_task(task["id"])
        assert after["status"] == "pending"
        assert after["claimed_by"] == ""

    def test_one_shot_recovery_postpones_next_run(self, runtime):
        task = _due_task(runtime)
        runtime.claim_due_tasks(lookback_minutes=5)
        time.sleep(1.1)
        runtime.recover_stale_running(max_age_minutes=0.0)
        after = runtime.get_task(task["id"])
        assert after["status"] == "pending"
        # 一次性任务恢复后顺延 5 分钟，避免立即被再 claim 形成 fire 循环
        assert after["next_run_at"] > after["last_run_at"]

    def test_fresh_running_within_threshold_not_recovered(self, runtime):
        task = _due_task(runtime)
        runtime.claim_due_tasks(lookback_minutes=5)
        # 刚认领（age < 阈值 30min）且认领进程存活 → 不回收
        recovered = runtime.recover_stale_running(max_age_minutes=30.0)
        assert recovered == []
        assert runtime.get_task(task["id"])["status"] == "running"

    def test_claimed_by_records_process_identity(self, runtime):
        import os

        task = _due_task(runtime)
        runtime.claim_due_tasks(lookback_minutes=5)
        claimed_by = runtime.get_task(task["id"])["claimed_by"]
        assert claimed_by.startswith(f"{os.getpid()}@")


class TestConcurrency:
    def test_parallel_create_tasks_no_loss(self, tmp_path):
        rt = ScheduledTaskRuntime(storage_path=tmp_path / "tasks.json")
        errors = []

        def create(i):
            try:
                rt.create_task(session_id="s1", command=f"任务{i}",
                               scheduled_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(rt.list_tasks()) == 8
