"""带附件消息在 journal 回放投影下必须保留图片内容块。

数据流：stream() 先把纯文本 emit 进 journal（thread.message.sent），
再构建含 image_url 结构化块的初始消息；run_from_state 回放 journal 后
project_run_state_to_loop_state 会用纯文本重建消息列表。若投影不保留
传入 state 中的本轮结构化 user 消息，模型就看不到图。

产品决策：图片只在本次 run 的第一次 LLM 调用可见；后续迭代/后续轮次
由 journal/memory 的纯文本语义自然退化，不重复发图。
"""

import copy
from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.executor import NativeAgentExecutor, project_run_state_to_loop_state
from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.types import AgentLoopState, ModelEvent, RunContext, ToolCall
from floodmind.agent.runtime.reducer import initial_run_state, reduce
from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope


STRUCTURED_USER = [{
    "type": "text",
    "text": "这张图里是什么？",
}, {
    "type": "image_url",
    "image_url": {"url": "data:image/png;base64,AAAA"},
}]


def _capturing_model_client(responses):
    """stream_chat 按序返回 responses，并深拷贝快照每次调用时刻的 messages。

    executor 会在迭代间原地修改消息 dict，断言必须基于调用时刻快照，
    不能读 call_args（那是 live list 引用，会被后续 mutation 污染）。
    返回 (mock, captured)。
    """
    captured: list = []

    def side_effect(*args, **kwargs):
        captured.append(copy.deepcopy(kwargs["messages"]))
        return responses.pop(0)

    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.side_effect = side_effect
    return mc, captured


def _last_user_content(messages):
    return [m for m in messages if m.get("role") == "user"][-1]["content"]


def _executor_with_journal(authority):
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [
        ModelEvent(type="token", content="好的"),
        ModelEvent(type="done"),
    ]
    executor = NativeAgentExecutor(
        model_client=mc,
        tool_executor=MagicMock(),
        event_bus=EventBus(),
        message_builder=MessageBuilder(),
        max_iterations=5,
        system_prompt="sys",
        tools_schema=[],
    )
    executor._journal_authority = authority
    return executor


def _context():
    return RunContext(
        session_id="s",
        user_text="这张图里是什么？",
        output_dir="/tmp/o",
        upload_dir="/tmp/u",
    )


class TestProjectionKeepsTurnStructuredMessage:
    def test_projected_state_keeps_structured_user_message(self):
        """journal 回放后，本轮结构化 user 消息不被纯文本重建覆盖。"""
        # journal 已有一轮历史 + 本轮纯文本 user 消息
        run_state = initial_run_state("run-1")
        run_state = reduce(run_state, EventEnvelope(
            event_id="e1", sequence=1, event_type="thread.message.sent",
            payload={"content": "历史问题"}, thread_id="",
        ))
        run_state = reduce(run_state, EventEnvelope(
            event_id="e2", sequence=2, event_type="thread.message.sent",
            payload={"content": "这张图里是什么？"}, thread_id="",
        ))

        # 传入 state：消息已构建好，最后一条 user 是结构化内容块
        state = AgentLoopState(
            session_id="s",
            run_id="run-1",
            status="created",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "历史问题"},
                {"role": "assistant", "content": "历史回答"},
                {"role": "user", "content": STRUCTURED_USER},
            ],
        )

        projected = project_run_state_to_loop_state(state, run_state)
        last_user = [m for m in projected.messages if m.get("role") == "user"][-1]
        assert last_user["content"] == STRUCTURED_USER

    def test_run_from_state_first_llm_call_sees_image(self):
        """端到端：journal 回放路径下，第一次 LLM 调用收到结构化 user 消息。"""
        run_state = initial_run_state("run-1")
        run_state = reduce(run_state, EventEnvelope(
            event_id="e1", sequence=1, event_type="thread.message.sent",
            payload={"content": "这张图里是什么？"}, thread_id="",
        ))
        authority = MagicMock()
        authority.replay.return_value = run_state

        mc, captured = _capturing_model_client([
            [ModelEvent(type="token", content="好的"), ModelEvent(type="done")],
        ])
        executor = NativeAgentExecutor(
            model_client=mc,
            tool_executor=MagicMock(),
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=5,
            system_prompt="sys",
            tools_schema=[],
        )
        executor._journal_authority = authority

        state = AgentLoopState(
            session_id="s",
            run_id="run-1",
            status="created",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": STRUCTURED_USER},
            ],
        )
        executor.run_from_state(_context(), state)

        assert len(captured) == 1
        assert _last_user_content(captured[0]) == STRUCTURED_USER

    def test_plain_text_state_unchanged(self):
        """无附件（最后一条 user 为纯文本）时投影行为完全不变。"""
        run_state = initial_run_state("run-1")
        run_state = reduce(run_state, EventEnvelope(
            event_id="e1", sequence=1, event_type="thread.message.sent",
            payload={"content": "hi"}, thread_id="",
        ))
        state = AgentLoopState(
            session_id="s",
            run_id="run-1",
            status="created",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ],
        )
        projected = project_run_state_to_loop_state(state, run_state)
        last_user = [m for m in projected.messages if m.get("role") == "user"][-1]
        assert last_user["content"] == "hi"


