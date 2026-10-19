# P0+P1 Forward-Only Migration: Baseline Fixes + Canonical Contracts

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two pre-existing test defects and add the pure-data Canonical Contracts layer (identity, event envelope, journal writer, tool transaction, runtime context, manifests, model parts, child thread) without changing any runtime behavior.

**Architecture:** P1 adds contracts as a pure data layer under `floodmind/agent/runtime/contracts/` plus a JSONL journal writer under `floodmind/agent/runtime/services/`. Nothing is wired into the executor yet (that is P2). Each module is independently testable.

**Deviation from design (P0):** the design lists "Provider Golden Stream Fixtures" under P0, but they have no consumer until the Model Layer refactor (P4). Per YAGNI they are deliberately deferred to the P4 plan. Identity/event-taxonomy freeze (design P0) is delivered here as Tasks 3-4.

**Tech Stack:** Python 3.10+, pydantic v2, stdlib `json`/`hashlib`/`uuid`/`pathlib`, pytest.

## Global Constraints

- **No backward compat, no fallback:** do not add legacy adapters, dual-write paths, or `chat_history.json` read fallbacks.
- **JSONL segments** for the journal backend (§18 layout: `events-000001.jsonl` + `index.json`).
- **Deterministic pure functions:** canonical JSON uses `ensure_ascii=False, sort_keys=True, separators=(",", ":")`.
- All IDs are `str` with a `prefix_` + `uuid4().hex` shape (prefixes below).
- Existing runtime behavior MUST NOT change in P1 — no executor/service edits in this plan.
- Test command: `python -m pytest -q <path> -p no:cacheprovider`. Full baseline at the end: `python -m pytest -q -p no:cacheprovider` (after Task 1, the `--deselect` is no longer needed).

## File Structure

| File | Responsibility |
|---|---|
| `tests/test_profile_system.py` (modify) | Rename recursive full-suite test so pytest does not collect it |
| `tests/test_agent_skill_roots.py` (modify) | Fix background-subscriber assertion shape |
| `floodmind/agent/runtime/contracts/identity.py` (create) | ID prefixes, `new_id`/`is_valid_id`, `Identity` hierarchy ref |
| `floodmind/agent/runtime/contracts/canonical_events.py` (create) | `EventEnvelope`, `Provenance`, `Actor`, `EventIntegrity`, `EVENT_TYPES` |
| `floodmind/agent/runtime/services/journal_writer.py` (create) | JSONL segment writer: CAS append, idempotency, hash chain, rolling, tail recovery |
| `floodmind/agent/runtime/contracts/transactions.py` (create) | `ToolTransaction`, canonical args, `arguments_sha256`, fingerprint helpers |
| `floodmind/agent/runtime/contracts/runtime_context.py` (create) | Immutable `RuntimeContext` |
| `floodmind/agent/runtime/contracts/artifacts.py` (create) | `ArtifactManifest` |
| `floodmind/agent/runtime/contracts/context_manifest.py` (create) | `ProjectionManifest`, `ProjectionSource` |
| `floodmind/agent/runtime/contracts/parts.py` (create) | Canonical model part events |
| `floodmind/agent/runtime/contracts/child_thread.py` (create) | `ChildThread`, `SubagentResult` |
| `tests/test_identity.py`, `tests/test_canonical_events.py`, `tests/test_journal_writer.py`, `tests/test_transactions.py`, `tests/test_runtime_context.py`, `tests/test_artifacts.py`, `tests/test_context_manifest.py`, `tests/test_parts.py`, `tests/test_child_thread.py` (create) | Per-module tests |

---

### Task 1: Fix recursive full-suite test

**Files:**
- Modify: `tests/test_profile_system.py:352-380`

**Interfaces:**
- Produces: `run_full_pytest_suite()` (plain function, not collected by pytest), invoked only from `__main__`.

- [ ] **Step 1: Rename the function so pytest does not collect it**

In `tests/test_profile_system.py`, change line 352 from:

```python
def test_10_pytest():
```

to:

```python
def run_full_pytest_suite():
```

and change line 380 in the `__main__` block from:

```python
    run_test_suite("10. Full Pytest Suite", test_10_pytest)
```

to:

```python
    run_test_suite("10. Full Pytest Suite", run_full_pytest_suite)
```

- [ ] **Step 2: Verify it is no longer collected**

