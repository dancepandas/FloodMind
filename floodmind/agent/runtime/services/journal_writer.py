"""Canonical JSONL segment journal writer with CAS and hash chain (target §4.2).

The JSONL segments are the authoritative journal. index.json is a fast-forward
accelerator that is reconciled against the segments on every load, so a crash
between a segment append and an index save can never re-use a sequence or hash.
Appends take a cross-platform file lock so concurrent writer instances against
the same run observe each other's tail and raise on CAS conflicts.
"""

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from floodmind.agent.runtime.contracts.canonical_events import (
    EventEnvelope, canonical_json, canonical_payload_sha256,
)

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover - Windows uses msvcrt
    fcntl = None


class JournalWriteConflict(Exception):
    """expected_last_sequence did not match the on-disk tail."""


_SEGMENT_PREFIX = "events-"
_SEGMENT_SUFFIX = ".jsonl"


def _segment_name(number: int) -> str:
    return f"{_SEGMENT_PREFIX}{number:06d}{_SEGMENT_SUFFIX}"


def _hash_input(event: EventEnvelope) -> str:
    d = event.model_dump()
    d["integrity"] = {}
    return canonical_json(d)


class JournalWriter:
    def __init__(
        self,
        base_dir: Path,
        run_id: str,
        *,
        max_segment_bytes: int = 10 * 1024 * 1024,
    ):
        self._base_dir = Path(base_dir)
        self._run_id = run_id
        self._max_segment_bytes = max_segment_bytes
        self._journal_dir = self._base_dir / "runs" / run_id / "journal"
        self._journal_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._journal_dir / ".lock"
        self._index_path = self._journal_dir / "index.json"
        self._sealed: Dict[str, EventEnvelope] = {}
        self._load_index()

    # ── lock ─────────────────────────────────────────────────────

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with open(self._lock_path, "a+b") as f:
            if f.seek(0, os.SEEK_END) == 0:
                f.write(b"\x00")
                f.flush()
            f.seek(0)
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            else:
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                else:
                    import msvcrt
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass

    # ── index + authoritative reconciliation ─────────────────────

    def _load_index(self) -> None:
        self._last_sequence = 0
        self._last_event_sha256 = ""
        self._current_segment = 1
        self._sealed = {}
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                self._last_sequence = int(data.get("last_sequence", 0))
                self._last_event_sha256 = str(data.get("last_event_sha256", ""))
                self._current_segment = int(data.get("current_segment", 1))
            except (ValueError, OSError):
                pass
        self._reconcile_from_journal()

    def _reconcile_from_journal(self) -> None:
        """Rebuild the tail from the authoritative JSONL.

        The journal segments are the source of truth: a crash between a segment
        append and an index save, or a corrupted index, must not let the writer
        re-use a sequence or a previous-event hash. A corrupt line is treated as
        the end of the authoritative tail.
        """
        last_sequence = 0
        last_hash = ""
        sealed: Dict[str, EventEnvelope] = {}
        for number in range(1, self._current_segment + 1):
            path = self._segment_path(number)
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = EventEnvelope.model_validate_json(line)
                except Exception:
                    break
                sealed[event.event_id] = event
                if event.sequence > last_sequence:
                    last_sequence = event.sequence
                    last_hash = event.integrity.event_sha256
        if last_sequence >= self._last_sequence:
            self._last_sequence = last_sequence
            self._last_event_sha256 = last_hash
        self._sealed.update(sealed)

    def _save_index(self) -> None:
        index = {
            "run_id": self._run_id,
            "last_sequence": self._last_sequence,
            "last_event_sha256": self._last_event_sha256,
            "current_segment": self._current_segment,
            "event_ids": sorted(self._sealed),
        }
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, indent=2), encoding="utf-8")
        tmp.replace(self._index_path)

    def _segment_path(self, number: int) -> Path:
        return self._journal_dir / _segment_name(number)

    # ── public API ──────────────────────────────────────────────

    def current_sequence(self) -> int:
        return self._last_sequence

    def segment_count(self) -> int:
        return self._current_segment

    def sealed(self, event_id: str) -> Optional[EventEnvelope]:
        return self._sealed.get(event_id)

    def append(
        self,
        event: EventEnvelope,
        *,
        expected_last_sequence: Optional[int] = None,
    ) -> EventEnvelope:
        with self._locked():
            # Re-read the authoritative tail under the lock: a concurrent writer
            # instance may have advanced the journal since this one loaded it.
            self._reconcile_from_journal()
            if expected_last_sequence is not None and expected_last_sequence != self._last_sequence:
                raise JournalWriteConflict(
                    f"expected last sequence {expected_last_sequence}, got {self._last_sequence}"
                )
            existing = self._sealed.get(event.event_id)
            if existing is not None:
                return existing  # idempotent retry: return the persisted sealed envelope

            event.sequence = self._last_sequence + 1
            event.integrity.payload_sha256 = canonical_payload_sha256(event.payload)
            event.integrity.previous_event_sha256 = self._last_event_sha256
            event.integrity.event_sha256 = hashlib.sha256(
                f"{self._last_event_sha256}|{_hash_input(event)}".encode("utf-8")
            ).hexdigest()

            path = self._segment_path(self._current_segment)
            with path.open("a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
                f.flush()
                os.fsync(f.fileno())

            self._last_sequence = event.sequence
            self._last_event_sha256 = event.integrity.event_sha256
            self._sealed[event.event_id] = event

            if path.stat().st_size > self._max_segment_bytes:
                self.roll_segment()
            else:
                self._save_index()
            return event

    def roll_segment(self) -> None:
        self._current_segment += 1
        self._save_index()

    def read_from(self, after_sequence: int = 0) -> List[EventEnvelope]:
        events: List[EventEnvelope] = []
        for number in range(1, self._current_segment + 1):
            path = self._segment_path(number)
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = EventEnvelope.model_validate_json(line)
                except Exception:
                    continue  # half-written tail / corrupted line is skipped on read
                if event.sequence > after_sequence:
                    events.append(event)
        events.sort(key=lambda e: e.sequence)
        return events
