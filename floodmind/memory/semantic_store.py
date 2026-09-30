"""语义事实存储（P0 记忆增强新条目模型）。

Graphiti 式失效语义：UPDATE 不覆盖内容，旧条目打 invalid_at/superseded_by，
保留时点回溯（水利参数/方案版本演变场景）。DELETE 为软删除。

数据文件 data/memory/semantic_memory.json，与旧 long_term_memory.json 分离；
门面初始化时一次性导入旧条目（import_legacy，幂等），此后旧 store 冻结只读。
"""

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime
from typing import List, Optional, Set

from pydantic import BaseModel

logger = logging.getLogger(__name__)

try:
    from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT as _RUNTIME_ROOT
except Exception:  # pragma: no cover - 防御性回退
    _RUNTIME_ROOT = None

if _RUNTIME_ROOT is not None:
    _DEFAULT_STORE_FILE = str(_RUNTIME_ROOT / "data" / "memory" / "semantic_memory.json")
else:  # pragma: no cover
    _DEFAULT_STORE_FILE = os.path.join(os.path.expanduser("~"), ".floodmind", "semantic_memory.json")

TERM_RE = re.compile(r"[一-龥]+|[A-Za-z0-9]{2,}")


def extract_terms(text: str) -> Set[str]:
    """抽取检索词（语义事实存储专属口径；与旧 LongTermMemory.search 并不同
    口径——旧 store 已冻结只读，切分逻辑未共享，不要按旧实现假设行为）。

    中文连续段按非重叠二字切分（单字 remainder 不抽取，同 {2,} 口径），
    ASCII 连续段（长度≥2）整体作为词。例如 "敖江预报" -> {"敖江", "预报"}，
    可与 "敖江洪水预报参数" 的 {"敖江","洪水","预报","参数"} 重叠命中。
    """
    terms: Set[str] = set()
    for run in TERM_RE.findall(str(text).lower()):
        if run.isascii():
            terms.add(run)
        else:
            terms.update(run[i:i + 2] for i in range(0, len(run) - 1, 2))
    return terms


class LTMEntryV2(BaseModel):
    entry_id: str
    content: str
    category: str = "note"
    importance: float = 0.5
    valid_at: str = ""                       # ISO datetime
    invalid_at: Optional[str] = None
    superseded_by: Optional[str] = None      # 指向新条目 entry_id
    status: str = "active"                   # active | superseded | deleted
    access_count: int = 0


