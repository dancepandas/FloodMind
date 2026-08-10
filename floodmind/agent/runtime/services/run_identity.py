"""Resolve one session into canonical event-sourcing identities."""

import json
from pathlib import Path

from floodmind.agent.runtime.contracts.identity import new_id


def resolve_identity(session_id: str, session_dir: Path) -> dict:
    """Return stable conversation identity plus fresh task/run/thread/turn IDs."""
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    meta_path = session_dir / "session.json"
    metadata = {}
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            metadata = {}
    conversation_id = str(metadata.get("conversation_id") or "")
    if not conversation_id:
        conversation_id = new_id("conversation")
        metadata["conversation_id"] = conversation_id
        metadata.setdefault("session_id", session_id)
        meta_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {
        "conversation_id": conversation_id,
        "task_id": new_id("task"),
        "run_id": new_id("run"),
        "thread_id": new_id("thread"),
        "turn_id": new_id("turn"),
    }
