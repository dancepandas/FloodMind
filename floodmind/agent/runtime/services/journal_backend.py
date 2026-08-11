"""Journal 存储后端协议（目标 §18：SQLite 只作 Backend 或 Index）。"""
from typing import List, Optional, Protocol, runtime_checkable

from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope


@runtime_checkable
class JournalBackend(Protocol):
    """Journal 后端 seam。JSONL JournalWriter 与 SQLite Index 均实现此协议。"""

    def append(
        self, event: EventEnvelope, *, expected_last_sequence: Optional[int] = None,
    ) -> EventEnvelope: ...

    def append_many(
        self, events: List[EventEnvelope], *, expected_last_sequence: Optional[int] = None,
    ) -> List[EventEnvelope]: ...

    def current_sequence(self) -> int: ...

    def read_from(self, after_sequence: int = 0) -> List[EventEnvelope]: ...

    def sealed(self, event_id: str) -> Optional[EventEnvelope]: ...
