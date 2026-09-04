"""JournalAuthority：per-run 身份作用域的事件写入/重放门面（目标 §4/§5）。

- emit(): 构造 EventEnvelope，填充当前 run/thread/turn/attempt 身份，落盘（CAS 冲突抛 JournalWriteConflict）。
- append_group(): 一次原子提交多事件（Task 3 append_many）。
- replay(): 从 cursor 之后读事件，按 event_id 去重，用确定性 Reducer 折叠出 RunState。
"""

from typing import Dict, List, Optional, Any

import logging

from pathlib import Path

from floodmind.agent.runtime.contracts.canonical_events import (
    EventEnvelope, Actor, utcnow,
)
from floodmind.agent.runtime.contracts.run_state import RunState
from floodmind.agent.runtime.reducer import reduce, initial_run_state
from floodmind.agent.runtime.services.journal_writer import JournalWriter
from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT


logger = logging.getLogger(__name__)

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
    index: bool = False,
) -> "JournalAuthority":
    journal_dir = _run_journal_dir(runtime_dir, conversation_id, task_id, run_id)
    writer = JournalWriter(runtime_dir, run_id, journal_dir=journal_dir)
    journal_index = None
    if index:
        from floodmind.agent.runtime.services.journal_index import SqliteJournalIndex
        journal_index = SqliteJournalIndex(journal_dir, run_id)
        journal_index.rebuild_from(journal_dir)
    return JournalAuthority(
        writer=writer,
        index=journal_index,
        journal_dir=journal_dir,
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
        index: Optional[Any] = None,
        journal_dir: Optional[Path] = None,
        conversation_id: str,
        task_id: str,
        run_id: str,
        thread_id: str,
        turn_id: str,
        attempt_id: str = "",
    ):
        self._writer = writer
        self._index = index
        self._journal_dir = Path(journal_dir) if journal_dir is not None else None
        self.conversation_id = conversation_id
        self.task_id = task_id
        self.run_id = run_id
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.attempt_id = attempt_id
        # trace processors：canonical 事件只读旁路（异常隔离，不回放历史）
        import threading
        self._processors: List[Any] = []
        self._processors_lock = threading.RLock()

    def _scope(self, scope: dict, key: str) -> str:
        """解析 scope 覆盖；显式 None 视为未提供，回落权威身份默认值。"""
        value = scope.get(key)
        return value if value else getattr(self, key, "")

    def new_envelope(self, event_type: str, payload: Dict[str, Any], **scope) -> EventEnvelope:
        import uuid
        return EventEnvelope(
            event_id=f"evt_{uuid.uuid4().hex}",
            event_type=event_type,
            sequence=0,  # writer assigns the real sequence on append
            conversation_id=self.conversation_id,
            task_id=self.task_id,
            run_id=self.run_id,
            thread_id=self._scope(scope, "thread_id"),
            turn_id=self._scope(scope, "turn_id"),
            attempt_id=self._scope(scope, "attempt_id"),
            call_id=self._scope(scope, "call_id"),
            actor=Actor(
                type=scope.get("actor_type") or "system",
                id=scope.get("actor_id") or "",
            ),
            payload=payload,
            recorded_at=utcnow(),
        )

    def emit(self, event_type: str, payload: Dict[str, Any], **scope) -> EventEnvelope:
        envelope = self.new_envelope(event_type, payload, **scope)
        envelope = self._writer.append(envelope)
        if self._index:
            try:
                self._index.index_event(envelope)
            except Exception:
                pass
        self._notify_processors(envelope)
        return envelope

    # ── trace processors：canonical 事件的只读旁路消费 ──────────────────

    def add_processor(self, processor: Any) -> None:
        """注册旁路处理器（on_event(envelope)）。只影响注册后的事件，不回放历史。"""
        with self._processors_lock:
            if processor not in self._processors:
                self._processors.append(processor)

    def remove_processor(self, processor: Any) -> None:
        with self._processors_lock:
            try:
                self._processors.remove(processor)
            except ValueError:
                pass

    def _notify_processors(self, envelope: EventEnvelope) -> None:
        """逐个回调 processor；异常隔离（宿主处理器故障不影响写入路径）。"""
        with self._processors_lock:
            processors = list(self._processors)
        for processor in processors:
            try:
                processor.on_event(envelope)
            except Exception as exc:
                logger.warning("trace processor %r failed: %s", processor, exc)

    def append_group(self, events: List[EventEnvelope]) -> List[EventEnvelope]:
        envelopes = self._writer.append_many(events)
        if self._index:
            for envelope in envelopes:
                try:
                    self._index.index_event(envelope)
                except Exception:
                    pass
        return envelopes

    def cursor(self) -> int:
        return self._writer.current_sequence()

    def read_after(self, after_sequence: int = 0) -> List[EventEnvelope]:
        if self._index is not None:
            try:
                if self._index.count() == self._writer.current_sequence():
                    return self._index.read_after(after_sequence)
            except Exception:
                pass
            events = self._writer.read_from(after_sequence)
            try:
                self._index.rebuild_from(self._journal_dir)
            except Exception:
                pass
            return events
        return self._writer.read_from(after_sequence)

    def replay(self, after_sequence: int = 0, state: Optional[RunState] = None,
               events: Optional[List[EventEnvelope]] = None) -> RunState:
        current = state or initial_run_state(
            self.run_id, conversation_id=self.conversation_id,
            task_id=self.task_id, thread_id=self.thread_id,
        )
        seen: set = set()
        for event in (self._writer.read_from(after_sequence) if events is None else events):
            if event.event_id in seen:
                continue  # 重复 event_id 不重复副作用
            seen.add(event.event_id)
            current = reduce(current, event)
        if self.thread_id:
            current.turns = [
                turn for turn in current.turns
                if turn.get("thread_id", "") in ("", self.thread_id)
            ]
        return current

    def checkpoint_snapshot(self) -> tuple:
        """原子读取 (journal_cursor, replayed RunState)，供 checkpoint 保存使用。

        checkpoint.save 要求两者严格相等。并行 specialist 共享同一份 run journal，
        先调 cursor() 再调 replay() 的两次独立读取之间可能插入其他线程的 append，
        造成 cursor 与 last_committed_sequence 错位（checkpoint 保存失败）。
        这里单次 read_from 取同一批事件：cursor 取批内最后一条的 sequence——
        并发写入者只会让快照偏旧（恢复时按幂等 reducer 重放补齐），不会偏新。
        """
        events = self._writer.read_from(0)
        cursor = events[-1].sequence if events else 0
        return cursor, self.replay(events=events)