class SemanticMemoryStore:
    """语义事实存储：原子落盘 + 进程锁，单进程假设（与 LongTermMemory 一致）。"""

    def __init__(self, store_file: Optional[str] = None):
        self.store_file = store_file or _DEFAULT_STORE_FILE
        self._entries: List[LTMEntryV2] = []
        self._lock = threading.RLock()
        self._load()

    # ── 读 ──────────────────────────────────────────────

    def get(self, entry_id: str) -> Optional[LTMEntryV2]:
        with self._lock:
            for e in self._entries:
                if e.entry_id == entry_id:
                    return e
        return None

    def active_entries(self) -> List[LTMEntryV2]:
        with self._lock:
            return [e for e in self._entries if e.status == "active"]

    def count_active(self) -> int:
        with self._lock:
            return sum(1 for e in self._entries if e.status == "active")

    def find_candidates(self, query_terms: Set[str], top_k: int = 3) -> List[LTMEntryV2]:
        """按关键词重叠取冲突候选（仅 active），供 consolidation LLM 判定前过滤。"""
        if not query_terms:
            return []
        scored = []
        for e in self.active_entries():
            overlap = len(query_terms & extract_terms(e.content))
            if overlap > 0:
                scored.append((overlap, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    # ── 写 ──────────────────────────────────────────────

    def add(self, content: str, category: str = "note", importance: float = 0.7) -> LTMEntryV2:
        entry = LTMEntryV2(
            entry_id=f"mem_{uuid.uuid4().hex[:8]}",
            content=str(content).strip(),
            category=category or "note",
            importance=float(importance),
            valid_at=datetime.now().isoformat(),
        )
        with self._lock:
            self._entries.append(entry)
            self._save()
        return entry

    def mark_superseded(self, old_id: str, new_id: str) -> bool:
        """UPDATE 的失效语义：旧条目保留内容，打 invalid_at + superseded_by。"""
        with self._lock:
            old = self.get(old_id)
            if old is None or old.status != "active":
                return False
            old.status = "superseded"
            old.invalid_at = datetime.now().isoformat()
            old.superseded_by = new_id
            self._save()
        return True

    def mark_deleted(self, entry_id: str) -> bool:
        """软删除：检索默认过滤，内容保留可回滚。"""
        with self._lock:
            e = self.get(entry_id)
            if e is None or e.status != "active":
                return False
            e.status = "deleted"
            self._save()
        return True

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        with self._lock:
            if not os.path.exists(self.store_file):
                self._entries = []
                return
            try:
                with open(self.store_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as e:
                logger.error("[语义记忆] 加载失败: %s", e)
                self._entries = []
                return
            items = raw.get("entries", []) if isinstance(raw, dict) else raw
            # 逐条构建而非列表推导整体校验：单条坏数据（缺字段/类型错）只跳过
            # 该条并记 warning，其余好条目全部保留。语义记忆是增强能力，一条脏
            # 数据不应清空整个项目的历史事实——此前一条 LTMEntryV2 校验失败
            # 就整体 _entries=[]，等于一次轻微落盘损坏导致全量数据丢失。
            entries: List[LTMEntryV2] = []
            for e in items if isinstance(items, list) else []:
                if not isinstance(e, dict):
                    logger.warning("[语义记忆] 跳过非对象条目: %r", e)
                    continue
                try:
                    entries.append(LTMEntryV2(**e))
                except Exception as ex:
                    logger.warning(
                        "[语义记忆] 跳过损坏条目（%s），content 前50字: %s",
                        ex, str(e.get("content", ""))[:50],
                    )
            self._entries = entries

    def _save(self):
        try:
            # dirname 可能为空串（store_file 为裸文件名，如测试传 "sem.json"），
            # 空串 makedirs 会抛 FileNotFoundError，回退当前目录。
            os.makedirs(os.path.dirname(self.store_file) or ".", exist_ok=True)
            tmp_path = self.store_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": 2, "entries": [e.model_dump() for e in self._entries]},
                    f, ensure_ascii=False, indent=2,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.store_file)
        except Exception as e:
            logger.error("[语义记忆] 保存失败: %s", e)

    def import_legacy(self, legacy_file: Optional[str] = None) -> int:
        """从旧 long_term_memory.json 导入（幂等：按 content 去重），返回新增条数。"""
        if legacy_file is None:
            try:
                from floodmind.memory.dual_memory import LONG_TERM_MEMORY_FILE as legacy_file
            except Exception as e:
                logger.warning("[语义记忆] 旧存储路径解析失败: %s", e)
                return 0
        if not os.path.exists(legacy_file):
            return 0
        try:
            with open(legacy_file, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception as e:
            logger.warning("[语义记忆] 旧存储读取失败: %s", e)
            return 0
        if not isinstance(items, list):
            return 0
        with self._lock:
            existing = {e.content for e in self._entries}
            added = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content", "")).strip()
                if not content or content in existing:
                    continue
                self._entries.append(LTMEntryV2(
                    entry_id=f"mem_{uuid.uuid4().hex[:8]}",
                    content=content,
                    category=str(item.get("category", "note")),
                    importance=float(item.get("importance", 0.5)),
                    valid_at=str(item.get("timestamp", "")) or datetime.now().isoformat(),
                    access_count=int(item.get("access_count", 0)),
                ))
                existing.add(content)
                added += 1
            if added:
                self._save()
        logger.info("[语义记忆] 旧存储导入 %d 条", added)
        return added
