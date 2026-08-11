"""Tests for BackgroundTaskService — 后台任务（exec_bash run_in_background）。"""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from floodmind.agent.runtime.services.background_task_service import (
    BackgroundTask,
    BackgroundTaskService,
)


def _sleep_cmd(seconds: float = 5):
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _wait_status(svc, task, statuses, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.status in statuses:
            return task.status
        time.sleep(0.05)
    return task.status


class TestBackgroundTaskService:
    @pytest.mark.parametrize(
        "session_id",
        [".", "..", "name.", "../escape", "..\\escape", "/absolute", "C:\\escape", "bad\x00id", "CON"],
    )
    def test_direct_service_rejects_unsafe_session_ids(self, tmp_path, session_id):
        svc = BackgroundTaskService(base_dir=tmp_path)

        with pytest.raises(ValueError, match="session_id"):
            svc.start(session_id, "noop", [sys.executable, "-c", "pass"], cwd=str(tmp_path))

        assert list(tmp_path.iterdir()) == []

    def test_background_directory_is_contained_under_configured_root(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        background = svc._background_dir("sub-worker-123")

        assert background == (tmp_path / "sub-worker-123" / "background").resolve()
        assert background.is_relative_to(tmp_path.resolve())

    def test_existing_symlink_cannot_redirect_background_storage(self, tmp_path):
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        link = tmp_path / "linked-session"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("directory symlinks are unavailable on this platform")

        svc = BackgroundTaskService(base_dir=tmp_path)
        with pytest.raises(ValueError, match="超出会话根目录"):
            svc._background_dir("linked-session")

    def test_start_writes_files_and_completes(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        task = svc.start("s1", "echo hi", [sys.executable, "-c", "print('hello-bg')"], cwd=str(tmp_path))
        assert task.status == "running"
        assert task.task_id
        assert Path(task.stdout_path).exists()
        assert Path(task.meta_path).exists()

        status = _wait_status(svc, task, {"completed", "failed"})
        assert status == "completed"
        assert task.exit_code == 0
        assert "hello-bg" in Path(task.stdout_path).read_text(encoding="utf-8")

        drained = svc.drain_completions("s1")
        assert any(t.task_id == task.task_id for t in drained)

    def test_subscribe_callback_fired(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        events = []
        svc.subscribe(lambda t: events.append(t))
        svc.start("s1", "x", [sys.executable, "-c", "pass"], cwd=str(tmp_path))
        deadline = time.time() + 10
        while time.time() < deadline and not events:
            time.sleep(0.05)
        assert events
        assert events[0].status in ("completed", "failed")

    def test_session_filtered_and_legacy_subscriptions(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        filtered, legacy = [], []
        unsubscribe = svc.subscribe(filtered.append, session_id="s1")
        svc.subscribe(legacy.append)

        first = svc.start("s2", "other", [sys.executable, "-c", "pass"], cwd=str(tmp_path))
        second = svc.start("s1", "mine", [sys.executable, "-c", "pass"], cwd=str(tmp_path))
        assert _wait_status(svc, first, {"completed", "failed"}) == "completed"
        assert _wait_status(svc, second, {"completed", "failed"}) == "completed"
        deadline = time.time() + 2
        while time.time() < deadline and len(legacy) < 2:
            time.sleep(0.01)

        assert [task.session_id for task in filtered] == ["s1"]
        assert {task.session_id for task in legacy} == {"s1", "s2"}
        unsubscribe()
        unsubscribe()

    def test_read_tail_is_bounded(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        log = tmp_path / "large.log"
        log.write_bytes(b"x" * 1_000_000 + "结尾".encode("utf-8"))
        with patch.object(Path, "read_text", side_effect=AssertionError("must not read whole file")):
            tail = svc._read_tail(str(log), limit=20)
        assert len(tail) <= 20
        assert tail.endswith("结尾")

    def test_kill_session_does_not_hold_lock_during_kill_or_callback(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        task = svc.start("s1", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
        observed = []
        original_kill = svc._kill_process

        def lock_available_from_other_thread():
            result = []

            def probe():
                acquired = svc._lock.acquire(timeout=1)
                result.append(acquired)
                if acquired:
                    svc._lock.release()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(timeout=2)
            return result == [True]

        def checked_kill(process):
            observed.append(lock_available_from_other_thread())
            original_kill(process)

        svc._kill_process = checked_kill
        svc.subscribe(lambda _: observed.append(lock_available_from_other_thread()))
        assert svc.kill_session("s1") == 1
        assert observed == [True, True]
        assert task.status == "killed"

    def test_completed_and_finalized_retention_is_bounded(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path, completed_retention=2, finalized_retention=3)
        tasks = [
            svc.start("s1", str(i), [sys.executable, "-c", "pass"], cwd=str(tmp_path))
            for i in range(5)
        ]
        for task in tasks:
            assert _wait_status(svc, task, {"completed", "failed"}) == "completed"
        deadline = time.time() + 2
        while time.time() < deadline and len(svc._finalized) < 3:
            time.sleep(0.01)
        assert len(svc._completed) <= 2
        assert len(svc._finalized) <= 3
        # 审计文件不随内存 retention 淘汰。
        assert all(Path(task.meta_path).exists() for task in tasks)

    def test_concurrent_limit_guard(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path, max_concurrent_per_session=2)
        svc.start("s1", "a", _sleep_cmd(5), cwd=str(tmp_path))
        svc.start("s1", "b", _sleep_cmd(5), cwd=str(tmp_path))
        with pytest.raises(RuntimeError, match="上限"):
            svc.start("s1", "c", [sys.executable, "-c", "pass"], cwd=str(tmp_path))
        assert svc.kill_session("s1") == 2

    def test_concurrent_starts_reserve_limit_slot_atomically(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path, max_concurrent_per_session=1)
        entered_popen = threading.Barrier(2)
        release_popen = threading.Event()
        original_popen = __import__("subprocess").Popen
        result = []

        def blocked_popen(*args, **kwargs):
            entered_popen.wait(timeout=5)
            assert release_popen.wait(timeout=5)
            return original_popen(*args, **kwargs)

        def first_start():
            try:
                result.append(svc.start("s1", "first", _sleep_cmd(30), cwd=str(tmp_path)))
            except Exception as exc:
                result.append(exc)

        with patch("floodmind.agent.runtime.services.background_task_service.subprocess.Popen", side_effect=blocked_popen):
            thread = threading.Thread(target=first_start)
            thread.start()
            entered_popen.wait(timeout=5)
            with pytest.raises(RuntimeError, match="上限"):
                svc.start("s1", "second", _sleep_cmd(30), cwd=str(tmp_path))
            release_popen.set()
            thread.join(timeout=10)

        assert len(result) == 1 and isinstance(result[0], BackgroundTask)
        assert len(svc.list("s1")) == 1
        svc.kill_session("s1")

    def test_failed_start_releases_pending_slot(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path, max_concurrent_per_session=1)
        with patch(
            "floodmind.agent.runtime.services.background_task_service.subprocess.Popen",
            side_effect=OSError("spawn failed"),
        ):
            with pytest.raises(OSError, match="spawn failed"):
                svc.start("s1", "bad", _sleep_cmd(1), cwd=str(tmp_path))
        task = svc.start("s1", "good", [sys.executable, "-c", "pass"], cwd=str(tmp_path))
        assert _wait_status(svc, task, {"completed", "failed"}) == "completed"

    def test_kill_marks_killed(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        task = svc.start("s1", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
        assert svc.kill("s1", task.task_id) is True
        status = _wait_status(svc, task, {"killed", "failed", "completed"})
        assert status == "killed"

    def test_kill_notifies_subscribers_immediately(self, tmp_path):
        """用户主动 kill 立即进完成队列并通知订阅者（Agent 感知状态变化，不等 wait 线程）。"""
        svc = BackgroundTaskService(base_dir=tmp_path)
        events = []
        svc.subscribe(lambda t: events.append(t))
        task = svc.start("s1", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
        assert svc.kill("s1", task.task_id) is True
        # kill() 同步收尾：订阅者应立即收到，且状态为 killed
        assert len(events) == 1, f"kill 后应立即可达 1 个通知，实际 {len(events)}"
        assert events[0].status == "killed"
        # 完成队列同样立即可 drain
        drained = svc.drain_completions("s1")
        assert any(t.task_id == task.task_id for t in drained)

    def test_kill_unknown_task_returns_false(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        assert svc.kill("s1", "nope") is False

    def test_get_rejects_cross_session_active_task(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        task = svc.start("s1", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
        try:
            assert svc.get("s2", task.task_id) is None
            assert svc.get("s1", task.task_id) is task
        finally:
            svc.kill_session("s1")

    def test_task_output_rejects_cross_session_active_task(self, tmp_path):
        from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
        from floodmind.tools.base_tools import _impl_task_output
        from floodmind.tools.session_context import set_runtime_context, set_session_context

        svc = BackgroundTaskService(base_dir=tmp_path)
        task = svc.start("s1", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
        set_session_context("s2", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path))
        set_runtime_context(RuntimeContext("s2", "s2", "run", "thread", "turn", background_service=svc))
        try:
            assert "未找到后台任务" in _impl_task_output(task.task_id)
        finally:
            svc.kill_session("s1")
            set_runtime_context(None)
            set_session_context("", output_dir="", state_dir="")

    def test_max_lifetime_kills(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path, max_lifetime_seconds=1)
        task = svc.start("s1", "sleep", _sleep_cmd(30), cwd=str(tmp_path), max_lifetime_seconds=1)
        status = _wait_status(svc, task, {"killed", "failed", "completed"}, timeout=10)
        assert status == "killed"


class TestExecBashBackground:
    def test_run_in_background_returns_task_id(self, tmp_path):
        from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
        from floodmind.tools.base_tools import _impl_exec_bash
        from floodmind.tools.session_context import set_runtime_context, set_session_context

        state_dir = tmp_path / ".floodmind"
        out_dir = tmp_path / "out"
        svc = BackgroundTaskService(base_dir=state_dir / "sessions")
        set_session_context("s1", output_dir=str(out_dir), state_dir=str(state_dir))
        set_runtime_context(RuntimeContext("s1", "s1", "run", "thread", "turn", background_service=svc))
        try:
            result = _impl_exec_bash(
                command=f"{sys.executable} -c 'import time; time.sleep(2)'",
                run_in_background=True,
            )
        finally:
            svc.kill_session("s1")
            set_session_context("", output_dir="")
            set_runtime_context(None)
        assert "task_id=" in result
        assert "TaskOutput" in result

    def test_sync_path_unaffected(self, tmp_path):
        """同步路径不带 run_in_background 参数时行为不变。"""
        from floodmind.tools.base_tools import _impl_exec_bash
        from floodmind.tools.session_context import set_session_context

        out_dir = tmp_path / "out"
        set_session_context("s1", output_dir=str(out_dir), state_dir=str(tmp_path / ".floodmind"))
        try:
            result = _impl_exec_bash(command=f"{sys.executable} -c 'print(1)'", timeout=10)
        finally:
            set_session_context("", output_dir="")
        assert "1" in result
        assert "task_id" not in result


class TestExecutorInjection:
    def test_injects_background_notification(self, tmp_path):
        """完成的后台任务以 user 消息注入下一次 LLM 调用。"""
        from floodmind.agent.native.executor import NativeAgentExecutor
        from floodmind.agent.native.message_builder import MessageBuilder
        from floodmind.agent.native.event_bus import EventBus
        from floodmind.agent.native.model_client import ModelClient
        from floodmind.agent.native.types import ModelEvent, RunContext

        svc = BackgroundTaskService(base_dir=tmp_path)
        # 直接构造一个已完成任务进完成队列（不真正跑进程）
        done = BackgroundTask(
            task_id="t-complete",
            session_id="test-session",
            command="echo hi",
            pid=123,
            status="completed",
            exit_code=0,
            stdout_path=str(tmp_path / "out.log"),
            stderr_path=str(tmp_path / "err.log"),
            meta_path=str(tmp_path / "meta.json"),
            started_at=time.time(),
            max_lifetime_seconds=60,
            tail="后台输出结果",
        )
        Path(done.stdout_path).write_text("后台输出结果", encoding="utf-8")
        svc._completed.append(done)

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = [
            [ModelEvent(type="token", content="first"), ModelEvent(type="done")],
            [ModelEvent(type="token", content="done."), ModelEvent(type="done")],
        ]
        tool_executor = MagicMock()
        executor = NativeAgentExecutor(
            model_client=mc,
            tool_executor=tool_executor,
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=3,
            system_prompt="test prompt",
            tools_schema=[],
            tool_registry=MagicMock(),
            background_task_service=svc,
        )
        ctx = RunContext(
            session_id="test-session",
            user_text="hello",
            output_dir=str(tmp_path / "out"),
            upload_dir=str(tmp_path / "up"),
        )
        executor.run(ctx, "hello")

        first_call_msgs = mc.stream_chat.call_args_list[0].kwargs["messages"]
        texts = [m.get("content", "") for m in first_call_msgs if m.get("role") == "user"]
        assert any("[后台任务完成]" in t and "后台输出结果" in t for t in texts), f"未注入后台通知: {texts}"

    def test_agent_cleanup_kills_session_tasks(self, tmp_path):
        """Agent.cleanup() kill 本会话存活后台任务。"""
        svc = BackgroundTaskService(base_dir=tmp_path)
        task = svc.start("sess-cleanup", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
        svc.kill_session("sess-cleanup")
        status = _wait_status(svc, task, {"killed", "failed", "completed"})
        assert status == "killed"
