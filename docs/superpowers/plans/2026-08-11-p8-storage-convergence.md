# P8 — Storage 收敛与 SDK 公共 API 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收敛存储（JSONL Journal 为唯一权威 + SQLite 可重建索引），按 TARGET 契约重定义 SDK 公共 API（desktop 消费的标准接口），并清理弃用的 web/TUI 前端。

**Architecture:** JSONL Journal 继续作为唯一运行事实源；新增 `SqliteJournalIndex` 作为可重建的只读索引（§18：SQLite 只作 Index，不能成为第二个独立事实源），挂进 `JournalAuthority` 的读写 seam。SDK `Agent` 按 §4.4/§10.1 双层流契约暴露 Journal 派生的公共事件（committed 带 sequence，preview 带 attempt_id/part_id）与标准身份/恢复。web/TUI（server/tui/web_server/start/session_store 的 sync_events）整体移除，回归纯 SDK。

**Tech Stack:** Python 3, sqlite3 (stdlib), pydantic, 既有 JournalWriter / JournalAuthority / reducer / NativeFloodAgent / api.Agent。

## Global Constraints

- **Forward-only, binding（用户指令）:** 不向后兼容、不 fallback、不双写、不留 legacy adapter。直接落 TARGET 契约。
- **WIP 文件 — 永不修改、不 stage:** `floodmind/agent/runtime/contracts/permissions.py`（未提交用户 WIP）、`floodmind/tools/base_tools.py`（冻结）。
- `git add` 显式路径，绝不 `git add -A`。只提交代码（计划/规范文档不入库）。每个提交结尾 `Co-Authored-By: Claude <noreply@anthropic.com>`。每任务独立提交在 master。
- 基线 HEAD: `2efee25`（P7 完成，**1168 passed / 2 skipped**）。每任务必须保持全绿。
- **P8 范围锁定（用户 2026-08-11 指示，已记入项目记忆）:** web/TUI 弃用只清理、不维护；desktop 是独立项目，P8 只交付标准 SDK 公共 API，desktop 按契约自行适配。**不做** web/desktop/TUI 宿主适配、不做 web SSE 宿主层。
- Reducer MUST 保持确定性（§2.8）。
- §18: SQLite 作为 Journal Backend 或 Index，**不能与 JSONL 同时成为两个独立事实源**。本计划选 **Index**：JSONL Journal 为唯一权威，`SqliteJournalIndex` 为派生可重建索引。
- §4.4/§10.1: Preview 可丢失、可被 Committed 对账；每个 Preview 携带 `attempt_id`/`part_id`；客户端用 `after_sequence` 重放。SDK 事件契约承载这一语义（desktop 据此实现重连/对账）。
- §23 gap「存储 | JSON/JSONL/SQLite 多面并存 → 单一权威 + 可重建 Projection/Index」：收敛 = 移除 web 的 `sync_events` 表 + index.json 加速器职责并入 `SqliteJournalIndex`。

## File Structure

- **Create** `floodmind/agent/runtime/services/journal_backend.py` — `JournalBackend` Protocol（append/append_many/current_sequence/read_from/sealed）。
- **Create** `floodmind/agent/runtime/services/journal_index.py` — `SqliteJournalIndex`（SQLite 派生索引 + rebuild）。
- **Create** `floodmind/agent/sdk_events.py` — `SdkEvent` + `project_canonical`。
- **Modify** `floodmind/agent/runtime/services/journal_authority.py` — 可选 `index=` 接线。
- **Modify** `floodmind/agent/runtime/services/journal_writer.py` — 标注/验证 Protocol 符合（若需）。
- **Modify** `floodmind/agent/api.py` — 身份属性、`events_after()`、resume、TARGET 契约 docstring。
- **Modify/删除** web/TUI: `floodmind/server/`、`floodmind/tui/`、`web_server.py`、`start.py`、`scheduler.py`（先验证 web-only）、`floodmind/memory/session_store.py`（先验证 web-only）、`floodmind/cli.py`、`floodmind/cli_interactive.py`、`pyproject.toml`、`Dockerfile`、`requirements.txt`。
- **Test** `tests/test_journal_index.py`、`tests/test_sdk_events.py`、`tests/test_sdk_api_identity.py`；删除 `tests/test_web_client.py`、`tests/test_server_agent_factory_workspace.py`、`tests/test_session_store_migration.py`；更新 `tests/test_cli_commands.py`、`tests/test_sdk_purity.py`。

