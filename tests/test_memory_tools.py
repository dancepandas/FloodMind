"""Tests for memory tools."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import floodmind.memory.core_memory
from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.tools.base_tools import set_memory_instance
from floodmind.tools.memory_tools import (
    conversation_search,
    core_memory_append,
    core_memory_read,
    journal_get_full_result,
    journal_search,
    _get_session_id,
)
from floodmind.tools.session_context import set_session_context


class TestMemoryTools:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        set_session_context("test-session", output_dir=self.tmp)
        # CoreMemoryAppend/Read 已重定向到项目级 CoreMemoryStore（门面单例）：
        # 重置门面单例并把默认存储文件重定向到临时目录，避免测试污染真实数据。
        from floodmind.memory import services as _svc_mod
        self._saved_default = floodmind.memory.core_memory._DEFAULT_STORE_FILE
        floodmind.memory.core_memory._DEFAULT_STORE_FILE = str(Path(self.tmp) / "core.json")
        self._saved_instance = _svc_mod._instance
        _svc_mod._instance = None
        # 关闭旧会话文件聚合迁移：测试不依赖磁盘上的历史会话文件，避免环境泄漏进断言
        from floodmind.config.settings import settings
        self._saved_migrate = settings.core_memory.migrate_legacy
        settings.core_memory.migrate_legacy = False

    def teardown_method(self):
        # 还原门面单例与默认存储文件，避免跨测试泄漏
        from floodmind.memory import services as _svc_mod
        from floodmind.config.settings import settings
        settings.core_memory.migrate_legacy = self._saved_migrate
        _svc_mod._instance = self._saved_instance
        floodmind.memory.core_memory._DEFAULT_STORE_FILE = self._saved_default
        set_session_context("", output_dir="")

    def test_core_memory_append_and_read(self):
        result = core_memory_append.func(category="user_preferences", fact="喜欢先出计划")
        assert "已记录" in result

        result = core_memory_read.func()
        assert "喜欢先出计划" in result
        assert "user_preferences" in result

        # 重复追加不应重复
        result = core_memory_append.func(category="user_preferences", fact="喜欢先出计划")
        assert "已存在" in result

        result = core_memory_read.func(category="user_preferences")
        # 只应有一条
        assert result.count("喜欢先出计划") == 1

    def test_core_memory_read_category_empty(self):
        result = core_memory_read.func(category="nonexistent")
        assert "没有记录" in result

    def test_conversation_search(self):
        memory = MagicMock()
        memory.search_history.return_value = "第1轮: 用户问了一个问题\n回答: 这是答案"
        set_memory_instance(memory)

        result = conversation_search.func(query="问题")
        assert "第1轮" in result
        memory.search_history.assert_called_once_with("问题", 3)

    def test_conversation_search_no_memory(self):
        set_memory_instance(None)
        result = conversation_search.func(query="问题")
        assert "记忆系统未初始化" in result

    def test_journal_search_and_get_full_result(self):
        runtime_dir = Path(self.tmp) / ".floodmind"
        auth = open_journal_authority(
            runtime_dir,
            conversation_id="conversation-1",
            task_id="task-1",
            run_id="run-1",
            thread_id="thread-1",
            turn_id="turn-1",
        )
        long_content = "word " * 50
        auth.emit(
            "tool.execution.completed",
            {
                "transaction_id": "ttx_1",
                "call_id": "tc1",
                "tool_id": "Read",
                "status": "succeeded",
                "result_summary": long_content,
                "full_ref": "ref-1",
                "artifacts": [],
            },
        )
        runtime_context = RuntimeContext(
            conversation_id="conversation-1",
            task_id="task-1",
            run_id="run-1",
            thread_id="thread-1",
            turn_id="turn-1",
            journal_authority=auth,
        )
        set_session_context("test-session", output_dir=self.tmp, runtime_context=runtime_context)

        result = journal_search.func(query="word")
        assert "Tool Read" in result
        assert "ref-1" in result

        result = journal_get_full_result.func(ref_id="ref-1")
        assert "完整工具结果" in result
        assert long_content in result

    def test_journal_get_full_result_not_found(self):
        result = journal_get_full_result.func(ref_id="not-exist")
        assert "未找到归档结果" in result

    def test_session_id_helper(self):
        assert _get_session_id() == "test-session"
