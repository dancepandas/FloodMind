"""Tests for background review system."""

from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.background_review import (
    BackgroundReviewer,
    ReviewResult,
)


class TestBackgroundReviewer:
    """Test BackgroundReviewer parsing and application."""

    def test_review_disabled_returns_none(self):
        """Disabled reviewer returns None."""
        llm = MagicMock()
        reviewer = BackgroundReviewer(llm, enabled=False)
        result = reviewer.review_session("s1", [{"role": "user", "content": "hi"}])
        assert result is None

    def test_review_few_messages_returns_none(self):
        """Too few messages → skip review."""
        llm = MagicMock()
        reviewer = BackgroundReviewer(llm, min_message_count=5)
        result = reviewer.review_session("s1", [{"role": "user", "content": "hi"}])
        assert result is None

    def test_review_short_conversation_returns_none(self):
        """Very short text → skip review."""
        llm = MagicMock()
        reviewer = BackgroundReviewer(llm)
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
        ]
        result = reviewer.review_session("s1", messages)
        assert result is None

    def test_parse_valid_json(self):
        """Valid JSON response is parsed into ReviewResult."""
        raw = (
            '{"user_preferences": [{"content": "prefer concise", "type": "preference"}], '
            '"experience": null, "skill_suggestions": []}'
        )
        result = BackgroundReviewer._parse_result(raw)
        assert result is not None
        assert len(result.user_preferences) == 1
        assert result.user_preferences[0]["content"] == "prefer concise"

    def test_parse_json_with_code_block(self):
        """JSON wrapped in markdown code block is handled."""
        raw = '```json\n{"user_preferences": [], "experience": null, "skill_suggestions": []}\n```'
        result = BackgroundReviewer._parse_result(raw)
        assert result is not None
        assert result.user_preferences == []

    def test_parse_empty_returns_none(self):
        """Empty JSON returns None."""
        assert BackgroundReviewer._parse_result("{}") is None
        assert BackgroundReviewer._parse_result("") is None

    def test_parse_invalid_json_returns_none(self):
        """Invalid JSON is gracefully handled."""
        assert BackgroundReviewer._parse_result("not json") is None

    def test_format_messages(self):
        """Message formatting handles roles and multimodal."""
        messages = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "tool", "content": "result"},
            {"role": "user", "content": [{"type": "text", "text": "multi"}]},
        ]
        text = BackgroundReviewer._format_messages(messages)
        assert "[用户]" in text
        assert "[助手]" in text
        assert "[工具结果]" in text
        assert "multi" in text

    def test_apply_preferences_to_memory(self):
        """User preferences are written to memory."""
        llm = MagicMock()
        reviewer = BackgroundReviewer(llm)
        result = ReviewResult(
            user_preferences=[{"content": "use celsius", "type": "preference"}],
            experience=None,
            skill_suggestions=[],
        )
        memory = MagicMock()
        memory.add_long_term_memory.return_value = True
        stats = reviewer.apply_review_result("s1", result, memory_instance=memory)
        assert stats["preferences"] == 1
        memory.add_long_term_memory.assert_called_once_with("use celsius", "preference")

    def test_apply_experience_to_tree(self):
        """Experience is added to experience tree."""
        llm = MagicMock()
        reviewer = BackgroundReviewer(llm)
        result = ReviewResult(
            user_preferences=[],
            experience={
                "tree_path": ["水文", "预报"],
                "task_description": "run model",
                "domain_keywords": ["flood"],
                "skill_used": "aojiang-hydro",
                "pitfalls": ["p1"],
                "solutions": ["s1"],
                "importance": 0.8,
            },
            skill_suggestions=[],
        )
        tree = MagicMock()
        stats = reviewer.apply_review_result("s1", result, experience_tree=tree)
        assert stats["experiences"] == 1
        tree.add_leaf.assert_called_once()

    def test_apply_skills_queued(self):
        """Skill suggestions are queued to disk."""
        llm = MagicMock()
        reviewer = BackgroundReviewer(llm)
        result = ReviewResult(
            user_preferences=[],
            experience=None,
            skill_suggestions=[{"skill_name": "test-skill", "suggestion": "fix X", "reason": "bug"}],
        )
        import tempfile, os, json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            # Patch the internal Path reference by monkey-patching _queue_skill_suggestions
            orig_queue = reviewer._queue_skill_suggestions
            queue_file = Path(tmp) / "s1.json"
            def mock_queue(sid, suggestions):
                queue_file.write_text(json.dumps(suggestions, ensure_ascii=False), encoding="utf-8")
            reviewer._queue_skill_suggestions = staticmethod(mock_queue)
            stats = reviewer.apply_review_result("s1", result)
            assert stats["suggestions"] == 1
            assert queue_file.exists()

    def test_apply_no_op_on_empty_result(self):
        """Empty result produces no side effects."""
        llm = MagicMock()
        reviewer = BackgroundReviewer(llm)
        result = ReviewResult(user_preferences=[], experience=None, skill_suggestions=[])
        stats = reviewer.apply_review_result("s1", result)
        assert stats == {"preferences": 0, "experiences": 0, "suggestions": 0}


from unittest.mock import patch
