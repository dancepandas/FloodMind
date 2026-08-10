from unittest.mock import MagicMock

from floodmind.agent.native.native_flood_agent import NativeFloodAgent


def test_enqueue_user_message_uses_agent_held_authority():
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent._journal_authority = MagicMock()

    assert agent.enqueue_user_message("queued") is True
    agent._journal_authority.emit.assert_called_once_with(
        "thread.message.sent", {"content": "queued", "turn_index": 0}
    )


def test_enqueue_user_message_reports_unbound_authority():
    agent = NativeFloodAgent.__new__(NativeFloodAgent)
    agent._journal_authority = None

    assert agent.enqueue_user_message("queued") is False
