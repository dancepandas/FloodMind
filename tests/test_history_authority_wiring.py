from unittest.mock import MagicMock

from floodmind.agent.native.native_flood_agent import NativeFloodAgent


def test_enqueue_user_message_uses_agent_held_authority():
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent._journal_authority = MagicMock()

    assert agent.enqueue_user_message("queued") is True
    agent._journal_authority.emit.assert_called_once_with(
        "thread.message.sent", {"content": "queued", "turn_index": 0}
    )


def test_deactivate_run_clears_authority_and_disables_enqueue():
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    authority = MagicMock()
    agent._journal_authority = authority

    agent._deactivate_run_authority(authority)

    assert agent._journal_authority is None
    assert agent.enqueue_user_message("idle notice") is False
    authority.emit.assert_not_called()


def test_enqueue_user_message_reports_unbound_authority():
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent._journal_authority = None

    assert agent.enqueue_user_message("queued") is False
