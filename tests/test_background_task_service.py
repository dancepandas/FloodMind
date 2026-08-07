"""Tests for BackgroundTaskService — 后台任务（exec_bash run_in_background）。"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

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

    def test_concurrent_limit_guard(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path, max_concurrent_per_session=2)
        svc.start("s1", "a", _sleep_cmd(5), cwd=str(tmp_path))
        svc.start("s1", "b", _sleep_cmd(5), cwd=str(tmp_path))
        with pytest.raises(RuntimeError, match="上限"):
            svc.start("s1", "c", [sys.executable, "-c", "pass"], cwd=str(tmp_path))
        assert svc.kill_session("s1") == 2

    def test_kill_marks_killed(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        task = svc.start("s1", "sleep", _sleep_cmd(30), cwd=str(tmp_path))
        assert svc.kill("s1", task.task_id) is True
        status = _wait_status(svc, task, {"killed", "failed", "completed"})
        assert status == "killed"

    def test_kill_unknown_task_returns_false(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path)
        assert svc.kill("s1", "nope") is False

    def test_max_lifetime_kills(self, tmp_path):
        svc = BackgroundTaskService(base_dir=tmp_path, max_lifetime_seconds=1)
        task = svc.start("s1", "sleep", _sleep_cmd(30), cwd=str(tmp_path), max_lifetime_seconds=1)
        status = _wait_status(svc, task, {"killed", "failed", "completed"}, timeout=10)
        assert status == "killed"


class TestExecBashBackground:
    def test_run_in_background_returns_task_id(self, tmp_path):
        from floodmind.tools.base_tools import _impl_exec_bash
        from floodmind.tools.session_context import set_session_context

        state_dir = tmp_path / ".floodmind"
        out_dir = tmp_path / "out"
        set_session_context("s1", output_dir=str(out_dir), state_dir=str(state_dir))
        try:
            result = _impl_exec_bash(
                command=f"{sys.executable} -c 'import time; time.sleep(2)'",
                run_in_background=True,
            )
        finally:
            set_session_context("", output_dir="")
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
