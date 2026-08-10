"""JournalAuthority：per-run 身份作用域的事件写入/重放门面（目标 §4/§5）。

- emit(): 构造 EventEnvelope，填充当前 run/thread/turn/attempt 身份，落盘（CAS 冲突抛 JournalWriteConflict）。
- append_group(): 一次原子提交多事件（Task 3 append_many）。
- replay(): 从 cursor 之后读事件，按 event_id 去重，用确定性 Reducer 折叠出 RunState。
"""

from typing import Dict, List, Optional, Any

from pathlib import Path

from floodmind.agent.runtime.contracts.canonical_events import (
    EventEnvelope, Actor, utcnow,
)
from floodmind.agent.runtime.contracts.identity import new_id
from floodmind.agent.runtime.contracts.run_state import RunState
from floodmind.agent.runtime.reducer import reduce, initial_run_state
from floodmind.agent.runtime.services.journal_writer import JournalWriter
from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT


DEFAULT_RUNTIME_ROOT: Path = PROJECT_ROOT / ".floodmind"


def _run_journal_dir(runtime_dir: Path, conversation_id: str, task_id: str, run_id: str) -> Path:
    return (
        Path(runtime_dir) / "conversations" / conversation_id / "tasks" / task_id
        / "runs" / run_id / "journal"
    )


def open_journal_authority(
    runtime_dir: Path,
    *,
    conversation_id: str,
    task_id: str,
    run_id: str,
    thread_id: str,
    turn_id: str,
    attempt_id: str = "",
) -> "JournalAuthority":
    journal_dir = _run_journal_dir(runtime_dir, conversation_id, task_id, run_id)
    writer = JournalWriter(runtime_dir, run_id, journal_dir=journal_dir)
    return JournalAuthority(
        writer=writer,
        conversation_id=conversation_id,
        task_id=task_id,
        run_id=run_id,
        thread_id=thread_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
    )


class JournalAuthority:
    def __init__(
        self,
        *,
        writer: JournalWriter,
        conversation_id: str,
        task_id: str,
        run_id: str,
        thread_id: str,
        turn_id: str,
        attempt_id: str = "",
    ):
        self._writer = writer
        self.conversation_id = conversation_id
        self.task_id = task_id
        self.run_id = run_id
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.attempt_id = attempt_id

    def new_envelope(self, event_type: str, payload: Dict[str, Any], **scope) -> EventEnvelope:
        import uuid
        return EventEnvelope(
            event_id=f"evt_{uuid.uuid4().hex}",
            event_type=event_type,
            sequence=0,  # writer assigns the real sequence on append
            conversation_id=self.conversation_id,
            task_id=self.task_id,
            run_id=self.run_id,
            thread_id=scope.get("thread_id", self.thread_id),
            turn_id=scope.get("turn_id", self.turn_id),
            attempt_id=scope.get("attempt_id", self.attempt_id),
            call_id=scope.get("call_id", ""),
            actor=Actor(type=scope.get("actor_type", "system"), id=scope.get("actor_id", "")),
            payload=payload,
            recorded_at=utcnow(),
        )

    def emit(self, event_type: str, payload: Dict[str, Any], **scope) -> EventEnvelope:
        envelope = self.new_envelope(event_type, payload, **scope)
        return self._writer.append(envelope)

    def append_group(self, events: List[EventEnvelope]) -> List[EventEnvelope]:
        return self._writer.append_many(events)

    def cursor(self) -> int:
        return self._writer.current_sequence()

    def read_after(self, after_sequence: int = 0) -> List[EventEnvelope]:
        return self._writer.read_from(after_sequence)

    def replay(self, after_sequence: int = 0, state: Optional[RunState] = None) -> RunState:
        current = state or initial_run_state(
            self.run_id, conversation_id=self.conversation_id,
            task_id=self.task_id, thread_id=self.thread_id,
        )
        seen: set = set()
        for event in self._writer.read_from(after_sequence):
            if event.event_id in seen:
                continue  # 重复 event_id 不重复副作用
            seen.add(event.event_id)
            current = reduce(current, event)
        return current
