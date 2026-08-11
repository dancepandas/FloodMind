"""P8 Task 2 — SDK Agent 标准身份 + events_after 契约。"""

from floodmind.agent.api import Agent
from floodmind.agent.native.model_client import ModelClient


def _agent(tmp_path):
    mc = ModelClient(
        api_key="mock-key", base_url="https://mock.api/v1", model_name="mock-model"
    )
    mc.stream_chat = lambda *args, **kwargs: iter([
        __import__("floodmind.agent.native.types", fromlist=["ModelEvent"]).ModelEvent(
            type="token", content="ok"),
        __import__("floodmind.agent.native.types", fromlist=["ModelEvent"]).ModelEvent(
            type="done"),
    ])
    return Agent(llm=mc, session_id="sdk-sess", bare=True)


def test_agent_standard_identity_after_run(tmp_path):
    agent = _agent(tmp_path)
    out = agent.run("hi")
    assert isinstance(out, str)
    assert agent.run_id and agent.run_id.startswith("run_")
    assert isinstance(agent.conversation_id, str)


def test_agent_events_after_replays_committed(tmp_path):
    agent = _agent(tmp_path)
    agent.run("hi")
    events = agent.events_after(0)
    assert isinstance(events, list)
    if events:
        assert all(e["sequence"] >= 0 for e in events)
        seqs = [e["sequence"] for e in events]
        assert seqs == sorted(seqs)
