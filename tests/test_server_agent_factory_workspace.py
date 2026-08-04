"""Tests for Web/session workspace binding in agent_factory."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("flask", reason="legacy Web adapter requires optional floodmind[web] extra")


def test_create_agent_for_session_binds_web_workspace(tmp_path):
    from floodmind.server.agent_factory import create_agent_for_session

    session_manager = MagicMock()
    session_manager.sessions_dir = tmp_path / "sessions"
    session_manager.get_memory_dir.return_value = tmp_path / "sessions" / "s1" / "memory"

    with (
        patch("floodmind.server.agent_factory.clear_session_token_usage"),
        patch("floodmind.server.agent_factory.ModelClient.from_settings", return_value=MagicMock()),
        patch("floodmind.config.model_resolver.resolve_model") as mock_resolve_model,
        patch("floodmind.server.agent_factory.DualMemory", return_value=MagicMock()),
        patch("floodmind.server.agent_factory.create_flood_agent", return_value=MagicMock()) as mock_create_agent,
    ):
        mock_resolve_model.return_value.context_window = 8192
        agent = create_agent_for_session("s1", session_manager)

    assert agent is mock_create_agent.return_value
    kwargs = mock_create_agent.call_args.kwargs
    ws = kwargs["workspace"]
    assert ws.mode == "web_session"
    assert ws.user_dir == (tmp_path / "sessions" / "s1" / "outputs").resolve()
    assert kwargs["session_id"] == "s1"
