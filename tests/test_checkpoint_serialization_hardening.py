"""Checkpoint 序列化加固：动态挂载字段持久化 + 不可序列化值降级。

对照 openai-agents run_state.py 的防御哲学：
- 运行时挂载在 state 上的动态字段（Pydantic extra=allow 不进 model_dump）
  必须显式进 checkpoint，否则 MiniMax 等厂商要求的 assistant 快照中断后丢失；
- _json_default 遇未知类型 fail-fast 会把整次 checkpoint 保存炸掉——
  改为可序列化部分保留、不可序列化部分显式降级标记。
"""

import json
import tempfile
from pathlib import Path

import pytest

from floodmind.agent.native.types import (
    AgentLoopState, ToolCall, ToolResult, TerminalReason,
)
from floodmind.agent.runtime.services.checkpoint_service import CheckpointService
from floodmind.agent.runtime.reducer import initial_run_state


def _svc():
    return CheckpointService(base_dir=str(Path(tempfile.mkdtemp())))


def _populated_state(**kw):
    s = AgentLoopState(session_id="cs-test", run_id="run_1", status="awaiting_tool", **kw)
    s.messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "answer", "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "t", "arguments": "{}"}}]},
    ]
    s.pending_tool_calls = [ToolCall(id="1", name="t", arguments={})]
    return s


class TestDynamicFieldPersistence:
    def test_round_assistant_message_survives_checkpoint(self):
        """assistant 快照（MiniMax 多轮回传必需）必须进 checkpoint。"""
        svc = _svc()
        s = _populated_state()
        s.round_assistant_message = {
            "role": "assistant", "content": "partial",
            "tool_calls": [{"id": "1", "type": "function",
                            "function": {"name": "t", "arguments": "{}"}}],
        }

        rs = initial_run_state("run_1")
        rec = svc.save(s, metadata={}, journal_cursor=0, run_state=rs)
        loaded = svc.load("cs-test", rec.checkpoint_id, state_class=AgentLoopState)

        assert loaded.round_assistant_message == s.round_assistant_message

    def test_pending_round_tool_records_survive_checkpoint(self):
        """跨 checkpoint 挂起的工具记录/已批准 ask 调用必须保留。"""
        svc = _svc()
        s = _populated_state()
        s._pending_round_tool_records = [{"tool_name": "Bash", "status": "completed"}]
        s._pending_completed_ask_calls = [ToolCall(id="9", name="Bash", arguments={})]

        rs = initial_run_state("run_1")
        rec = svc.save(s, metadata={}, journal_cursor=0, run_state=rs)
        loaded = svc.load("cs-test", rec.checkpoint_id, state_class=AgentLoopState)

        assert loaded._pending_round_tool_records == s._pending_round_tool_records
        # ToolCall 对象经 checkpoint 归一为 dict——断言数据等价而非类型等价
        loaded_calls = loaded._pending_completed_ask_calls
        assert [c["id"] for c in loaded_calls] == ["9"]
        assert [c["name"] for c in loaded_calls] == ["Bash"]


class TestJsonDefaultDegradation:
    def test_unserializable_value_degrades_not_raises(self):
        """_json_default 遇不可序列化对象降级为标记字符串，不炸整次保存。"""
        svc = _svc()
        s = _populated_state()
        # 模拟宿主工具把任意对象塞进消息（现实场景：自定义 attachments 等）
        s.messages.append({"role": "user", "content": "x", "_blob": object()})

        rs = initial_run_state("run_1")
        rec = svc.save(s, metadata={}, journal_cursor=0, run_state=rs)
        loaded = svc.load("cs-test", rec.checkpoint_id, state_class=AgentLoopState)

        blob = loaded.messages[-1].get("_blob")
        assert isinstance(blob, str) and "unserializable" in blob, \
            "不可序列化值应降级为标记，其余字段完整保留"
        assert loaded.messages[0]["content"] == "sys"

    def test_set_and_datetime_still_work(self):
        """降级路径不破坏既有 datetime 序列化。"""
        from datetime import datetime, timezone
        svc = _svc()
        s = _populated_state()
        s.messages.append({"role": "user", "content": "x", "ts": datetime.now(timezone.utc)})

        rs = initial_run_state("run_1")
        rec = svc.save(s, metadata={}, journal_cursor=0, run_state=rs)
        loaded = svc.load("cs-test", rec.checkpoint_id, state_class=AgentLoopState)

        assert "T" in loaded.messages[-1]["ts"] or "-" in loaded.messages[-1]["ts"]
