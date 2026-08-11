"""P2 验收（设计 §25.1 对应项）：重放确定性、重复 event_id 不重复副作用、半写尾部可恢复、旧历史源无回退。"""

import json
from pathlib import Path

from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.journal_writer import JournalWriter
from floodmind.agent.runtime.reducer import initial_run_state, reduce
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope


def _is_valid_json_event(line: str) -> bool:
    """一行是否是可解析的完整 EventEnvelope（用于验证修复后无撕裂残片）。"""
    try:
        EventEnvelope.model_validate_json(line)
        return True
    except Exception:
        return False


def _scenario(auth):
    auth.emit("thread.message.sent", {"content": "q1", "turn_index": 0})
    auth.emit("model.attempt.completed", {"attempt_id": "a1", "terminal_reason": "tool_calls",
        "content": "", "reasoning": "", "tool_calls": [], "is_final": False, "usage": {}})
    auth.emit("tool.execution.started", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Read", "arguments": "{}"})
    auth.emit("tool.execution.completed", {"transaction_id": "ttx_1", "call_id": "c1",
        "tool_id": "builtin:Read", "status": "succeeded", "result_summary": "ok", "full_ref": "", "artifacts": []})
    auth.emit("model.attempt.completed", {"attempt_id": "a2", "terminal_reason": "completed",
        "content": "final", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})
    auth.emit("run.completed", {"final_output": "final", "terminal_reason": "completed"})


def test_end_to_end_replay_determinism(tmp_path):
    a1 = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                run_id="run_1", thread_id="th", turn_id="tu")
    _scenario(a1)
    a2 = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                run_id="run_1", thread_id="th", turn_id="tu")
    s1 = a1.replay(0)
    s2 = a2.replay(0)
    assert s1.model_dump() == s2.model_dump()


def test_duplicate_event_id_no_duplicate_side_effect(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    # 构造固定 event_id 的信封（绕过 new_envelope 的随机 id），模拟 retry 同 event_id 落盘。
    # 见 brief 注：真实 emit 每次生成新 id，重复副作用由两层防护——writer._sealed 幂等返回
    # + replay 按 event_id 去重。此处直接调 writer.append_many 验证 sealed 幂等。
    envelope = auth.new_envelope("model.attempt.completed", {
        "attempt_id": "a", "terminal_reason": "completed", "content": "x",
        "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }).model_copy(update={"event_id": "evt_fixed"})
    writer = auth._writer

    # 首次落盘：单行、sequence 1
    first = writer.append_many([envelope])
    assert [e.sequence for e in first] == [1]
    assert len(writer.read_from(0)) == 1

    # 同 event_id 整组重试：writer._sealed 幂等返回已封存信封，不重复追加
    retry = writer.append_many([envelope])
    assert retry == first
    assert writer.current_sequence() == 1  # 未新增行/sequence
    assert len(writer.read_from(0)) == 1

    # replay 按 event_id 去重：副作用只算一次
    state = auth.replay(0)
    assert state.token_usage["total_tokens"] == 10
    assert state.status.value == "completed"


def test_half_written_tail_recoverable(tmp_path):
    auth = open_journal_authority(tmp_path, conversation_id="c", task_id="t",
                                  run_id="run_1", thread_id="th", turn_id="tu")
    _scenario(auth)
    writer = auth._writer
    seg = sorted(writer._journal_dir.glob("events-*.jsonl"))[-1]

    # 写入撕裂尾并证明它确实在 segment 里——否则"修复后消失"的断言没有区分力。
    torn_marker = '"event_type": "partial'
    with seg.open("a", encoding="utf-8") as f:
        f.write('{"event_type": "partial')
    assert torn_marker in seg.read_text(encoding="utf-8")

    # repair_tail 必须实际截断撕裂行，而不是靠 read_from 读取时跳过（read_from 会
    # except 掉不可解析行）。T3 的 repair_tail 单测覆盖截断本身，这里验证的是
    # 半写尾部确实被物理移除。
    writer.repair_tail()
    after = seg.read_text(encoding="utf-8")
    assert torn_marker not in after
    assert all(
        not line.strip() or _is_valid_json_event(line.strip())
        for line in after.splitlines()
    )

    # 端到端恢复：重放得到场景自然终态 completed（不受半写尾部影响）
    assert auth.replay(0).status.value == "completed"

    # 修复后 Journal 仍可连续追加：sequence 连续、无 JournalWriteConflict
    auth.emit("model.attempt.completed", {"attempt_id": "a3", "terminal_reason": "completed",
        "content": "after", "reasoning": "", "tool_calls": [], "is_final": True,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
    assert writer.current_sequence() == 7
    assert len(writer.read_from(6)) == 1


def test_old_history_sources_offline():
    r"""审计：floodmind/ 源码中不再引用旧历史源与全局 getter。

    Ruling 1 调整：`_turns` 禁止模式从裸 `"_turns"` 收紧为词边界正则
    `self\._turns\b`。裸 `_turns` 是误报源——它命中既存合法标识符（scoped_turns、
    _current_turns/_conversation_turns/_build_turns_text 等、max_turns、
    _turns_to_frontend）与 stale 注释（run_state.py:63、memory/__init__.py:5），
    而计划验收标准（plan line 1676）要求下线的是旧 DualMemory `_turns` 属性
    （self._turns）。词边界 `\b` 使 `self._turns_to_frontend(...)`（方法调用，
    合法）不被命中，而 `self._turns`（属性读写）被命中。其余模式保持原样
    （子串匹配）。

    根目录取 parents[1]（仓库根）：brief 原稿的 parents[2] 在本仓库布局下会解析到
    仓库父目录（D:\chengs\9.project），误扫同级的另一个旧 floodmind 仓库。
    """
    import re
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]

    # 其余模式保持原样：子串匹配。
    banned = ("get_permission_service", "get_path_service", "get_background_task_service",
              "chat_history.json", "ExecutionJournalService", "record_turn")
    hits = []
    for pat in banned:
        scan = (
            "import pathlib;"
            f"root=pathlib.Path({str(root)!r});"
            f"pat={pat!r};"
            "hits=[str(p) for p in root.joinpath('floodmind').rglob('*.py')"
            " if pat in p.read_text(encoding='utf-8', errors='ignore')]"
            ";print('\\n'.join(hits))"
        )
        out = subprocess.run([sys.executable, "-c", scan],
                             capture_output=True, text=True)
        if out.stdout.strip():
            hits.append((pat, out.stdout.strip()))

    # `_turns`：词边界正则，仅命中旧属性 `self._turns`（属性读写）。
    turns_regex = r"self\._turns\b"
    scan = (
        "import pathlib,re;"
        f"root=pathlib.Path({str(root)!r});"
        f"rx=re.compile({turns_regex!r});"
        "hits=[str(p) for p in root.joinpath('floodmind').rglob('*.py')"
        " if rx.search(p.read_text(encoding='utf-8', errors='ignore'))]"
        ";print('\\n'.join(hits))"
    )
    out = subprocess.run([sys.executable, "-c", scan],
                         capture_output=True, text=True)
    if out.stdout.strip():
        hits.append(("_turns", out.stdout.strip()))

    assert not hits, f"legacy sources still referenced: {hits}"
