"""FloodMind TUI — FloodMindClient 单元测试"""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestFloodMindClientInit:
    def test_default_session_id_format(self):
        from floodmind.tui.web_client import FloodMindClient
        c = FloodMindClient()
        assert c.session_id.startswith("tui-")
        assert len(c.session_id) == 12  # "tui-" + 8 hex
        c.close()

    def test_custom_base_url(self):
        from floodmind.tui.web_client import FloodMindClient
        c = FloodMindClient(base_url="http://example.com:9999/")
        assert c.base_url == "http://example.com:9999"
        c.close()


class TestHealthCheck:
    def test_server_down(self):
        from floodmind.tui.web_client import FloodMindClient
        c = FloodMindClient(base_url="http://localhost:1")
        assert c.health_check(timeout=0.1) is False
        c.close()

    def test_server_up_healthy(self):
        from floodmind.tui.web_client import FloodMindClient
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "healthy"}
        c = FloodMindClient()
        with patch.object(c.client, "get", return_value=resp):
            assert c.health_check() is True
        c.close()


class TestInitSession:
    def test_init_success(self):
        from floodmind.tui.web_client import FloodMindClient
        c = FloodMindClient()
        with patch.object(c.client, "post") as mock_post:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"model_name": "deepseek-v4", "status": "success"}
            mock_post.return_value = resp
            result = c.init_session(model_key="deepseek-v4-flash")
            assert result is True
            assert c.model_name == "deepseek-v4"
            assert c._init_ok is True
            mock_post.assert_called_once()
            payload = mock_post.call_args.kwargs["json"]
            assert payload["session_id"].startswith("tui-")
        c.close()

    def test_init_500(self):
        from floodmind.tui.web_client import FloodMindClient
        c = FloodMindClient()
        with patch.object(c.client, "post") as mock_post:
            resp = MagicMock()
            resp.status_code = 500
            mock_post.return_value = resp
            result = c.init_session()
            assert result is False
        c.close()


class TestStreamChat:
    def test_stream_parses_ndjson(self):
        from floodmind.tui.web_client import FloodMindClient
        c = FloodMindClient()
        c._init_ok = True

        lines = [
            json.dumps({"type": "thought_delta", "content": "想"}) + "\n",
            json.dumps({"type": "thought_delta", "content": "考"}) + "\n",
            json.dumps({"type": "answer_delta", "content": "回"}) + "\n",
            json.dumps({"type": "answer_delta", "content": "答"}) + "\n",
            json.dumps({"type": "stream_end"}) + "\n",
        ]

        class FakeResp:
            def __init__(self):
                self.status_code = 200
            def iter_lines(self):
                yield from lines
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with patch.object(c.client, "stream", return_value=FakeResp()):
            events = list(c.stream_chat("你好"))
            assert len(events) == 5
            assert events[0] == {"type": "thought_delta", "content": "想"}
            assert events[4] == {"type": "stream_end"}
        c.close()

    def test_stream_not_init_returns_error(self):
        from floodmind.tui.web_client import FloodMindClient
        c = FloodMindClient()
        # 不在这里 init；stream_chat 会尝试 init_session
        with patch.object(c, "init_session", return_value=False):
            events = list(c.stream_chat("你好"))
            assert len(events) == 1
            assert events[0]["type"] == "error"
            assert "初始化失败" in events[0]["content"]
        c.close()


class TestListSessions:
    def test_returns_list(self):
        from floodmind.tui.web_client import FloodMindClient
        c = FloodMindClient()
        with patch.object(c.client, "get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "status": "success",
                "sessions": [
                    {"session_id": "s1", "title": "test", "message_count": 5},
                    {"session_id": "s2", "title": "test2", "message_count": 2},
                ],
            }
            mock_get.return_value = resp
            sessions = c.list_sessions()
            assert len(sessions) == 2
            assert sessions[0]["session_id"] == "s1"
        c.close()


class TestClearMemory:
    def test_clear_success(self):
        from floodmind.tui.web_client import FloodMindClient
        c = FloodMindClient()
        with patch.object(c.client, "post") as mock_post:
            resp = MagicMock()
            resp.status_code = 200
            mock_post.return_value = resp
            assert c.clear_memory() is True
            payload = mock_post.call_args.kwargs["json"]
            assert payload["session_id"].startswith("tui-")
        c.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