Run: `python -m pytest -q tests/test_profile_system.py --collect-only -p no:cacheprovider | grep test_10`
Expected: no match (function renamed, pytest collects zero `test_10_pytest`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_profile_system.py
git commit -m "fix(test): stop test_10_pytest from recursively running the whole suite under pytest"
```

---

### Task 2: Fix background-subscriber test assertion shape

**Files:**
- Modify: `tests/test_agent_skill_roots.py:151,157`

**Interfaces:**
- Consumes: `BackgroundTaskService._subscribers` is `List[Tuple[Optional[str], Callable]]` (session_id, callback).
- Produces: corrected membership assertions using the tuple form.

- [ ] **Step 1: Write the corrected assertions**

In `tests/test_agent_skill_roots.py`, replace line 151:

```python
    assert a.raw._bg_task_callback in bg_service._subscribers
```

with:

```python
    assert (a.raw.session_id, a.raw._bg_task_callback) in bg_service._subscribers
```

and replace line 157:

```python
    assert a.raw._bg_task_callback not in bg_service._subscribers
```

with:

```python
    assert (a.raw.session_id, a.raw._bg_task_callback) not in bg_service._subscribers
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest -q tests/test_agent_skill_roots.py::test_programmatic_update_and_cleanup_are_owner_scoped -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_skill_roots.py
git commit -m "fix(test): assert tuple (session_id, callback) membership in background subscriber list"
```

---

### Task 3: Identity contract

**Files:**
- Create: `floodmind/agent/runtime/contracts/identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `ID_PREFIXES: Dict[str, str]`
  - `new_id(kind: str) -> str`
  - `is_valid_id(kind: str, value: str) -> bool`
  - `@dataclass(frozen=True) class Identity` with fields `conversation_id`, `task_id`, `run_id`, `thread_id`, `turn_id`, `attempt_id: str | None = None`, `call_id: str | None = None`.

- [ ] **Step 1: Write the failing test**

`tests/test_identity.py`:

```python
import pytest

from floodmind.agent.runtime.contracts.identity import (
    ID_PREFIXES, Identity, is_valid_id, new_id,
)


def test_prefixes_cover_target_identities():
    for kind in ("conversation", "task", "run", "thread", "turn", "attempt",
                 "call", "transaction", "artifact", "checkpoint"):
        assert kind in ID_PREFIXES


def test_new_id_shape_and_validity():
    conv = new_id("conversation")
    assert conv.startswith("conv_")
    assert is_valid_id("conversation", conv)
    assert not is_valid_id("run", conv)


def test_new_id_is_unique():
    assert new_id("call") != new_id("call")


def test_new_id_rejects_unknown_kind():
    with pytest.raises(ValueError):
        new_id("nope")


def test_identity_defaults():
    i = Identity(conversation_id="conv_1", task_id="task_1", run_id="run_1",
                 thread_id="thread_1", turn_id="turn_1")
    assert i.attempt_id is None
    assert i.call_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_identity.py -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'floodmind.agent.runtime.contracts.identity'`.

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/contracts/identity.py`:

```python
"""Canonical identity contract (target §3)."""

from dataclasses import dataclass
from typing import Dict, Optional
import uuid

ID_PREFIXES: Dict[str, str] = {
    "conversation": "conv_",
    "task": "task_",
    "run": "run_",
    "thread": "thread_",
    "turn": "turn_",
    "attempt": "attempt_",
    "call": "call_",
    "transaction": "ttx_",
    "artifact": "art_",
    "checkpoint": "chk_",
}


def new_id(kind: str) -> str:
    prefix = ID_PREFIXES.get(kind)
    if prefix is None:
        raise ValueError(f"unknown id kind: {kind!r}")
    return f"{prefix}{uuid.uuid4().hex}"


def is_valid_id(kind: str, value: str) -> bool:
    prefix = ID_PREFIXES.get(kind)
    if prefix is None:
        return False
    return value.startswith(prefix) and len(value) > len(prefix)


@dataclass(frozen=True)
class Identity:
    """Identity scoping for a single run (target §3.1/§3.2)."""

    conversation_id: str
    task_id: str
    run_id: str
    thread_id: str
    turn_id: str
    attempt_id: Optional[str] = None
    call_id: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_identity.py -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/contracts/identity.py tests/test_identity.py
git commit -m "feat(contracts): canonical identity hierarchy with new_id/is_valid_id"
```

---

### Task 4: Canonical Event Envelope + taxonomy

**Files:**
- Create: `floodmind/agent/runtime/contracts/canonical_events.py`
- Test: `tests/test_canonical_events.py`

**Interfaces:**
- Consumes: nothing (pydantic only).
- Produces:
  - `EventEnvelope(BaseModel)` fields: `schema_version: str = "1.0"`, `event_id: str`, `event_type: str`, `sequence: int`, `recorded_at: datetime`, identity fields (`conversation_id/task_id/run_id/thread_id/turn_id/attempt_id/call_id`, each `str = ""`), `causation_id: str = ""`, `correlation_id: str = ""`, `actor: Actor`, `payload: Dict[str, Any]`, `provenance: Provenance`, `integrity: EventIntegrity`.
  - `Actor(BaseModel)`: `type: Literal["user","model","tool","host","system","subagent"] = "system"`, `id: str = ""`.
  - `Provenance(BaseModel)`: `source: Literal["runtime","provider","host","migration"] = "runtime"`, `provider/request_id/code_version/codec_version: str = ""`.
  - `EventIntegrity(BaseModel)`: `payload_sha256/previous_event_sha256/event_sha256: str = ""`.
  - `EVENT_TYPES: frozenset[str]` (subset of §4.3, per category).
  - `canonical_payload_sha256(payload: Dict[str, Any]) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/test_canonical_events.py`:

```python
from floodmind.agent.runtime.contracts.canonical_events import (
    EVENT_TYPES, Actor, EventEnvelope, EventIntegrity, Provenance,
    canonical_payload_sha256,
)


def test_envelope_defaults():
    e = EventEnvelope(event_id="evt_1", event_type="run.started", sequence=1)
    assert e.schema_version == "1.0"
    assert e.actor.type == "system"
    assert e.conversation_id == ""
    assert e.integrity.event_sha256 == ""


def test_required_event_types_present():
    for name in ("run.started", "model.attempt.completed", "tool.execution.started",
                 "tool.result.committed", "context.compaction.completed",
                 "thread.created", "artifact.committed", "checkpoint.created"):
        assert name in EVENT_TYPES


def test_actor_provenance_literals():
    Actor(type="user", id="u1")
    Provenance(source="provider", provider="openai", request_id="r1")


def test_canonical_payload_sha256_deterministic():
    p1 = {"b": 2, "a": 1}
    p2 = {"a": 1, "b": 2}
    assert canonical_payload_sha256(p1) == canonical_payload_sha256(p2)
    assert len(canonical_payload_sha256(p1)) == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_canonical_events.py -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/contracts/canonical_events.py`:

```python
"""Canonical Event Envelope and taxonomy (target §4)."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Literal
from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def canonical_payload_sha256(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class Actor(BaseModel):
    type: Literal["user", "model", "tool", "host", "system", "subagent"] = "system"
    id: str = ""


class Provenance(BaseModel):
    source: Literal["runtime", "provider", "host", "migration"] = "runtime"
    provider: str = ""
    request_id: str = ""
    code_version: str = ""
    codec_version: str = ""


class EventIntegrity(BaseModel):
    payload_sha256: str = ""
    previous_event_sha256: str = ""
    event_sha256: str = ""


class EventEnvelope(BaseModel):
    schema_version: str = "1.0"
    event_id: str
    event_type: str
    sequence: int
    recorded_at: datetime = Field(default_factory=utcnow)

    conversation_id: str = ""
    task_id: str = ""
    run_id: str = ""
    thread_id: str = ""
    turn_id: str = ""
    attempt_id: str = ""
    call_id: str = ""

    causation_id: str = ""
    correlation_id: str = ""

    actor: Actor = Field(default_factory=Actor)
    payload: Dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
    integrity: EventIntegrity = Field(default_factory=EventIntegrity)


_EVENT_TYPES: Dict[str, frozenset[str]] = {
    "run": frozenset({
        "run.created", "run.started", "run.pause.requested", "run.paused",
        "run.cancel.requested", "run.cancel.completed", "run.completed", "run.failed",
    }),
    "context": frozenset({
        "context.projection.started", "context.source.selected",
        "context.source.omitted", "context.compaction.started",
        "context.compaction.completed", "context.projection.committed",
    }),
    "model": frozenset({
        "model.attempt.started", "model.request.committed", "model.stream.preview",
        "model.block.completed", "model.usage.recorded", "model.attempt.completed",
        "model.attempt.failed", "model.retry.scheduled", "model.continuation.requested",
    }),
    "tool": frozenset({
        "tool.call.proposed", "tool.call.validated", "tool.permission.evaluated",
        "tool.approval.requested", "tool.approval.resolved", "tool.execution.started",
        "tool.execution.completed", "tool.execution.failed", "tool.execution.cancelled",
        "tool.execution.indeterminate", "tool.result.committed",
    }),
    "thread": frozenset({
        "thread.spawn.requested", "thread.created", "thread.message.sent",
        "thread.completed", "thread.failed", "thread.cancelled",
    }),
    "artifact": frozenset({
        "artifact.declared", "artifact.committed", "artifact.verified",
        "artifact.superseded",
    }),
    "checkpoint": frozenset({"checkpoint.created", "resume.started",
                             "resume.reconciliation.required", "resume.completed"}),
    "security": frozenset({"security.violation", "privacy.redaction.requested",
                           "privacy.redaction.completed"}),
}

EVENT_TYPES: frozenset[str] = frozenset(
    t for types in _EVENT_TYPES.values() for t in types
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_canonical_events.py -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/contracts/canonical_events.py tests/test_canonical_events.py
git commit -m "feat(contracts): canonical event envelope, provenance, integrity, and event taxonomy"
```

---

### Task 5: JournalWriter — CAS append, idempotency, segments

**Files:**
- Create: `floodmind/agent/runtime/services/journal_writer.py`
- Test: `tests/test_journal_writer.py`

**Interfaces:**
- Consumes: `EventEnvelope` from Task 4, `canonical_payload_sha256`, `canonical_json` (re-exported for hash computation).
- Produces:
  - `class JournalWriteConflict(Exception)`
  - `class JournalWriter`:
    - `__init__(self, base_dir: Path, run_id: str, *, max_segment_bytes: int = 10 * 1024 * 1024)`
    - `append(self, event: EventEnvelope, *, expected_last_sequence: int | None = None) -> EventEnvelope` — fills `integrity.payload_sha256`/`event_sha256`, enforces CAS, persists, returns the sealed envelope.
    - `current_sequence(self) -> int`
    - `read_from(self, after_sequence: int = 0) -> list[EventEnvelope]`
    - `segment_count(self) -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_journal_writer.py`:

```python
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.services.journal_writer import (
    JournalWriteConflict, JournalWriter,
)


def _evt(seq: int, event_id: str = "") -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id or f"evt_{seq}",
        event_type="run.started",
        sequence=seq,
        run_id="run_1",
        payload={"seq": seq},
    )


def test_append_assigns_sequence_and_hashes(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    sealed = w.append(_evt(1))
    assert sealed.sequence == 1
    assert sealed.integrity.payload_sha256
    assert sealed.integrity.event_sha256
    assert w.current_sequence() == 1


def test_cas_conflict_detected(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1))
    with pytest.raises(JournalWriteConflict):
        w.append(_evt(2), expected_last_sequence=0)


def test_idempotent_event_id_not_appended_twice(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1, event_id="evt_x"))
    w.append(_evt(2, event_id="evt_x"))  # same id, should be no-op
    assert w.current_sequence() == 1


def test_read_from_replays_in_order(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1))
    w.append(_evt(2))
    assert [e.sequence for e in w.read_from()] == [1, 2]
    assert [e.sequence for e in w.read_from(after_sequence=1)] == [2]


def test_cas_conflict_across_writer_instances(tmp_path: Path):
    w1 = JournalWriter(tmp_path, "run_1")
    w2 = JournalWriter(tmp_path, "run_1")
    w1.append(_evt(1))
    # w2 was constructed before the append; a stale expected value must conflict
    # against the authoritative on-disk tail, not a cached in-memory value.
    with pytest.raises(JournalWriteConflict):
        w2.append(_evt(2), expected_last_sequence=0)


def test_duplicate_event_id_returns_persisted_sealed_envelope(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    first = w.append(_evt(1, event_id="evt_x"))
    again = w.append(_evt(2, event_id="evt_x"))
    assert again.event_id == first.event_id
    assert again.sequence == first.sequence
    assert again.integrity.event_sha256 == first.integrity.event_sha256


def test_duplicate_event_id_with_wrong_cas_raises(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1, event_id="evt_x"))
    with pytest.raises(JournalWriteConflict):
        w.append(_evt(2, event_id="evt_x"), expected_last_sequence=5)


def test_crash_between_append_and_index_save_recovers_sequence(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1))
    # Simulate a crash window: JSONL already holds the event but index.json still
    # points at sequence 0. A fresh writer must reconcile to the journal tail.
    index = tmp_path / "runs" / "run_1" / "journal" / "index.json"
    index.write_text(
        '{"run_id":"run_1","last_sequence":0,"last_event_sha256":"","current_segment":1,"event_ids":[]}',
        encoding="utf-8",
    )
    w2 = JournalWriter(tmp_path, "run_1")
    assert w2.current_sequence() == 1
    w2.append(_evt(2))
    assert [e.sequence for e in w2.read_from()] == [1, 2]


def test_concurrent_rollover_is_not_missed(tmp_path: Path):
    w1 = JournalWriter(tmp_path, "run_1", max_segment_bytes=1024)
    w2 = JournalWriter(tmp_path, "run_1")  # constructed before w1 rolls
    for i in range(1, 30):
        w1.append(_evt(i))
    assert w1.segment_count() > 1
    # w2 cached current_segment=1; its append must observe w1's rolled tail.
    w2.append(_evt(100))
    seqs = [e.sequence for e in w2.read_from()]
    assert seqs == list(range(1, 30)) + [30]


def test_append_writes_canonical_json_line(tmp_path: Path):
    from floodmind.agent.runtime.contracts.canonical_events import canonical_json
    w = JournalWriter(tmp_path, "run_1")
    sealed = w.append(_evt(1))
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    line = seg.read_text(encoding="utf-8").splitlines()[0]
    assert line == canonical_json(sealed.model_dump())


def test_stale_reader_sees_concurrently_rolled_segments(tmp_path: Path):
    w1 = JournalWriter(tmp_path, "run_1", max_segment_bytes=1024)
    r2 = JournalWriter(tmp_path, "run_1")  # stale reader, never appends
    for i in range(1, 30):
        w1.append(_evt(i))
    assert w1.segment_count() > 1
    assert [e.sequence for e in r2.read_from()] == list(range(1, 30))


def test_unsafe_run_id_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        JournalWriter(tmp_path, "../evil")
```
```
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_journal_writer.py -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/services/journal_writer.py`:

```python
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
        if not run_id or run_id in {".", ".."} or ".." in run_id or Path(run_id).name != run_id:
            raise ValueError(f"unsafe run_id: {run_id!r}")
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
        """Rebuild the tail from the authoritative JSONL segments.

        All on-disk segments are scanned — not just up to the cached current
        segment — so a concurrent writer that rolled a segment is never missed.
        The journal is authoritative: if any events exist they override index
        values, and the current segment advances to the highest one on disk.
        A corrupt line is treated as the end of that segment's tail.
        """
        last_sequence = 0
        last_hash = ""
        highest_segment = 0
        sealed: Dict[str, EventEnvelope] = {}
        for number in self._segment_numbers_on_disk():
            highest_segment = max(highest_segment, number)
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
        if last_sequence:
            self._last_sequence = last_sequence
            self._last_event_sha256 = last_hash
            self._current_segment = max(self._current_segment, highest_segment)
        self._sealed.update(sealed)

    def _segment_numbers_on_disk(self) -> List[int]:
        numbers: List[int] = []
        if self._journal_dir.exists():
            for p in self._journal_dir.glob(f"{_SEGMENT_PREFIX}*{_SEGMENT_SUFFIX}"):
                name = p.name[len(_SEGMENT_PREFIX):-len(_SEGMENT_SUFFIX)]
                if name.isdigit():
                    numbers.append(int(name))
        return sorted(numbers)

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
                f.write(canonical_json(event.model_dump()) + "\n")
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
        for number in self._segment_numbers_on_disk():
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_journal_writer.py -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/services/journal_writer.py tests/test_journal_writer.py
git commit -m "feat(journal): JSONL segment writer with CAS append, idempotent event ids, and hash chain"
```

---

### Task 6: JournalWriter — segment rolling + half-write tail recovery

**Files:**
- Modify: `floodmind/agent/runtime/services/journal_writer.py`
- Test: `tests/test_journal_writer.py`

**Interfaces:**
- Consumes: `JournalWriter` from Task 5.
- Produces:
  - `roll_segment()` renamed behavior: writes the current segment to a sealed name and opens the next segment (still `roll_segment()`).
  - `repair_tail()` — truncates a half-written final line so reads are clean.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_journal_writer.py`:

```python
def test_roll_segment_creates_sealed_files(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1", max_segment_bytes=1024)
    for i in range(1, 60):
        w.append(_evt(i))
    assert w.segment_count() > 1
    # sequence continuity across segments
    seqs = [e.sequence for e in w.read_from()]
    assert seqs == list(range(1, 60))


def test_repair_tail_truncates_half_write(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    w.append(_evt(1))
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    expected = seg.read_bytes()
    with seg.open("ab") as f:
        f.write(b'{"event_id": "evt_partial"')  # half-written line
    w.repair_tail()
    # Byte assertion proves the tail was truly removed and the full event kept.
    assert seg.read_bytes() == expected
    assert [e.sequence for e in w.read_from()] == [1]


def test_repair_tail_clears_segment_with_only_half_write(tmp_path: Path):
    w = JournalWriter(tmp_path, "run_1")
    seg = tmp_path / "runs" / "run_1" / "journal" / "events-000001.jsonl"
    with seg.open("ab") as f:
        f.write(b'{"event_id": "evt_partial"')  # only content is a half-write
    w.repair_tail()
    assert seg.read_bytes() == b""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_journal_writer.py -p no:cacheprovider`
Expected: `test_roll_segment_creates_sealed_files` PASSES already (Task 5's reconciled reader spans rolled segments) and `test_repair_tail_truncates_half_write` FAILS (`AttributeError: 'JournalWriter' object has no attribute 'repair_tail'`).

- [ ] **Step 3: Implement sealing + tail repair**

In `floodmind/agent/runtime/services/journal_writer.py`, replace `roll_segment` and add `repair_tail`:

```python
    def roll_segment(self) -> None:
        # Seal the current segment by recording the next number in index; a fresh
        # segment file is created lazily on next append.
        self._current_segment += 1
        self._save_index()

    def repair_tail(self) -> None:
        """Truncate a half-written tail in the current segment.

        Binary mode is required: in text mode ``f.tell()`` returns an opaque
        cookie that is not a byte offset, so a ``seek`` from a second file
        object is unreliable. Binary mode gives exact byte offsets. A segment
        whose only content is a half-written first line is truncated to empty.
        """
        path = self._segment_path(self._current_segment)
        if not path.exists():
            return
        last_full = 0
        corrupt_tail = False
        with path.open("rb") as f:
            for raw in f:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    EventEnvelope.model_validate_json(line)
                    last_full = f.tell()
                except Exception:
                    corrupt_tail = True
                    break
        if corrupt_tail:
            with path.open("r+b") as f:
                f.seek(last_full)
                f.truncate()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_journal_writer.py -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/services/journal_writer.py tests/test_journal_writer.py
git commit -m "feat(journal): sealed segment rolling and half-write tail repair"
```

---

### Task 7: ToolTransaction + canonical fingerprints

**Files:**
- Create: `floodmind/agent/runtime/contracts/transactions.py`
- Test: `tests/test_transactions.py`

**Interfaces:**
- Consumes: `canonical_json` from Task 4 (`floodmind.agent.runtime.contracts.canonical_events`).
- Produces:
  - `class ToolStatus(str, Enum)`: `proposed, validated, approval_required, approved, denied, running, succeeded, failed, cancelled, indeterminate`.
  - `class SideEffectClass(str, Enum)`: `read, reversible_write, irreversible, external`.
  - `class ToolTransaction(BaseModel)` fields per §6.1.
  - `canonical_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]`
  - `arguments_sha256(tool_id, tool_version, arguments) -> str`
  - `compute_fingerprint(parts: Dict[str, str]) -> str`

- [ ] **Step 1: Write the failing test**

`tests/test_transactions.py`:

```python
from floodmind.agent.runtime.contracts.transactions import (
    ToolStatus, ToolTransaction, arguments_sha256, canonical_arguments,
    compute_fingerprint,
)


def test_canonical_arguments_are_key_sorted():
    assert canonical_arguments({"b": 2, "a": 1}) == {"a": 1, "b": 2}


def test_arguments_sha256_is_deterministic_and_sensitive_to_tool_id():
    a = arguments_sha256("Write", "1", {"file_path": "/x", "content": "hi"})
    b = arguments_sha256("Write", "1", {"content": "hi", "file_path": "/x"})
    assert a == b
    c = arguments_sha256("Read", "1", {"file_path": "/x", "content": "hi"})
    assert a != c


def test_fingerprint_changes_when_any_part_changes():
    f1 = compute_fingerprint({"tool": "Write", "targets": "/a"})
    f2 = compute_fingerprint({"tool": "Write", "targets": "/b"})
    assert f1 != f2


def test_transaction_default_status():
    t = ToolTransaction(transaction_id="ttx_1", call_id="call_1", tool_id="builtin:Read")
    assert t.status == ToolStatus.proposed
    assert t.side_effect_class.value == "read"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_transactions.py -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/contracts/transactions.py`:

```python
"""Tool Transaction contract (target §6)."""

import hashlib
import json
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from floodmind.agent.runtime.contracts.canonical_events import canonical_json


def canonical_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(canonical_json(arguments))


def arguments_sha256(tool_id: str, tool_version: str, arguments: Dict[str, Any]) -> str:
    digest = canonical_json({
        "tool_id": tool_id,
        "tool_version": tool_version,
        "arguments": canonical_arguments(arguments),
    })
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()


def compute_fingerprint(parts: Dict[str, str]) -> str:
    digest = canonical_json(parts)
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()


class SideEffectClass(str, Enum):
    read = "read"
    reversible_write = "reversible_write"
    irreversible = "irreversible"
    external = "external"


class ToolStatus(str, Enum):
    proposed = "proposed"
    validated = "validated"
    approval_required = "approval_required"
    approved = "approved"
    denied = "denied"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    indeterminate = "indeterminate"


class ToolTransaction(BaseModel):
    transaction_id: str
    call_id: str
    tool_id: str
    tool_version: str = "1"
    canonical_arguments: Dict[str, Any] = Field(default_factory=dict)
    arguments_sha256: str = ""
    workspace_fingerprint: str = ""
    runtime_fingerprint: str = ""
    permission_fingerprint: str = ""
    idempotency_key: str = ""
    side_effect_class: SideEffectClass = SideEffectClass.read
    status: ToolStatus = ToolStatus.proposed
    preconditions: List[str] = Field(default_factory=list)
    result_ref: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_transactions.py -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/contracts/transactions.py tests/test_transactions.py
git commit -m "feat(contracts): tool transaction, canonical arguments, sha256 fingerprints"
```

---

### Task 8: Immutable RuntimeContext

**Files:**
- Create: `floodmind/agent/runtime/contracts/runtime_context.py`
- Test: `tests/test_runtime_context.py`

**Interfaces:**
- Consumes: nothing (stdlib dataclass).
- Produces: `@dataclass(frozen=True) class RuntimeContext` with §11.1 fields; services typed as `Any` to keep the contract import-light.

- [ ] **Step 1: Write the failing test**

`tests/test_runtime_context.py`:

```python
import dataclasses
import pytest

from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext


def _ctx() -> RuntimeContext:
    return RuntimeContext(
        conversation_id="conv_1", task_id="task_1", run_id="run_1",
        thread_id="thread_1", turn_id="turn_1",
    )


def test_frozen_rejects_mutation():
    ctx = _ctx()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.agent_tier = "sub"  # type: ignore[misc]


def test_identity_fields_default_services_to_none():
    ctx = _ctx()
    assert ctx.permission_service is None
    assert ctx.runtime_mode == "execution"


def test_requires_identity_fields():
    with pytest.raises(TypeError):
        RuntimeContext()  # type: ignore[call-arg]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_runtime_context.py -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/contracts/runtime_context.py`:

```python
"""Immutable RuntimeContext (target §11.1)."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class RuntimeContext:
    """Frozen per-run context injected into every tool/model operation.

    Service fields are typed as Any to keep this contract import-light; P2 rewires
    concrete services into these slots and removes ContextVar global getters.
    """

    conversation_id: str
    task_id: str
    run_id: str
    thread_id: str
    turn_id: str
    actor_type: str = "system"
    actor_id: str = ""
    agent_tier: str = "main"
    runtime_mode: str = "execution"
    workspace_id: str = ""
    workspace_generation: str = ""
    sandbox_id: str = ""
    permission_service: Any = None
    path_service: Any = None
    background_service: Any = None
    artifact_service: Any = None
    cancellation: Any = None
    deadline: Any = None
    environment_id: str = ""
    policy_version: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_runtime_context.py -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/contracts/runtime_context.py tests/test_runtime_context.py
git commit -m "feat(contracts): immutable RuntimeContext"
```

---

### Task 9: Artifact Manifest

**Files:**
- Create: `floodmind/agent/runtime/contracts/artifacts.py`
- Test: `tests/test_artifacts.py`

**Interfaces:**
- Consumes: `utcnow` re-exported locally (import from `canonical_events` to avoid duplication).
- Produces: `class ArtifactManifest(BaseModel)` per §15.1.

- [ ] **Step 1: Write the failing test**

`tests/test_artifacts.py`:

```python
from floodmind.agent.runtime.contracts.artifacts import ArtifactManifest


def test_manifest_defaults():
    m = ArtifactManifest(artifact_id="art_1", content_sha256="abc",
                         storage_uri="file:///a.json")
    assert m.media_type == "application/octet-stream"
    assert m.verified is False
    assert m.supersedes is None


def test_manifest_required_fields():
    m = ArtifactManifest(artifact_id="art_2", content_sha256="def",
                         storage_uri="file:///b.json", logical_name="forecast.json")
    assert m.logical_name == "forecast.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_artifacts.py -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/contracts/artifacts.py`:

```python
"""Artifact Manifest (target §15)."""

from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from floodmind.agent.runtime.contracts.canonical_events import utcnow


class ArtifactManifest(BaseModel):
    artifact_id: str
    content_sha256: str
    media_type: str = "application/octet-stream"
    size: int = 0
    storage_uri: str
    logical_name: str = ""
    producer_call_id: str = ""
    producer_thread_id: str = ""
    workspace_generation: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    sensitivity: str = "internal"
    verified: bool = False
    supersedes: Optional[str] = None
    retention: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_artifacts.py -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/contracts/artifacts.py tests/test_artifacts.py
git commit -m "feat(contracts): artifact manifest"
```

---

### Task 10: Context Projection Manifest

**Files:**
- Create: `floodmind/agent/runtime/contracts/context_manifest.py`
- Test: `tests/test_context_manifest.py`

**Interfaces:**
- Consumes: nothing extra.
- Produces:
  - `class ProjectionSource(BaseModel)` per §9.2 `sources[]`.
  - `class ProjectionManifest(BaseModel)` per §9.2.
  - `projection_sha256(manifest) -> str` (hashes the whole manifest body).

- [ ] **Step 1: Write the failing test**

`tests/test_context_manifest.py`:

```python
from floodmind.agent.runtime.contracts.context_manifest import (
    ProjectionManifest, ProjectionSource, projection_sha256,
)


def test_manifest_roundtrip_and_hash_deterministic():
    m1 = ProjectionManifest(
        projection_id="ctx_1", model_call_id="call_1",
        sources=[ProjectionSource(source_id="s1", source_type="agents",
                                  content_sha256="a", original_tokens=10,
                                  projected_tokens=10)],
    )
    m2 = ProjectionManifest.model_validate_json(m1.model_dump_json())
    assert projection_sha256(m1) == projection_sha256(m2)
    assert len(projection_sha256(m1)) == 64


def test_budget_fields():
    m = ProjectionManifest(projection_id="ctx_2", model_call_id="call_2")
    assert m.total_projected_tokens == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_context_manifest.py -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/contracts/context_manifest.py`:

```python
"""Context Projection Manifest (target §9.2)."""

import hashlib
from typing import List
from pydantic import BaseModel, Field

from floodmind.agent.runtime.contracts.canonical_events import canonical_json


class ProjectionSource(BaseModel):
    source_id: str
    source_type: str = "episode"
    content_sha256: str = ""
    original_tokens: int = 0
    projected_tokens: int = 0
    transform: str = "identity"
    priority: int = 0
    selected: bool = True


class ProjectionManifest(BaseModel):
    projection_id: str
    model_call_id: str
    projection_version: str = "1"
    model: str = ""
    codec_version: str = ""
    capability_snapshot_id: str = ""
    sources: List[ProjectionSource] = Field(default_factory=list)
    tool_registry_version: str = ""
    skill_catalog_version: str = ""
    total_projected_tokens: int = 0
    projection_sha256: str = ""


def projection_sha256(manifest: ProjectionManifest) -> str:
    return hashlib.sha256(canonical_json(manifest.model_dump()).encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_context_manifest.py -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/contracts/context_manifest.py tests/test_context_manifest.py
git commit -m "feat(contracts): context projection manifest"
```

---

### Task 11: Canonical Model Part events

**Files:**
- Create: `floodmind/agent/runtime/contracts/parts.py`
- Test: `tests/test_parts.py`

**Interfaces:**
- Consumes: nothing extra.
- Produces:
  - `class PartType(str, Enum)` per §7.5.
  - `PART_EVENT_TYPES = ("response_start", "part_start", "text_delta", "reasoning_delta", "tool_call_delta", "part_end", "usage", "response_end", "error")`.
  - `class PartStartEvent(BaseModel)`: `type: Literal["part_start"] = "part_start"`, `part_id: str`, `part_type: PartType`.
  - `class PartDeltaEvent(BaseModel)`: `type: Literal["part_delta"] = "part_delta"`, `part_id: str`, `text: str = ""`.
  - `class PartEndEvent(BaseModel)`: `type: Literal["part_end"] = "part_end"`, `part_id: str`.
  - `class UsageRecorded(BaseModel)`: `type: Literal["usage"] = "usage"`, `prompt_tokens/completion_tokens/total_tokens: int = 0`.
  - `class ResponseEndEvent(BaseModel)`: `type: Literal["response_end"] = "response_end"`, `terminal_reason_code: str`, `terminal_reason_raw: str = ""`.

- [ ] **Step 1: Write the failing test**

`tests/test_parts.py`:

```python
from floodmind.agent.runtime.contracts.parts import (
    PART_EVENT_TYPES, PartDeltaEvent, PartEndEvent, PartStartEvent,
    PartType, ResponseEndEvent, UsageRecorded,
)


def test_part_event_names():
    for name in ("response_start", "part_start", "text_delta", "reasoning_delta",
                 "tool_call_delta", "part_end", "usage", "response_end", "error"):
        assert name in PART_EVENT_TYPES


def test_part_start_carries_part_type():
    e = PartStartEvent(part_id="p1", part_type=PartType.tool_call)
    assert e.type == "part_start"
    assert e.part_type == PartType.tool_call


def test_delta_and_end():
    assert PartDeltaEvent(part_id="p1", text="hi").text == "hi"
    assert PartEndEvent(part_id="p1").type == "part_end"


def test_usage_and_response_end():
    u = UsageRecorded(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    assert u.total_tokens == 3
    r = ResponseEndEvent(terminal_reason_code="max_tokens", terminal_reason_raw="length")
    assert r.terminal_reason_raw == "length"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_parts.py -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/contracts/parts.py`:

```python
"""Canonical model part events (target §7.5)."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel


class PartType(str, Enum):
    text = "text"
    reasoning_summary = "reasoning_summary"
    provider_reasoning = "provider_reasoning"
    tool_call = "tool_call"
    refusal = "refusal"
    compaction = "compaction"
    provider_extension = "provider_extension"


PART_EVENT_TYPES = (
    "response_start", "part_start", "text_delta", "reasoning_delta",
    "tool_call_delta", "part_end", "usage", "response_end", "error",
)


class PartStartEvent(BaseModel):
    type: Literal["part_start"] = "part_start"
    part_id: str
    part_type: PartType


class PartDeltaEvent(BaseModel):
    type: Literal["part_delta"] = "part_delta"
    part_id: str
    text: str = ""


class PartEndEvent(BaseModel):
    type: Literal["part_end"] = "part_end"
    part_id: str


class UsageRecorded(BaseModel):
    type: Literal["usage"] = "usage"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ResponseEndEvent(BaseModel):
    type: Literal["response_end"] = "response_end"
    terminal_reason_code: str
    terminal_reason_raw: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_parts.py -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/contracts/parts.py tests/test_parts.py
git commit -m "feat(contracts): canonical model part events"
```

---

### Task 12: ChildThread contract + Typed SubAgent Result

**Files:**
- Create: `floodmind/agent/runtime/contracts/child_thread.py`
- Test: `tests/test_child_thread.py`

**Interfaces:**
- Consumes: nothing extra.
- Produces:
  - `class ChildThread(BaseModel)` per §13.1.
  - `class SubagentEventType(str, Enum)`: `accepted, running, result, failed, cancelled`.
  - `class SubagentResult(BaseModel)`: `thread_id`, `parent_call_id`, `event_type: SubagentEventType`, `summary: str = ""`, `artifact_ids: List[str]`, `tool_result_summaries: List[Dict[str, Any]]`, `needs_human: bool = False`.

- [ ] **Step 1: Write the failing test**

`tests/test_child_thread.py`:

```python
from floodmind.agent.runtime.contracts.child_thread import (
    ChildThread, SubagentEventType, SubagentResult,
)


def test_child_thread_defaults():
    t = ChildThread(thread_id="thread_1", parent_thread_id="thread_0",
                    parent_call_id="call_1")
    assert t.tool_allowlist == []
    assert t.max_turns == 50


def test_subagent_result_typed_handoff():
    r = SubagentResult(thread_id="thread_1", parent_call_id="call_1",
                       event_type=SubagentEventType.result, summary="done",
                       artifact_ids=["art_1"])
    assert r.event_type == SubagentEventType.result
    assert r.needs_human is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_child_thread.py -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`floodmind/agent/runtime/contracts/child_thread.py`:

```python
"""Child Thread contract and typed subagent result (target §13)."""

from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class ChildThread(BaseModel):
    thread_id: str
    parent_thread_id: str
    parent_call_id: str
    workspace_snapshot_id: str = ""
    sandbox_id: str = ""
    tool_allowlist: List[str] = Field(default_factory=list)
    max_turns: int = 50
    max_tokens: int = 32768
    wall_clock_budget_seconds: float = 300.0


class SubagentEventType(str, Enum):
    accepted = "accepted"
    running = "running"
    result = "result"
    failed = "failed"
    cancelled = "cancelled"


class SubagentResult(BaseModel):
    thread_id: str
    parent_call_id: str
    event_type: SubagentEventType
    summary: str = ""
    artifact_ids: List[str] = Field(default_factory=list)
    tool_result_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    needs_human: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_child_thread.py -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add floodmind/agent/runtime/contracts/child_thread.py tests/test_child_thread.py
git commit -m "feat(contracts): child thread contract and typed subagent result"
```

---

### Task 13: Full baseline verification

**Files:**
- None (verification only).

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: all pass (846 + new contract tests), no `--deselect` needed (Task 1 removed the recursion), and the previously failing background-subscriber test passes (Task 2).

- [ ] **Step 2: Commit any stray artifacts**

```bash
git status --short
```
Expected: only intended files. If the recursive test's `__pycache__` or temp dirs are tracked, do not commit them.
