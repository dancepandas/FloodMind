"""
Event replayer — rebuild AgentLoopState from a checkpoint + subsequent events.

Usage:
    from_checkpoint = replayer.replay_from_checkpoint(checkpoint, all_events)
"""

import logging
from typing import List, Optional

from floodmind.agent.native.events.schema import SessionEvent
from floodmind.agent.native.events.updater import apply_event
from floodmind.agent.native.types import AgentLoopState
from .checkpoint import Checkpoint

logger = logging.getLogger(__name__)


def replay_from_checkpoint(
    checkpoint: Checkpoint,
    events_after: List[SessionEvent],
) -> AgentLoopState:
    """Rebuild state from checkpoint + replay events that happened after it.

    This is the core resume mechanism:
    1. Build initial state from checkpoint
    2. Replay events in chronological order
    3. Return fully reconstructed state
    """
    # 1. Build base state from checkpoint
    state = AgentLoopState(
        run_id=f"resumed-{checkpoint.session_id}-{checkpoint.step}",
        iteration=checkpoint.step,
        plan=checkpoint.plan,
        execution_journal=[],
    )

    # 2. Replay events from the checkpoint's perspective
    for event in sorted(events_after, key=lambda e: e.timestamp):
        state = apply_event(state, event)

    logger.info(
        "[Replayer] state rebuilt from checkpoint step=%d, replayed %d events",
        checkpoint.step, len(events_after),
    )
    return state


def build_initial_state(session_id: str) -> AgentLoopState:
    """Create a fresh AgentLoopState for a new session."""
    return AgentLoopState(
        run_id=f"run-{session_id}",
        iteration=0,
        execution_journal=[],
    )
