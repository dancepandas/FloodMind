"""Canonical JSONL segment journal writer with CAS and hash chain (target §4.2).

The JSONL segments are the authoritative journal. index.json is a fast-forward
accelerator that is reconciled against the segments on every load, so a crash
between a segment append and an index save can never re-use a sequence or hash.
Appends take a cross-platform file lock so concurrent writer instances against
the same run observe each other's tail and raise on CAS conflicts.

Group commits (append_many) are a single write under the file lock — atomic
w.r.t. concurrent writers, but NOT filesystem-level all-or-none atomicity.
On an OS-level write/flush/fsync failure the segment is repaired to the last
full line (repair_tail) and reconciled before re-raising: a clean committed
prefix may remain, a torn tail never does. Callers must re-read after a failed
group append and retry only the uncommitted remainder.
"""

import hashlib
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from floodmind.agent.runtime.contracts.canonical_events import (
    EventEnvelope, canonical_json, canonical_payload_sha256,
)
from floodmind.common.filelock import FileLock

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover - Windows uses msvcrt
    fcntl = None

logger = logging.getLogger(__name__)


class JournalWriteConflict(Exception):
    """expected_last_sequence did not match the on-disk tail."""


class JournalMidFileCorruption(Exception):
    """坏行之后仍存在完整合法行——拒绝截断（防 repair_tail 销毁合法事件）。"""


def _iter_segment_lines(path: Path) -> Iterator[str]:
    """按行二进制读取 segment 并逐行解码（D05）。

    ``read_text(encoding="utf-8")`` 对整个文件解码，多字节字符被撕裂行切断时
    会抛 UnicodeDecodeError 令整个 journal 不可打开。按行解码 + errors=replace
    把损坏限制在坏行本身。
    """
    with path.open("rb") as f:
        for raw in f:
            yield raw.decode("utf-8", errors="replace")


_SEGMENT_PREFIX = "events-"
_SEGMENT_SUFFIX = ".jsonl"


def _segment_name(number: int) -> str:
    return f"{_SEGMENT_PREFIX}{number:06d}{_SEGMENT_SUFFIX}"


def segment_files(journal_dir: Path) -> List[Path]:
    """Return numeric journal segments in authoritative read order."""
    journal_dir = Path(journal_dir)
    numbered: List[tuple[int, Path]] = []
    if journal_dir.exists():
        for path in journal_dir.glob(f"{_SEGMENT_PREFIX}*{_SEGMENT_SUFFIX}"):
            suffix = path.name[len(_SEGMENT_PREFIX):-len(_SEGMENT_SUFFIX)]
            if suffix.isdigit():
                numbered.append((int(suffix), path))
    return [path for _, path in sorted(numbered)]


def _hash_input(event: EventEnvelope) -> str:
    d = event.model_dump()
    d["integrity"] = {}
    return canonical_json(d)


def _retry_hash(event: EventEnvelope) -> str:
    """Content hash used to prove a whole-group retry.

    Ignores journal-assigned fields (sequence, recorded_at, integrity) so a
    caller re-sending the same logical events — even freshly reconstructed —
    matches the sealed envelopes on event_type, payload, and identity fields.
    """
    d = event.model_dump()
    d["sequence"] = 0
    d["recorded_at"] = ""
    d["integrity"] = {}
    return canonical_json(d)


