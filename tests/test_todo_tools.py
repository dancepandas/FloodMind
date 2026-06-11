"""Tests for Todo task management tools.

测试内容:
- TodoWrite 全量写入/替换
- TodoList 空列表与已有列表
- 任务状态规范化 (pending/in_progress/completed/cancelled)
- 优先级规范化 (high/normal/low)
- 防御性输入处理 (JSON 字符串、非列表、非法状态)
- 事件发射
"""

import json

import pytest

from floodmind.tools.todo_tools import (
    _impl_todo_write,
    _impl_todo_list,
    _normalize_todo,
    _todo_store,
    set_todo_event_bus,
)


class TestTodoNormalize:
    """Test internal _normalize_todo helper."""

    def test_defaults(self):
        t = _normalize_todo({"content": "hello"})
        assert t["content"] == "hello"
        assert t["status"] == "pending"
        assert t["priority"] == "normal"
        assert t["id"].startswith("todo_")

    def test_status_normalization(self):
        assert _normalize_todo({"status": "COMPLETED"})["status"] == "completed"
        assert _normalize_todo({"status": "In_Progress"})["status"] == "in_progress"
        assert _normalize_todo({"status": "bogus"})["status"] == "pending"

    def test_priority_normalization(self):
        assert _normalize_todo({"priority": "HIGH"})["priority"] == "high"
        assert _normalize_todo({"priority": "Low"})["priority"] == "low"
        assert _normalize_todo({"priority": "bogus"})["priority"] == "normal"

    def test_id_preserved(self):
        t = _normalize_todo({"id": "my-id", "content": "x"})
        assert t["id"] == "my-id"


class TestTodoWrite:
    """Test TodoWrite tool logic."""

    def setup_method(self):
        # 清理 todo store
        _todo_store.clear()
        set_todo_event_bus(None)

    def test_write_replaces_all(self):
        _impl_todo_write([{"id": "1", "content": "task1"}])
        _impl_todo_write([{"id": "2", "content": "task2"}])
        todos = _todo_store.get("default", [])
        assert len(todos) == 1
        assert todos[0]["id"] == "2"

    def test_write_multiple_items(self):
        result = _impl_todo_write([
            {"id": "a", "content": "buy milk", "status": "pending"},
            {"id": "b", "content": "write code", "status": "in_progress"},
        ])
        assert "buy milk" in result
        assert "write code" in result
        todos = _todo_store.get("default", [])
        assert len(todos) == 2

    def test_write_empty_clears(self):
        _impl_todo_write([{"id": "1", "content": "task1"}])
        _impl_todo_write([])
        assert _todo_store.get("default", []) == []

    def test_write_json_string_input(self):
        payload = json.dumps([{"id": "1", "content": "from json"}])
        result = _impl_todo_write(payload)
        assert "from json" in result

    def test_write_invalid_json_string(self):
        result = _impl_todo_write("not json")
        assert "错误" in result

    def test_write_non_list_input(self):
        result = _impl_todo_write({"id": "1"})
        assert "错误" in result

    def test_write_emits_event(self):
        events = []

        class FakeBus:
            def emit_todo_updated(self, todos):
                events.append(todos)

        set_todo_event_bus(FakeBus())
        _impl_todo_write([{"id": "1", "content": "emit me"}])
        assert len(events) == 1
        assert events[0][0]["content"] == "emit me"


class TestTodoList:
    """Test TodoList tool logic."""

    def setup_method(self):
        _todo_store.clear()

    def test_empty_list(self):
        result = _impl_todo_list()
        assert "没有待办任务" in result

    def test_list_returns_items(self):
        _todo_store["default"] = [
            {"id": "1", "content": "task A", "status": "completed", "priority": "high"},
        ]
        result = _impl_todo_list()
        assert "task A" in result
        assert "✅" in result

    def test_list_icons(self):
        _todo_store["default"] = [
            {"id": "1", "content": "pending", "status": "pending", "priority": "normal"},
            {"id": "2", "content": "in_progress", "status": "in_progress", "priority": "normal"},
            {"id": "3", "content": "completed", "status": "completed", "priority": "normal"},
            {"id": "4", "content": "cancelled", "status": "cancelled", "priority": "normal"},
        ]
        result = _impl_todo_list()
        assert "⬜" in result
        assert "🔄" in result
        assert "✅" in result
        assert "❌" in result