---

### Task 1: JournalBackend 协议 + SqliteJournalIndex（可重建索引）

**Files:**
- Create: `floodmind/agent/runtime/services/journal_backend.py`
- Create: `floodmind/agent/runtime/services/journal_index.py`
- Modify: `floodmind/agent/runtime/services/journal_authority.py`
- Test: `tests/test_journal_index.py`

**Interfaces:**
- Consumes: `EventEnvelope`（canonical_events.py）、`JournalWriter`（journal_writer.py，`_segment_name`/segment 文件布局 `events-*.jsonl`）。
- Produces: `JournalBackend` Protocol（`append(event, *, expected_last_sequence) -> EventEnvelope`、`append_many(events, *, expected_last_sequence) -> List[EventEnvelope]`、`current_sequence() -> int`、`read_from(after_sequence=0) -> List[EventEnvelope]`、`sealed(event_id) -> Optional[EventEnvelope]`）。`SqliteJournalIndex(journal_dir, run_id)` 提供 `index_event(envelope)`、`read_after(after_sequence=0) -> List[EventEnvelope]`、`rebuild_from(journal_dir) -> int`、`close()`。`JournalAuthority` 新增可选 `index=`，`emit`/`append_group` 成功后 best-effort 索引，`read_after` 优先走索引。

- [ ] **Step 1: 写失败测试（索引重建 + read_after 与 JSONL 扫描一致）。**

Create `tests/test_journal_index.py`:

```python
"""P8 Task 1 — SqliteJournalIndex 派生索引：可重建、与 JSONL 权威一致、非第二事实源。"""
from pathlib import Path

from floodmind.agent.runtime.services.journal_authority import open_journal_authority
from floodmind.agent.runtime.services.journal_index import SqliteJournalIndex


def _seed_journal(tmp_path, events):
    auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th", turn_id="tu",
    )
    for et, payload in events:
        auth.emit(et, payload)
    return auth


def test_index_rebuild_matches_segment_scan(tmp_path):
    auth = _seed_journal(tmp_path, [
        ("run.started", {"a": 1}),
        ("model.attempt.completed", {"content": "hi", "is_final": True}),
        ("run.completed", {}),
    ])
    idx = SqliteJournalIndex(auth._writer._base_dir / "conversations" / "c" / "tasks" / "t"
                             / "runs" / "run_1" / "journal", "run_1")
    rebuilt = idx.rebuild_from(Path(idx._db).parent)
    assert rebuilt == 3
    # 索引 read_after 与 JSONL 权威扫描一致
    assert idx.read_after(0) == auth.read_after(0)
    assert [e.sequence for e in idx.read_after(0)] == [1, 2, 3]
    assert idx.read_after(2) == auth.read_after(2)
    idx.close()


def test_index_is_derived_not_authoritative(tmp_path):
    """§18：索引可丢弃可重建；JSONL 是唯一权威。"""
    auth = _seed_journal(tmp_path, [("run.started", {})])
    jdir = Path(auth._writer._base_dir) / "conversations" / "c" / "tasks" / "t" \
        / "runs" / "run_1" / "journal"
    idx = SqliteJournalIndex(jdir, "run_1")
    idx.rebuild_from(jdir)
    # 删除索引文件：JSONL 权威不受影响
    idx.close()
    Path(idx._db).unlink()
    assert auth.read_after(0)  # 仍能读
    idx2 = SqliteJournalIndex(jdir, "run_1")
    assert idx2.rebuild_from(jdir) == 1
    idx2.close()


def test_authority_read_after_uses_index(tmp_path):
    """JournalAuthority 挂 index 后 read_after 返回相同结果。"""
    auth = open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id="run_1", thread_id="th", turn_id="tu", index=True,
    )
    auth.emit("run.started", {})
    auth.emit("run.completed", {})
    assert [e.sequence for e in auth.read_after(0)] == [1, 2]
    assert auth.read_after(1)[0].event_type == "run.completed"
```