class JournalWriter:
    def __init__(
        self,
        base_dir: Path,
        run_id: str,
        *,
        max_segment_bytes: int = 10 * 1024 * 1024,
        journal_dir: Optional[Path] = None,
    ):
        if not run_id or run_id in {".", ".."} or ".." in run_id or Path(run_id).name != run_id:
            raise ValueError(f"unsafe run_id: {run_id!r}")
        self._base_dir = Path(base_dir)
        self._run_id = run_id
        self._max_segment_bytes = max_segment_bytes
        self._journal_dir = (
            Path(journal_dir)
            if journal_dir is not None
            else self._base_dir / "runs" / run_id / "journal"
        )
        self._journal_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._journal_dir / ".lock"
        self._file_lock = FileLock(self._lock_path, timeout=30.0)
        self._index_path = self._journal_dir / "index.json"
        self._sealed: Dict[str, EventEnvelope] = {}
        self._load_index()

    # ── lock ─────────────────────────────────────────────────────

    @contextmanager
    def _locked(self) -> Iterator[None]:
        # 统一走跨平台 FileLock（Windows msvcrt 重试直至超时，不再 10 秒抛裸 OSError）。
        # 复用实例：FileLock 的可重入保护是 thread-local 的，逐次新建会使重入保护失效。
        with self._file_lock:
            yield

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
            for line in _iter_segment_lines(path):
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
        return [
            int(path.name[len(_SEGMENT_PREFIX):-len(_SEGMENT_SUFFIX)])
            for path in segment_files(self._journal_dir)
        ]

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

    def _ensure_clean_tail_locked(self) -> None:
        """P1-1：追加前确保当前 segment 尾部无撕裂坏行。

        append 以 "a" 模式写在文件物理 EOF——若尾部存在撕裂坏行而未处理，新事件会
        落在坏行之后：本次 fsync 成功且内存序列推进，但重启后 reconcile 在坏行处
        break，新事件永久不可读，且下次 append 复用同一序列号、哈希链断链。
        此处主动 repair_tail：D04 已保证只截断真正的撕裂尾（中部损坏会抛
        JournalMidFileCorruption 拒绝修复，此时 append 一并失败，fail-closed）。
        """
        path = self._segment_path(self._current_segment)
        if not path.exists() or path.stat().st_size == 0:
            return
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            window = min(size, 262144)
            f.seek(size - window)
            tail = f.read()
        raw_lines = tail.split(b"\n")
        last = raw_lines[-1] if raw_lines else b""
        line = last.decode("utf-8", errors="replace").strip()
        if not line:
            return  # 以换行结尾 → 无撕裂尾
        try:
            EventEnvelope.model_validate_json(line)
        except Exception:
            # 末行无法解析 → 撕裂尾。repair_tail 全量扫描兜底，只在安全时截断。
            self.repair_tail()
            self._reconcile_from_journal()

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
            self._ensure_clean_tail_locked()
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
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(canonical_json(event.model_dump()) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except Exception:
                # 与 append_many 相同的恢复契约：截断撕裂尾、恢复磁盘权威尾再抛出。
                try:
                    self.repair_tail()
                    self._reconcile_from_journal()
                except Exception as repair_exc:
                    logger.error("append 失败后 repair_tail 也失败: %s", repair_exc)
                raise

            self._last_sequence = event.sequence
            self._last_event_sha256 = event.integrity.event_sha256
            self._sealed[event.event_id] = event

            if path.stat().st_size > self._max_segment_bytes:
                self.roll_segment()
            else:
                self._save_index()
            return event

    def append_many(
        self,
        events: List[EventEnvelope],
        *,
        expected_last_sequence: Optional[int] = None,
    ) -> List[EventEnvelope]:
        """原子追加一组事件：单次锁、单次 CAS、sequence 连续、哈希链连续、单次写 + 单次 fsync。

        三种情形精确区分：
        - 全新组：无任何 id 已封存 → CAS 校验后整体写入；
        - 整组重试：全部 id 已封存、内容语义一致、且封存序列连续（组身份由 journal 自身证明）→ 原样返回；
        - 其余（部分重叠 / 内容不符 / 从无关组拼装出非连续序列）→ ValueError，绝不静默当作幂等。

        恢复契约（不承诺文件系统级 all-or-none 原子性）：
        - 整组在文件锁内以单次 write 提交，对并发写者原子；
        - 若 OS 级 write/flush/fsync 失败，本方法会 repair_tail()（截断到最后一个完整行）并
          _reconcile_from_journal() 后再抛出——可能残留干净的已提交前缀，但绝不残留撕裂行；
        - 调用方失败后应通过 read_from(after_sequence=cursor) 重读对齐，仅重试未提交的余量
          （partial-overlap 的 ValueError 正是触发该重试的信号）。
        """
        if not events:
            return []
        with self._locked():
            self._reconcile_from_journal()
            self._ensure_clean_tail_locked()
            # 组内 event_id 不得重复
            seen_ids = set()
            for event in events:
                if event.event_id in seen_ids:
                    raise ValueError(f"append_many: duplicate event_id in group: {event.event_id!r}")
                seen_ids.add(event.event_id)

            sealed_ids = [e.event_id for e in events if e.event_id in self._sealed]
            if len(sealed_ids) == len(events):
                # 全部已封存：内容语义一致 + 封存序列连续 = 整组重试，否则拒绝。
                for event in events:
                    sealed = self._sealed[event.event_id]
                    if _retry_hash(sealed) != _retry_hash(event):
                        raise ValueError("append_many: partial or mismatched group retry")
                sorted_seqs = sorted(
                    self._sealed[e.event_id].sequence for e in events
                )
                if sorted_seqs != list(range(sorted_seqs[0], sorted_seqs[0] + len(sorted_seqs))):
                    raise ValueError("append_many: partial or mismatched group retry")
                return [self._sealed[e.event_id] for e in events]
            if sealed_ids:
                raise ValueError("append_many: partial or mismatched group retry")

            # 全新组：先 CAS，再在本地副本上构造，单次写落盘成功后才发布到内存权威状态。
            if expected_last_sequence is not None and expected_last_sequence != self._last_sequence:
                raise JournalWriteConflict(
                    f"expected last sequence {expected_last_sequence}, got {self._last_sequence}"
                )
            sealed_group: List[EventEnvelope] = []
            seq = self._last_sequence
            last_hash = self._last_event_sha256
            for event in events:
                copy = event.model_copy(deep=True)
                seq += 1
                copy.sequence = seq
                copy.integrity.payload_sha256 = canonical_payload_sha256(copy.payload)
                copy.integrity.previous_event_sha256 = last_hash
                copy.integrity.event_sha256 = hashlib.sha256(
                    f"{last_hash}|{_hash_input(copy)}".encode("utf-8")
                ).hexdigest()
                last_hash = copy.integrity.event_sha256
                sealed_group.append(copy)
            group_bytes = "".join(
                canonical_json(e.model_dump()) + "\n" for e in sealed_group
            ).encode("utf-8")
            path = self._segment_path(self._current_segment)
            try:
                with path.open("ab") as f:
                    f.write(group_bytes)
                    f.flush()
                    os.fsync(f.fileno())
                should_roll = path.stat().st_size > self._max_segment_bytes
            except Exception:
                # 截断撕裂行、恢复磁盘权威尾，再抛出；实例保持可用。
                self.repair_tail()
                self._reconcile_from_journal()
                raise
            # 落盘成功后才推进内存权威状态。
            self._last_sequence = sealed_group[-1].sequence
            self._last_event_sha256 = sealed_group[-1].integrity.event_sha256
            for copy in sealed_group:
                self._sealed[copy.event_id] = copy
            if should_roll:
                self.roll_segment()
            else:
                self._save_index()
            return sealed_group

    def roll_segment(self) -> None:
        self._current_segment += 1
        self._save_index()

    def repair_tail(self) -> None:
        """Truncate a half-written tail in the current segment.

        Binary mode is required: in text mode ``f.tell()`` returns an opaque
        cookie that is not a byte offset, so a ``seek`` from a second file
        object is unreliable. Binary mode gives exact byte offsets. A segment
        whose only content is a half-written first line is truncated to empty.

        安全截断（D04）：只允许截断"最后一个完整合法行之后、且直到 EOF 不再出现
        任何完整合法行"的内容——即撕裂尾部。若坏行之后仍有合法事件（中部损坏），
        抛出 JournalMidFileCorruption，绝不静默截掉合法数据。
        """
        path = self._segment_path(self._current_segment)
        if not path.exists():
            return
        last_full = 0
        first_bad_offset: Optional[int] = None
        valid_after_bad = False
        offset = 0
        with path.open("rb") as f:
            for raw in f:
                line_start = offset
                offset += len(raw)
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    EventEnvelope.model_validate_json(line)
                    last_full = line_start + len(raw)
                    if first_bad_offset is not None:
                        valid_after_bad = True
                except Exception:
                    if first_bad_offset is None:
                        first_bad_offset = line_start
        if first_bad_offset is None:
            return
        if valid_after_bad:
            # 中部损坏：后面还有合法事件，截断会销毁数据——拒绝修复并上报。
            raise JournalMidFileCorruption(
                f"journal segment {path} 存在中部损坏（坏行之后仍有合法事件），"
                "已拒绝 repair_tail 截断以保护数据；请人工修复该 segment"
            )
        with path.open("r+b") as f:
            f.seek(last_full)
            f.truncate()

    def read_from(self, after_sequence: int = 0) -> List[EventEnvelope]:
        """读取 after_sequence 之后的事件，按 sequence 排序。

        坏行语义与 _reconcile_from_journal 一致（D14 统一）：一个 segment 内
        遇到无法解析的行即视为该 segment 的尾部截断点（撕裂尾/损坏行之后的
        内容不参与重放），后续 segment 仍正常读取。按行二进制解码，多字节
        撕裂只影响坏行本身，不会让整个 segment 不可读（D05）。
        """
        events: List[EventEnvelope] = []
        for number in self._segment_numbers_on_disk():
            path = self._segment_path(number)
            if not path.exists():
                continue
            for line in _iter_segment_lines(path):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = EventEnvelope.model_validate_json(line)
                except Exception:
                    break  # 与 reconcile 一致：坏行即该 segment 尾部
                if event.sequence > after_sequence:
                    events.append(event)
        events.sort(key=lambda e: e.sequence)
        return events
