"""Canonical Journal to flat conversation-turn projections."""

from pathlib import Path
from typing import Dict, List

from floodmind.agent.runtime.reducer import initial_run_state, reduce
from floodmind.agent.runtime.services.journal_authority import JournalAuthority
from floodmind.agent.runtime.services.journal_writer import JournalWriter


def project_current(auth: JournalAuthority) -> List[Dict]:
    """Project the authority's current run into flat turns."""
    return auth.replay(after_sequence=0).turns


def project_conversation(runtime_dir, conversation_id: str) -> List[Dict]:
    """Project every run below a conversation into one flat turn stream.

    Conversation runs are assumed sequential and ``recorded_at`` monotonic. Equal
    timestamps are resolved deterministically by sequence and event ID. Concurrent
    runs for one conversation are not projected causally and remain out of scope
    until child-thread work lands within the same run.
    """
    runs_root = Path(runtime_dir) / "conversations" / conversation_id / "tasks"
    if not runs_root.is_dir():
        return []

    events = []
    for task_dir in sorted(runs_root.iterdir()):
        runs_dir = task_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted(runs_dir.iterdir()):
            journal_dir = run_dir / "journal"
            if journal_dir.is_dir():
                writer = JournalWriter(Path(runtime_dir), run_dir.name, journal_dir=journal_dir)
                events.extend(writer.read_from(0))

    events.sort(key=lambda event: (event.recorded_at, event.sequence, event.event_id))
    state = initial_run_state("conversation_projection", conversation_id=conversation_id)
    seen = set()
    for event in events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        state = reduce(state, event)
    return state.turns