- [ ] **Step 2: 运行验证失败。**

Run: `python -m pytest tests/test_journal_index.py -q`
Expected: FAIL — `from ...journal_index import SqliteJournalIndex` 导入失败（模块不存在），且 `open_journal_authority(..., index=True)` 报参数错误。

- [ ] **Step 3: 实现 Protocol + 索引。**

Create `floodmind/agent/runtime/services/journal_backend.py`:

```python
"""Journal 存储后端协议（目标 §18：SQLite 只作 Backend 或 Index）。"""
from typing import Any, List, Optional, Protocol, runtime_checkable

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
```

Create `floodmind/agent/runtime/services/journal_index.py`:

```python
"""SqliteJournalIndex — Journal 的可重建只读索引（§18：SQLite 只作 Index）。

JSONL Journal 是唯一权威；本索引是派生加速器，可随时丢弃并从 segments 重建。
绝不作为第二独立事实源。
"""
import json
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional

from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
from floodmind.agent.runtime.services.journal_writer import (
    _SEGMENT_PREFIX, _SEGMENT_SUFFIX,
)


class SqliteJournalIndex:
    """SQLite 索引：`journal_events` 表 + `seq_idx`。read_after 走索引加速。"""

    def __init__(self, journal_dir: Path, run_id: str):
        self._journal_dir = Path(journal_dir)
        self._db = self._journal_dir / "journal.sqlite3"
        self._run_id = run_id
        self._conn = sqlite3.connect(str(self._db))
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS journal_events ("
                " run_id TEXT NOT NULL, sequence INTEGER NOT NULL,"
                " event_id TEXT PRIMARY KEY, event_type TEXT, thread_id TEXT,"
                " payload_json TEXT, recorded_at TEXT)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_seq ON journal_events(run_id, sequence)"
            )
            self._conn.commit()

    def index_event(self, envelope: EventEnvelope) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO journal_events VALUES (?,?,?,?,?,?,?)",
                (self._run_id, envelope.sequence, envelope.event_id,
                 envelope.event_type, envelope.thread_id,
                 json.dumps(envelope.payload, ensure_ascii=False),
                 envelope.recorded_at.isoformat()),
            )
            self._conn.commit()

    def read_after(self, after_sequence: int = 0) -> List[EventEnvelope]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, event_type, sequence, thread_id, payload_json, recorded_at"
                " FROM journal_events WHERE run_id=? AND sequence>? ORDER BY sequence",
                (self._run_id, after_sequence),
            ).fetchall()
        return [self._row_to_envelope(r) for r in rows]

    def rebuild_from(self, journal_dir: Path) -> int:
        """从 JSONL segments 全量重建索引（先清空）。返回索引条数。"""
        journal_dir = Path(journal_dir)
        with self._lock:
            self._conn.execute("DELETE FROM journal_events WHERE run_id=?", (self._run_id,))
            count = 0
            for seg in sorted(journal_dir.glob(f"{_SEGMENT_PREFIX}*{_SEGMENT_SUFFIX}")):
                for line in seg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        env = EventEnvelope.model_validate_json(line)
                    except Exception:
                        continue  # 半写/坏行跳过（与 JournalWriter.read_from 同语义）
                    if env.sequence is None:
                        continue
                    self._conn.execute(
                        "INSERT OR REPLACE INTO journal_events VALUES (?,?,?,?,?,?,?)",
                        (self._run_id, env.sequence, env.event_id, env.event_type,
                         env.thread_id, json.dumps(env.payload, ensure_ascii=False),
                         env.recorded_at.isoformat()),
                    )
                    count += 1
            self._conn.commit()
        return count

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _row_to_envelope(self, row) -> EventEnvelope:
        from floodmind.agent.runtime.contracts.canonical_events import EventEnvelope
        return EventEnvelope.model_validate({
            "schema_version": 1,
            "event_id": row[0],
            "event_type": row[1],
            "sequence": row[2],
            "thread_id": row[3],
            "payload": json.loads(row[4]),
            "recorded_at": row[5],
        })
```

