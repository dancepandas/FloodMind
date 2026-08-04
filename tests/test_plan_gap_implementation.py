import json

from floodmind.agent.native.event_bus import EventBus
from floodmind.agent.runtime.contracts.events import VALID_EVENT_TYPES
from floodmind.memory.session_manager import SessionManager
from floodmind.plugin.loader import PluginLoader


def test_session_manager_messages_page_cursor(tmp_path):
    sm = SessionManager(config={"data_dir": str(tmp_path)})
    session_id = "ses_page"
    memory_dir = sm.get_memory_dir(session_id)
    memory_dir.mkdir(parents=True, exist_ok=True)
    turns = []
    for i in range(6):
        turns.append({"role": "user", "content": f"u{i}"})
        turns.append({"role": "assistant", "content": f"a{i}"})
    (memory_dir / "chat_history.json").write_text(json.dumps({"turns": turns}), encoding="utf-8")

    page1 = sm.get_messages_page(session_id, limit=3)
    assert [m["content"] for m in page1["items"]] == ["a4", "u5", "a5"]
    assert page1["more"] is True
    assert page1["cursor"]

    page2 = sm.get_messages_page(session_id, limit=3, before_cursor=page1["cursor"])
    assert [m["content"] for m in page2["items"]] == ["u3", "a3", "u4"]
    assert page2["more"] is True


def test_compaction_aliases_emit_current_event_names():
    bus = EventBus()
    events = []
    bus.add_listener(events.append)

    bus.emit_compaction_start(reason="manual")
    bus.emit_compaction_end("summary")

    assert events[0]["type"] == "context_compress_start"
    assert events[0]["reason"] == "manual"
    assert events[1]["type"] == "context_compress_done"
    assert events[1]["summary_preview"] == "summary"


def test_new_event_types_are_in_runtime_contract():
    for event_type in [
        "llm_step_start",
        "llm_step_end",
        "retry_attempt",
        "context_compress_start",
        "context_compress_done",
    ]:
        assert event_type in VALID_EVENT_TYPES


def test_plugin_loader_discover_is_idempotent_and_avoids_module_name_collision(tmp_path):
    root1 = tmp_path / "p1"
    root2 = tmp_path / "p2"
    root1.mkdir()
    root2.mkdir()
    plugin_code = """
from floodmind.plugin import FloodmindPlugin
class MyPlugin(FloodmindPlugin):
    pass
"""
    (root1 / "same.py").write_text(plugin_code, encoding="utf-8")
    (root2 / "same.py").write_text(plugin_code.replace("MyPlugin", "OtherPlugin"), encoding="utf-8")

    loader = PluginLoader([root1, root2])
    first = loader.discover()
    second = loader.discover()

    assert len(first) == 2
    assert len(second) == 2
    assert {p.name for p in second} == {"MyPlugin", "OtherPlugin"}
