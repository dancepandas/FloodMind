from floodmind.agent.runtime.contracts.child_thread import (
    ChildThread, SubagentEventType, SubagentResult,
)


def test_child_thread_defaults():
    t = ChildThread(thread_id="thread_1", parent_thread_id="thread_0",
                    parent_call_id="call_1")
    assert t.tool_allowlist == []
    assert t.max_turns == 50


def test_subagent_result_typed_handoff():
    r = SubagentResult(thread_id="thread_1", parent_call_id="call_1",
                       event_type=SubagentEventType.result, summary="done",
                       artifact_ids=["art_1"])
    assert r.event_type == SubagentEventType.result
    assert r.needs_human is False
