"""Journal trace processor：canonical 事件的只读旁路消费。

对标 openai-agents 的 tracing processor 抽象：宿主可注册处理器把 journal
事件实时导出到 OTLP/自建面板。核心约束：
- processor 是只读旁路——事件照常落 journal，处理器故障不影响写入路径；
- 每条 committed envelope 恰好回调一次，带 canonical sequence；
- SDK Agent 构造参数 trace_processors 透传。
"""

import pytest

from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


class _Collector:
    def __init__(self, name="collector", fail=False):
        self.name = name
        self.events = []
        self.fail = fail

    def on_event(self, envelope: EventEnvelope) -> None:
        if self.fail:
            raise RuntimeError(f"{self.name} 故障")
        self.events.append((envelope.event_type, envelope.sequence, envelope.payload))


def _authority(tmp_path):
    return open_journal_authority(
        tmp_path,
        conversation_id="c", task_id="t", run_id="run_1",
        thread_id="thread_1", turn_id="turn_1",
    )


class TestTraceProcessor:
    def test_processor_receives_committed_events(self, tmp_path):
        """每条 committed envelope 恰好回调一次，带 canonical sequence。"""
        authority = _authority(tmp_path)
        collector = _Collector()
        authority.add_processor(collector)

        authority.emit("thread.message.sent", {"content": "hello"})
        authority.emit("run.completed", {"final_output": "done"})

        types = [t for t, _, _ in collector.events]
        assert types == ["thread.message.sent", "run.completed"]
        seqs = [s for _, s, _ in collector.events]
        assert seqs[0] < seqs[1], "sequence 单调"
        assert all(s > 0 for s in seqs), "envelope 携带真实 sequence"

    def test_processor_failure_does_not_break_write_path(self, tmp_path):
        """processor 异常隔离：事件照常落 journal，replay 不受影响。"""
        authority = _authority(tmp_path)
        bad = _Collector(name="bad", fail=True)
        good = _Collector(name="good")
        authority.add_processor(bad)
        authority.add_processor(good)

        env = authority.emit("thread.message.sent", {"content": "hello"})

        # 写入路径完好
        replayed = authority.replay()
        assert [t["content"] for t in replayed.turns if t.get("role") == "user"] == ["hello"]
        # 好的 processor 照常收到
        assert len(good.events) == 1

    def test_remove_processor(self, tmp_path):
        authority = _authority(tmp_path)
        collector = _Collector()
        authority.add_processor(collector)
        authority.emit("thread.message.sent", {"content": "a"})
        authority.remove_processor(collector)
        authority.emit("thread.message.sent", {"content": "b"})
        assert len(collector.events) == 1

    def test_events_landing_before_registration_not_replayed(self, tmp_path):
        """processor 只看注册后的事件（旁路订阅语义，不回放历史）。"""
        authority = _authority(tmp_path)
        authority.emit("thread.message.sent", {"content": "before"})
        collector = _Collector()
        authority.add_processor(collector)
        authority.emit("thread.message.sent", {"content": "after"})
        assert [p.get("content") for _, _, p in collector.events] == ["after"]

    def test_sdk_agent_accepts_trace_processors(self, tmp_path):
        """SDK Agent 构造参数接线：processor 收到 run 内 journal 事件。"""
        from unittest.mock import MagicMock
        from floodmind import Agent
        from floodmind.agent.native.model_client import ModelClient
        from floodmind.agent.guardrail import GuardrailResult

        llm = MagicMock(spec=ModelClient)
        llm.enable_thinking = False
        llm.model_name = "test-model"
        llm.classify_error.return_value = None

        def block(messages):
            return GuardrailResult(tripwire_triggered=True, message="sdk 拦截")
        block.__name__ = "block"

        collector = _Collector()
        from floodmind.agent.runtime.contracts.workspace import Workspace
        workspace = Workspace.from_folder(str(tmp_path), session_id="tp-sdk").ensure()
        agent = Agent(
            llm=llm, session_id="tp-sdk", workspace=workspace,
            input_guardrails=[block], trace_processors=[collector],
        )
        agent.run("hello")

        types = [t for t, _, _ in collector.events]
        assert "thread.message.sent" in types
        assert "safety.guardrail.triggered" in types
        assert "run.failed" in types