class TestImageSentOncePerRun:
    @staticmethod
    def _history_run_state():
        """会话已有历史轮的 journal 状态：user → assistant → 本轮 user。

        投影后 iteration = 1（历史 assistant 条数），复刻"会话第二轮对话"。
        """
        run_state = initial_run_state("run-2")
        run_state.turns = [
            {"role": "user", "content": "历史问题", "thread_id": ""},
            {"role": "assistant", "content": "历史回答", "thread_id": ""},
            {"role": "user", "content": "这张图里是什么？", "thread_id": ""},
        ]
        run_state.last_committed_sequence = 3
        return run_state

    def test_second_turn_image_visible_on_first_call(self):
        """会话第二轮（投影 iteration>0）带新图：本次 run 首次调用必须看到图。

        剥离语义按 run 计数，不能按投影来的会话级 iteration 计数——
        否则第二轮对话用户新发的图会在首次调用前就被剥掉。
        """
        authority = MagicMock()
        authority.replay.return_value = self._history_run_state()
        mc, captured = _capturing_model_client([
            [ModelEvent(type="token", content="好的"), ModelEvent(type="done")],
        ])
        executor = NativeAgentExecutor(
            model_client=mc,
            tool_executor=MagicMock(),
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=5,
            system_prompt="sys",
            tools_schema=[],
        )
        executor._journal_authority = authority
        state = AgentLoopState(
            session_id="s",
            run_id="run-2",
            status="created",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "历史问题"},
                {"role": "assistant", "content": "历史回答"},
                {"role": "user", "content": STRUCTURED_USER},
            ],
        )
        executor.run_from_state(_context(), state)

        assert len(captured) == 1
        assert _last_user_content(captured[0]) == STRUCTURED_USER

    def test_checkpoint_snapshots_contain_no_image(self):
        """检查点快照必须是剥图后的纯文本，resume 不会把旧图带回。"""
        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult

        def make_stream(*args, **kwargs):
            if not make_stream.called:
                make_stream.called = True
                return [
                    ModelEvent(type="tool_call_done",
                               tool_call=ToolCall(id="t1", name="test_tool", arguments={"k": 1})),
                    ModelEvent(type="done"),
                ]
            return [
                ModelEvent(type="token", content="完成"),
                ModelEvent(type="done"),
            ]
        make_stream.called = False

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = make_stream
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="test_tool", content="ok", status="completed",
        )
        reg = MagicMock()
        reg.get.return_value = None
        reg.all.return_value = []
        reg.tools_schema.return_value = [{"type": "function", "function": {"name": "test_tool"}}]
        saved: list = []
        checkpoint_service = MagicMock()
        checkpoint_service.save.side_effect = \
            lambda s, **kw: saved.append(copy.deepcopy(s.messages)) or MagicMock()

        executor = NativeAgentExecutor(
            model_client=mc,
            tool_executor=tool_executor,
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=5,
            system_prompt="sys",
            tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
            tool_registry=reg,
            checkpoint_service=checkpoint_service,
        )
        executor._journal_authority = MagicMock()
        executor._journal_authority.replay.return_value = self._history_run_state()
        executor._journal_authority.checkpoint_snapshot.return_value = (
            3, self._history_run_state(),
        )

        state = AgentLoopState(
            session_id="s",
            run_id="run-2",
            status="created",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": STRUCTURED_USER},
            ],
        )
        executor.run_from_state(_context(), state)

        assert saved, "至少应有一次检查点保存"
        for messages in saved:
            for m in messages:
                if m.get("role") == "user":
                    assert not isinstance(m["content"], list), "检查点不得携带结构化图片消息"

    def test_second_llm_call_within_run_strips_image(self):
        """同一次 run 内：第一次 LLM 调用看到图，工具调用后的第二次调用不再重发。"""
        from floodmind.agent.runtime.contracts.tools import ToolResult as NativeToolResult

        calls: list = []

        def make_stream(*args, **kwargs):
            # 深拷贝快照：executor 会在迭代间原地修改消息 dict，浅拷贝会串改历史快照
            calls.append(copy.deepcopy(kwargs["messages"]))
            if len(calls) == 1:
                return [
                    ModelEvent(type="tool_call_done",
                               tool_call=ToolCall(id="t1", name="test_tool", arguments={"k": 1})),
                    ModelEvent(type="done"),
                ]
            return [
                ModelEvent(type="token", content="完成"),
                ModelEvent(type="done"),
            ]

        mc = MagicMock(spec=ModelClient)
        mc.stream_chat.side_effect = make_stream
        tool_executor = MagicMock()
        tool_executor.execute.return_value = NativeToolResult(
            tool_call_id="t1", name="test_tool", content="ok", status="completed",
        )
        from floodmind.agent.runtime.contracts.tools import ToolSpec
        reg = MagicMock()
        reg.get.return_value = None
        reg.all.return_value = []
        reg.tools_schema.return_value = [{"type": "function", "function": {"name": "test_tool"}}]

        executor = NativeAgentExecutor(
            model_client=mc,
            tool_executor=tool_executor,
            event_bus=EventBus(),
            message_builder=MessageBuilder(),
            max_iterations=5,
            system_prompt="sys",
            tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
            tool_registry=reg,
        )

        state = AgentLoopState(
            session_id="s",
            run_id="run-1",
            status="created",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": STRUCTURED_USER},
            ],
        )
        result = executor.run_from_state(_context(), state)
        assert "完成" in result.final_output
        assert len(calls) == 2

        first_user = [m for m in calls[0] if m.get("role") == "user"][-1]
        assert first_user["content"] == STRUCTURED_USER

        second_user = [m for m in calls[1] if m.get("role") == "user"][-1]
        assert isinstance(second_user["content"], str), "第二次调用必须退化为纯文本"
        assert second_user["content"] == "这张图里是什么？"
        assert "image_url" not in str(calls[1])
