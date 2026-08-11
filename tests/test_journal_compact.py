"""Journal-backed Compact tests (P5 Task 3, §9.5/§9.6)."""

from floodmind.agent.native.context_compressor import ContextCompressor
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def test_compact_emits_summary_event_and_keeps_user_message(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "long user message that must never be truncated", "turn_index": 0})
    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "long user message that must never be truncated"},
        {"role": "assistant", "content": "a" * 2000},
        {"role": "assistant", "content": "b" * 2000},
    ]
    cc = ContextCompressor()
    result = cc.compress_journal(messages, auth, capabilities=None,
                                 budget=None, max_context_tokens=1200)
    # 当前用户请求不静默截断
    assert any("long user message that must never be truncated" in m.get("content", "")
               for m in result.compressed_messages if m.get("role") == "user")
    # Summary Event 落 journal
    types = [e.event_type for e in auth.read_after(0)]
    assert "context.compaction.completed" in types
    # 原始 journal 不变（只新增 summary 事件，不修改原事件）
    first = auth.read_after(0)[0]
    assert first.event_type == "thread.message.sent" and first.payload["content"] == "long user message that must never be truncated"
