"""写端 fencing（G-01）：lease token 易主后旧 owner 不得再写 journal。"""

import json

import pytest

from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.journal_writer import JournalFencedError
from floodmind.agent.runtime.services.resume_service import ResumeService, open_lease
from floodmind.agent.runtime.services.runtime_layout import lease_file


def _auth(tmp_path, fence=None, run_id="run_1"):
    return open_journal_authority(
        tmp_path,
        conversation_id="conv",
        task_id="task",
        run_id=run_id,
        thread_id="thread",
        turn_id="turn",
        fence_check=fence,
    )


def _make_lease(tmp_path, owner="run", run_id="run_1"):
    return open_lease(
        tmp_path, run_id, owner=owner, conversation_id="conv", task_id="task"
    )


def _takeover(tmp_path, owner="resume", run_id="run_1"):
    """把现有 lease 置为过期后由新 owner 接管（模拟旧进程失联后 resume）。"""
    path = lease_file(tmp_path, "conv", "task", run_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["expires_at"] = 0
    path.write_text(json.dumps(data), encoding="utf-8")
    return _make_lease(tmp_path, owner=owner, run_id=run_id)


def test_append_rejected_after_lease_takeover(tmp_path):
    lease = _make_lease(tmp_path)
    auth = _auth(tmp_path, fence=lease.fence_check)
    auth.emit("thread.message.sent", {"content": "owner event"})
    assert auth.cursor() == 1

    new_lease = _takeover(tmp_path)
    with pytest.raises(JournalFencedError, match="易主"):
        auth.emit("thread.message.sent", {"content": "stale event"})

    new_auth = _auth(tmp_path, fence=new_lease.fence_check)
    new_auth.emit("resume.started", {})
    assert new_auth.cursor() == 2


def test_group_append_rejected_after_lease_takeover(tmp_path):
    lease = _make_lease(tmp_path)
    auth = _auth(tmp_path, fence=lease.fence_check)
    _takeover(tmp_path)
    events = [auth.new_envelope("thread.message.sent", {"content": "stale"})]
    with pytest.raises(JournalFencedError):
        auth.append_group(events)


def test_fence_check_corrupt_lease_fails_closed(tmp_path):
    lease = _make_lease(tmp_path)
    lease_file(tmp_path, "conv", "task", "run_1").write_text(
        "{not-json", encoding="utf-8"
    )
    with pytest.raises(JournalFencedError, match="损坏"):
        lease.fence_check()


def test_fence_check_missing_lease_fails_closed(tmp_path):
    lease = _make_lease(tmp_path)
    lease_file(tmp_path, "conv", "task", "run_1").unlink()
    with pytest.raises(JournalFencedError, match="不存在"):
        lease.fence_check()


def test_resume_authority_is_fenced_and_release_does_not_steal_takeover(tmp_path):
    outcome = ResumeService().resume(
        runtime_dir=tmp_path,
        conversation_id="conv",
        task_id="task",
        run_id="run_1",
        thread_id="thread",
        turn_id="turn",
    )
    new_lease = _takeover(tmp_path)

    with pytest.raises(JournalFencedError):
        outcome.authority.emit("thread.message.sent", {"content": "late"})

    # 旧 owner 迟到的 release 不得删除新 owner 的 lease（token 校验）
    outcome.lease.release()
    path = lease_file(tmp_path, "conv", "task", "run_1")
    assert path.exists()
    new_lease.fence_check()  # 新 owner 仍可写入
