"""P5 验收（设计 §25.5 Compact 对应项）。"""

import hashlib

from floodmind.agent.native.context_compressor import ContextCompressor
from floodmind.agent.native.atomic_groups import AtomicGroups
from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.contracts.canonical_events import canonical_json


def test_compact_does_not_split_tool_call_result_atomic_group(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "Read", "arguments": {"p": 1}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file content " + "x" * 5000},
        {"role": "assistant", "content": "done " * 500},
    ]
    cc = ContextCompressor()
    result = cc.compress_journal(messages, auth, max_context_tokens=8000)
    compressed = result.compressed_messages
    # 不拆 Tool Call + Result：要么整组保留，要么整组被摘要替换，绝不只留一半
    tc = [m for m in compressed if m.get("tool_calls")]
    tr = [m for m in compressed if m.get("role") == "tool"]
    assert len(tc) == len(tr)  # 工具组完整


def test_compact_replay_block_not_lost(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    auth.emit("thread.message.sent", {"content": "user one", "turn_index": 0})
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "completed",
        "content": "assistant one", "reasoning": "think", "tool_calls": [], "is_final": True, "usage": {}})
    auth.emit("thread.message.sent", {"content": "user two", "turn_index": 1})
    auth.emit("model.attempt.completed", {"attempt_id": "a2", "terminal_reason": "completed",
        "content": "assistant two", "reasoning": "think", "tool_calls": [], "is_final": True, "usage": {}})
    auth.emit("thread.message.sent", {"content": "user three", "turn_index": 2})
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "user one"},
        {"role": "assistant", "content": "assistant one", "reasoning": "think"},
        {"role": "user", "content": "user two"},
        {"role": "assistant", "content": "assistant two", "reasoning": "think"},
        {"role": "user", "content": "user three"},
    ]
    before = auth.replay().turns
    cc = ContextCompressor(head_keep=1, tail_keep=2)
    cc.compress_journal(messages, auth, max_context_tokens=32000)
    after = auth.replay().turns
    assert before == after  # 原始 journal 不变（compact 只 append summary，不改原事件）
    assert any(e.event_type == "context.compaction.completed" for e in auth.read_after(0))


def test_compact_never_truncates_current_user_message(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    current = "当前用户请求绝不能静默截断 " * 200
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": current}]
    cc = ContextCompressor()
    result = cc.compress_journal(messages, auth, max_context_tokens=32000)
    assert any(m.get("role") == "user" and m.get("content") == current for m in result.compressed_messages)
