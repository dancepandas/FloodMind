"""
Checkpoint — session state snapshot for resume.

A checkpoint captures the complete state needed to resume a session
from a specific step, including messages, events, plan, and file snapshots.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from floodmind.agent.native.types import AgentLoopState, ExecutionPlan

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    """Immutable snapshot of session state at a specific step."""

    session_id: str
    step: int
    agent_name: str = "build"
    plan: Optional[ExecutionPlan] = None
    messages: List[dict] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)
    artifact_snapshot: str = ""  # hash or manifest of file state
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "step": self.step,
            "agent_name": self.agent_name,
            "plan": {
                "plan_id": self.plan.plan_id,
                "user_message": self.plan.user_message,
                "goal_deliverables": self.plan.goal_deliverables,
                "steps": self.plan.steps,
                "created_at": self.plan.created_at,
                "updated_at": self.plan.updated_at,
                "terminal_status": self.plan.terminal_status,
            } if self.plan else {},
            "messages": self.messages,
            "events": self.events,
            "artifact_snapshot": self.artifact_snapshot,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Checkpoint":
        plan_data = data.get("plan", {})
        plan = ExecutionPlan.from_dict(plan_data) if plan_data else None
        return cls(
            session_id=data.get("session_id", ""),
            step=data.get("step", 0),
            agent_name=data.get("agent_name", "build"),
            plan=plan,
            messages=data.get("messages", []),
            events=data.get("events", []),
            artifact_snapshot=data.get("artifact_snapshot", ""),
            created_at=data.get("created_at", 0.0),
        )


class CheckpointStore:
    """Save/load checkpoints via SQLite session_store."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path

    def save(self, checkpoint: Checkpoint) -> int:
        """Persist checkpoint. Returns checkpoint id."""
        import sqlite3
        conn = sqlite3.connect(self._db_path or str(_default_db_path()), check_same_thread=False)
        try:
            cursor = conn.execute(
                """
                INSERT INTO checkpoints
                (session_id, step, agent_name, plan_json, messages_json, events_json, artifact_snapshot, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.session_id,
                    checkpoint.step,
                    checkpoint.agent_name,
                    json.dumps(checkpoint.plan.to_dict() if checkpoint.plan else {}, ensure_ascii=False),
                    json.dumps(checkpoint.messages, ensure_ascii=False),
                    json.dumps(checkpoint.events, ensure_ascii=False),
                    checkpoint.artifact_snapshot,
                    checkpoint.created_at or time.time(),
                ),
            )
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()

    def load_latest(self, session_id: str) -> Optional[Checkpoint]:
        """Load the most recent checkpoint for a session."""
        import sqlite3
        conn = sqlite3.connect(self._db_path or str(_default_db_path()), check_same_thread=False)
        try:
            cursor = conn.execute(
                "SELECT * FROM checkpoints WHERE session_id = ? ORDER BY step DESC, created_at DESC LIMIT 1",
                (session_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return _row_to_checkpoint(row)
        finally:
            conn.close()

    def load_at_step(self, session_id: str, step: int) -> Optional[Checkpoint]:
        """Load checkpoint at or before the given step."""
        import sqlite3
        conn = sqlite3.connect(self._db_path or str(_default_db_path()), check_same_thread=False)
        try:
            cursor = conn.execute(
                "SELECT * FROM checkpoints WHERE session_id = ? AND step <= ? ORDER BY step DESC, created_at DESC LIMIT 1",
                (session_id, step),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return _row_to_checkpoint(row)
        finally:
            conn.close()

    def list_checkpoints(self, session_id: str) -> List[Checkpoint]:
        """List all checkpoints for a session."""
        import sqlite3
        conn = sqlite3.connect(self._db_path or str(_default_db_path()), check_same_thread=False)
        try:
            cursor = conn.execute(
                "SELECT * FROM checkpoints WHERE session_id = ? ORDER BY step ASC, created_at ASC",
                (session_id,),
            )
            return [_row_to_checkpoint(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def prune_old(self, session_id: str, keep_last: int = 10) -> int:
        """Delete old checkpoints, keeping only the last N. Returns deleted count."""
        import sqlite3
        conn = sqlite3.connect(self._db_path or str(_default_db_path()), check_same_thread=False)
        try:
            cursor = conn.execute(
                """
                DELETE FROM checkpoints
                WHERE session_id = ? AND id NOT IN (
                    SELECT id FROM checkpoints WHERE session_id = ? ORDER BY step DESC, created_at DESC LIMIT ?
                )
                """,
                (session_id, session_id, keep_last),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


def _default_db_path():
    from pathlib import Path
    return Path.home() / ".config" / "floodmind" / "sessions.db"


def _row_to_checkpoint(row) -> Checkpoint:
    """Convert a sqlite3.Row to Checkpoint."""
    plan_json = row["plan_json"] or "{}"
    plan_data = json.loads(plan_json)
    return Checkpoint(
        session_id=row["session_id"],
        step=row["step"],
        agent_name=row["agent_name"],
        plan=ExecutionPlan.from_dict(plan_data) if plan_data else None,
        messages=json.loads(row["messages_json"] or "[]"),
        events=json.loads(row["events_json"] or "[]"),
        artifact_snapshot=row["artifact_snapshot"] or "",
        created_at=row["created_at"],
    )