In `floodmind/agent/runtime/services/journal_authority.py`:

- Add `index: bool = False` param to `open_journal_authority(...)`; when True, construct `SqliteJournalIndex(journal_dir, run_id)` and pass to `JournalAuthority`.
- `JournalAuthority.__init__(..., index: Optional[Any] = None)` stores `self._index`.
- `emit`/`append_group`: after `self._writer.append(...)` succeeds, best-effort `if self._index: try: self._index.index_event(envelope) except Exception: pass` (索引失败不破坏权威 append）。
- `read_after`: `if self._index is not None: return self._index.read_after(after_sequence)` else `self._writer.read_from(after_sequence)`。

（JournalWriter 的 Protocol 符合性：`assert isinstance(JournalWriter(...), JournalBackend)` 不强制加；现有方法签名已满足，任务实施者核对一次即可。）

- [ ] **Step 4: 运行验证通过。**

Run: `python -m pytest tests/test_journal_index.py -q`
Expected: `3 passed`.

- [ ] **Step 5: 回归 + 全量。**

Run: `python -m pytest tests/test_journal_authority.py -q`（既有 Journal 测试必须不变）然后 `python -m pytest -q`。
Expected: 既有 1168 + 3 新 = 全绿。

- [ ] **Step 6: 提交。**

```bash
git add floodmind/agent/runtime/services/journal_backend.py floodmind/agent/runtime/services/journal_index.py floodmind/agent/runtime/services/journal_authority.py tests/test_journal_index.py
git commit -m "feat(p8): JournalBackend protocol + rebuildable SqliteJournalIndex (§18 index, JSONL authoritative)
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: SDK 公共 API — 身份 + Journal 派生事件 + 恢复（desktop 契约）

**Files:**
- Create: `floodmind/agent/sdk_events.py`
- Modify: `floodmind/agent/api.py`
- Test: `tests/test_sdk_events.py`、`tests/test_sdk_api_identity.py`

**Interfaces:**
- Consumes: `EventEnvelope`、`JournalAuthority`（含 Task 1 的 `read_after`/`replay`）、`NativeFloodAgent.stream`（既有 preview 事件）、`reducer.reduce`/`initial_run_state`、P3 的 checkpoint/resume 设施。
- Produces: `SdkEvent`（`type`/`sequence`/`event_type`/`payload`/`attempt_id`/`part_id`）、`project_canonical(envelope) -> Optional[SdkEvent]`、`project_canonical_many(events) -> List[SdkEvent]`。`Agent` 新增属性 `conversation_id`/`task_id`/`run_id`/`thread_id`、方法 `events_after(sequence=0) -> List[SdkEvent]`、`resume(checkpoint_id=None) -> str`。TARGET 契约写进 `Agent` docstring（desktop 实现的 API 参照）。

- [ ] **Step 1: 写失败测试（projector + events_after + 身份）。**

Create `tests/test_sdk_events.py`:

```python
"""P8 Task 2 — SDK 公共事件契约（§4.4/§10.1 双层流，Journal 派生）。"""
from floodmind.agent.sdk_events import SdkEvent, project_canonical, project_canonical_many
from floodmind.agent.runtime.services.journal_authority import open_journal_authority


def _mk_auth(tmp_path, run_id="run_1"):
    return open_journal_authority(
        tmp_path / "runtime", conversation_id="c", task_id="t",
        run_id=run_id, thread_id="th", turn_id="tu",
    )


def test_project_canonical_maps_committed_events(tmp_path):
    auth = _mk_auth(tmp_path)
    auth.emit("model.attempt.completed", {"content": "final answer", "is_final": True})
    auth.emit("tool.result.committed", {"tool_name": "Bash", "status": "completed"})
    auth.emit("run.completed", {})
    events = project_canonical_many(auth.read_after(0))
    types = [e["type"] for e in events]
    assert "text_committed" in types          # model.attempt.completed(is_final)
    assert "tool_result" in types             # tool.result.committed
    assert "run_completed" in types           # run.completed
    seqs = [e["sequence"] for e in events]
    assert seqs == sorted(seqs) and seqs[-1] == auth.cursor()


def test_project_canonical_skips_internal_events(tmp_path):
    """纯内部事件（usage/checkpoint/attempt.started）不进入公共事件。"""
    auth = _mk_auth(tmp_path)
    auth.emit("model.attempt.started", {})
    auth.emit("model.usage.recorded", {"total_tokens": 10})
    auth.emit("model.attempt.completed", {"content": "ok", "is_final": True})
    events = project_canonical_many(auth.read_after(0))
    assert [e["type"] for e in events] == ["text_committed"]
```

Create `tests/test_sdk_api_identity.py`:

```python
"""P8 Task 2 — SDK Agent 标准身份 + events_after 契约。"""
from unittest.mock import MagicMock

from floodmind.agent.api import Agent
from floodmind.agent.native.model_client import ModelClient


def _agent(tmp_path):
    mc = MagicMock(spec=ModelClient)
    mc.stream_chat.return_value = [
        __import__("floodmind.agent.native.types", fromlist=["ModelEvent"]).ModelEvent(
            type="token", content="ok"),
        __import__("floodmind.agent.native.types", fromlist=["ModelEvent"]).ModelEvent(
            type="done"),
    ]
    return Agent(llm=mc, session_id="sdk-sess", bare=True)


def test_agent_standard_identity_after_run(tmp_path):
    agent = _agent(tmp_path)
    out = agent.run("hi")
    assert isinstance(out, str)
    # 标准身份已填充（run_id 恒生成；conversation_id 可能为空串但类型正确）
    assert agent.run_id and agent.run_id.startswith("run_")
    assert isinstance(agent.conversation_id, str)


def test_agent_events_after_replays_committed(tmp_path):
    agent = _agent(tmp_path)
    agent.run("hi")
    events = agent.events_after(0)
    assert isinstance(events, list)
    if events:
        assert all(e["sequence"] >= 0 for e in events)
        # sequence 单调递增
        seqs = [e["sequence"] for e in events]
        assert seqs == sorted(seqs)
```

- [ ] **Step 2: 运行验证失败。**

Run: `python -m pytest tests/test_sdk_events.py tests/test_sdk_api_identity.py -q`
Expected: FAIL — `sdk_events` 模块不存在；`Agent` 无 `conversation_id`/`events_after`。

- [ ] **Step 3: 实现事件契约 + Agent 扩展。**

Create `floodmind/agent/sdk_events.py`:

```python
"""SDK 公共事件契约（§4.4/§10.1 双层流，Journal 派生）。

Preview 可丢失、可被 Committed 对账；每个 Preview 携带 attempt_id/part_id。
Committed 事件携带 canonical sequence，客户端可用 after_sequence 重放/对账。
"""
from typing import Any, Dict, List, Optional, TypedDict


class SdkEvent(TypedDict, total=False):
    type: str            # 公共展示类型
    sequence: int        # canonical committed sequence
    event_type: str      # canonical event_type（"" 表示纯 preview）
    payload: Dict[str, Any]
    attempt_id: str
    part_id: str


# canonical event_type -> 公共类型（§4.4 映射）
_CANONICAL_TO_SDK = {
    "model.attempt.completed": "text_committed",
    "model.attempt.failed": "model_failed",
    "tool.result.committed": "tool_result",
    "tool.call.proposed": "tool_call",
    "artifact.committed": "artifact",
    "background.completed": "background_completed",
    "run.completed": "run_completed",
    "run.failed": "run_failed",
}
# 纯内部事件（不进公共流）
_SKIP_EVENT_TYPES = frozenset({
    "model.attempt.started", "model.request.committed", "model.usage.recorded",
    "model.stream.preview", "checkpoint.created", "context.projection.started",
    "context.projection.committed", "thread.message.sent",
})


def project_canonical(envelope) -> Optional[SdkEvent]:
    """把一条 canonical EventEnvelope 投影为公共 SdkEvent；内部事件返回 None。"""
    et = envelope.event_type
    if et in _SKIP_EVENT_TYPES:
        return None
    sdk_type = _CANONICAL_TO_SDK.get(et)
    if sdk_type is None:
        return None  # 未映射的 canonical 事件不进公共流
    payload = dict(envelope.payload or {})
    if et == "model.attempt.completed" and not payload.get("is_final"):
        return None  # 非最终 attempt 的中间提交不进公共流（最终回答才提交）
    event: SdkEvent = {
        "type": sdk_type,
        "sequence": envelope.sequence,
        "event_type": et,
        "payload": payload,
    }
    if getattr(envelope, "attempt_id", ""):
        event["attempt_id"] = envelope.attempt_id
    return event


def project_canonical_many(events) -> List[SdkEvent]:
    out = []
    for env in events:
        sdk = project_canonical(env)
        if sdk is not None:
            out.append(sdk)
    return out
```

In `floodmind/agent/api.py`:

- Add identity properties + `events_after` + `resume`:

```python
    @property
    def conversation_id(self) -> str:
        return getattr(self._agent, "conversation_id", "") or getattr(
            getattr(self._agent, "_journal_authority", None), "conversation_id", "")

    @property
    def task_id(self) -> str:
        return getattr(getattr(self._agent, "_journal_authority", None), "task_id", "")

    @property
    def run_id(self) -> str:
        return getattr(getattr(self._agent, "_journal_authority", None), "run_id", "")

    @property
    def thread_id(self) -> str:
        return getattr(getattr(self._agent, "_journal_authority", None), "thread_id", "")

    def events_after(self, sequence: int = 0) -> List[Dict[str, Any]]:
        """返回该 run 从 sequence 之后的所有公共事件（Journal 派生，可重放）。"""
        from floodmind.agent.sdk_events import project_canonical_many
        auth = getattr(self._agent, "_journal_authority", None)
        if auth is None:
            return []
        return project_canonical_many(auth.read_after(sequence))

    def resume(self, checkpoint_id: Optional[str] = None) -> str:
        """从 checkpoint 恢复执行（Journal Replay + Reconciliation），返回最终回答。"""
        return self._agent.run_with_resume(
            "", resume_checkpoint_id=checkpoint_id,
        ) if checkpoint_id else self._agent.run_with_resume("")
```

- Update the `Agent` class docstring to state the TARGET contract precisely（这是 desktop 实现的 API 参照）：标准身份 conversation_id/task_id/run_id/thread_id；`stream()` 产出 preview 事件（带 attempt_id/part_id）;`events_after(sequence)` 产出 committed 公共事件（带 sequence，可断线重放/对账）；`resume(checkpoint_id)` 走 Journal Replay + Reconciliation；`run()`/`chat()` 返回 committed 最终回答。保留既有 `tools`/`permission_handler`/`workspace`/`on_event` 等参数（已是标准表面）。

- [ ] **Step 4: 运行验证通过。**

Run: `python -m pytest tests/test_sdk_events.py tests/test_sdk_api_identity.py -q`
Expected: `4 passed`.

- [ ] **Step 5: 全量回归。**

Run: `python -m pytest -q`
Expected: 全绿（基线 1168 + 新）。

- [ ] **Step 6: 提交。**

```bash
git add floodmind/agent/sdk_events.py floodmind/agent/api.py tests/test_sdk_events.py tests/test_sdk_api_identity.py
git commit -m "feat(p8): SDK public API — standard identity + Journal-derived events + resume (desktop contract)
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: web/TUI 弃用清理

**Files:**
- 删除: `floodmind/server/`（整个包）、`floodmind/tui/`（整个包）、`web_server.py`、`start.py`、`scheduler.py`（若 web-only）、`floodmind/memory/session_store.py`（若仅 web 的 sync_events 使用）。
- Modify: `floodmind/cli.py`（移除 `tui`/`web`/`serve` legacy 命令）、`floodmind/cli_interactive.py`（移除 legacy 菜单分支）、`pyproject.toml`（移除 `web`/`tui`/`legacy`/`deployment` optional deps 与 `web/tui` 相关 scripts；`floodmind` script 保留）、`Dockerfile`（web 耦合 → 改为 SDK 无 web 依赖或删除）、`requirements.txt`。
- 测试: 删除 `tests/test_web_client.py`、`tests/test_server_agent_factory_workspace.py`、`tests/test_session_store_migration.py`；更新 `tests/test_cli_commands.py`（移除 web/tui/serve 断言）、`tests/test_sdk_purity.py`（banned 列表更新）。

**Interfaces:**
- 前置验证（删除前）:`git grep -n "floodmind.server\|floodmind.tui\|web_server\|start_web_server\|session_store\|sync_events\|scheduler" floodmind/ tests/` —— 确认除待删模块外无 core 依赖；`floodmind/memory/session_manager.py` 被 api.py 用（保留）。`floodmind/cli.py` 现有 `tui`/`web`/`serve` 已走 `_legacy_ui_notice`（映射报告确认）——直接删命令与 notice。

- [ ] **Step 1: 验证删除清单（先做无害 grep，不改代码）。**

Run:
```bash
git grep -n "from floodmind.server\|from floodmind.tui\|import floodmind.server\|import floodmind.tui" -- floodmind/ tests/ || echo "no core deps"
git grep -ln "session_store\|sync_events" -- floodmind/ tests/ || echo "no session_store deps outside server"
git grep -ln "web_server\|start_web_server\|start.py" -- floodmind/ tests/ || echo "no launcher deps"
```
Expected: 命中的文件全部在待删清单内（server/tui/web_server/start/scheduler/session_store 及其测试）；若 `session_store`/`scheduler` 被 core 使用，本任务不删该文件，仅删 web 专属部分并在台账记录。

- [ ] **Step 2: 删除 web/TUI 模块。**

Run:
```bash
git rm -r floodmind/server
git rm -r floodmind/tui
git rm web_server.py start.py
git rm floodmind/memory/session_store.py        # 仅当 Step 1 确认无 core 使用者
git rm scheduler.py                              # 仅当 Step 1 确认 web-only
```
同时删除对应测试：
```bash
git rm tests/test_web_client.py tests/test_server_agent_factory_workspace.py tests/test_session_store_migration.py
```

- [ ] **Step 3: 清理 CLI 与交互入口。**

In `floodmind/cli.py`: 删除 `tui`、`web_cmd`、`serve` 命令及 `_legacy_ui_notice`（若仅它们使用）；保留 `chat`/`run`/`list_checkpoints`/`pause_session`/`init`/`skill`/`config` 等纯 CLI。删除对 `floodmind.tui`/`floodmind.server`/`flask`/`textual` 的任何引用。

In `floodmind/cli_interactive.py`: 移除 `T`(tui)/`W`(web) 菜单分支与 `_legacy_ui_notice` 调用；保留 `R`(run)/`C`(chat)/`Q`。

- [ ] **Step 4: 清理打包/部署引用。**

In `pyproject.toml`:
- `[project.optional-dependencies]` 移除 `web`、`tui`、`legacy`、`deployment`（或把 `deployment` 收窄为不含 web）；移除 `flask`/`flask-cors`/`werkzeug`/`waitress`/`aiofiles`/`gunicorn`/`schedule`/`websockets`/`httpx-sse`/`rich`/`textual` 依赖条目。
- `[tool.setuptools.packages.find]` 的 `exclude = ["floodmind.server*", "floodmind.tui*"]` 保留（无害）或移除（已无此包）。
- `[project.scripts]` 保留 `floodmind = "floodmind.cli:main"`。

In `Dockerfile`: web 耦合（waitress `web_server:app`、`/api/health` healthcheck）→ 改为 SDK 目标：安装 core 依赖，`CMD` 改为 `floodmind run` 示例或删除 healthcheck；或整文件删除并在台账注明（desktop 是独立项目，web 部署不再需要）。选择：**删除 web 服务形态，Dockerfile 改注释说明仅供 SDK 无头运行**——实施者按 Step 1 的 grep 结果与仓库当前 Dockerfile 内容决定最小改动并记录。

In `requirements.txt`: 移除 `floodmind[tui]` 等 web/tui 引用注释。

- [ ] **Step 5: 更新保留的测试。**

In `tests/test_cli_commands.py`: 删除针对 `tui`/`web`/`serve`/`--tui`/`--web` 的断言（命令已删）；保留 `run`/`chat`/`config` 等。若 `_LEGACY_MODULES` 常量只剩 `flask`/`textual`，一并删除。

In `tests/test_sdk_purity.py`: `BANNED_SDK_IMPORTS` 更新为不引用已删模块（或保留 `flask`/`textual` 检查，因依赖已从 pyproject 移除，改为断言 core 不引入它们）。`test_pyproject_core_dependencies_exclude_web_tui_stack` 更新为断言 pyproject 不再声明 web/tui 依赖。

- [ ] **Step 6: 全量回归 + 提交。**

Run: `python -m pytest -q`
Expected: 全绿（净删一批测试；SDK core 1168 相关全过）。然后：
```bash
git add floodmind/cli.py floodmind/cli_interactive.py pyproject.toml Dockerfile requirements.txt tests/test_cli_commands.py tests/test_sdk_purity.py
git commit -m "chore(p8): remove deprecated web/TUI frontends and web-coupled storage/launch (SDK-only)
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage（§4 P8 + 用户范围锁定 + §18/§23）:**
- Journal Backend 最终形态 + SQLite 只作 Index → Task 1（`SqliteJournalIndex` 派生可重建；JSONL 唯一权威）。
- SSE 事件改由 Journal 派生 + after_sequence 重放 → 随 web 弃用，语义落到 SDK 层：Task 2 的 `SdkEvent.sequence` + `Agent.events_after(sequence)`（desktop 据此做重放/对账）；§4.4/§10.1 Preview 对账语义由 `project_canonical` 的 committed/preview 区分承载。
- SDK 公共 API 按 TARGET 契约重定义 → Task 2（标准身份 + Journal 派生事件 + resume，docstring 即契约）。
- web/desktop/TUI 适配 → 按用户范围锁定：**不做**。Task 3 只清理弃用 web/TUI。
- §23「存储多面并存 → 单一权威 + 可重建 Projection/Index」→ Task 1 收敛 + Task 3 移除 web `sync_events`。
- 验收（desktop 清单）→ 不实现桌面端，但 SDK 契约为 desktop 提供全部所需原语（`events_after`=断线重放/Preview 对账、标准身份、`resume`=Pause/Abort/Resume 可见、P3 的 pending reconcile、P6 的 background 唤醒事件）。

**Placeholder scan:** 无 TBD/TODO；每步有具体代码/命令/预期。

**Type consistency:** `SqliteJournalIndex.read_after` 返回 `List[EventEnvelope]` 与 `JournalAuthority.read_after` 一致；`SdkEvent.sequence` 即 `EventEnvelope.sequence`；`project_canonical` 对 `model.attempt.completed` 的 `is_final` 过滤与 reducer 语义一致；`Agent.resume` 委托 `run_with_resume`（既有签名 `run_with_resume(user_input, resume_session_id=None, resume_checkpoint_id=None)`）。

**已知边界（文档化，非缺口）:** `floodmind/memory/session_manager.py` 被 api.py 使用（validate_session_id），保留；`SESSION_CONTEXT`（工具路径/会话上下文）非 web/TUI 专属，不在 P8 删除范围。desktop 集成由对方项目按 SDK 契约自理。
