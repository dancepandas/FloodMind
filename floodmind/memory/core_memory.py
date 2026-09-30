"""项目级 durable core memory（P0-3，Letta 原则：小而常驻、可工具自编辑）。

从 per-session core_memory.json 升格为项目级存储，启动时聚合迁移旧会话文件
（按 (category, fact) 去重，只读旧文件）。schema 预留 scope 字段
（user/project/session 三层为接口预留，本次只写 project）。

设计意图
--------
1. **为何项目级**：旧实现把 core_memory.json 放在每个会话目录下，会话一换
   "用户偏好/项目约束" 就全部丢失，与 Letta core memory "小而常驻、跨会话
   稳定" 的原则相悖。升格后整个项目只有一份权威存储，任何会话（含 native
   agent 与后续门面）读写同一事实集。
2. **迁移策略**：启动时扫描旧会话目录下的 core_memory.json，把纯字符串列表
   形态的旧事实聚合进新存储。聚合口径是 (category, fact) 全局去重——同一
   事实在多个会话出现时只保留一次，因此迁移天然幂等，可反复执行。旧文件
   只读，不删除、不改写，失败非致命（记 warning 后跳过该文件/放弃本轮）。
3. **scope 预留**：落盘 schema 每条事实带 scope（user/project/session 三层），
   本次实现统一写 "project"；字段保留以便后续按受众过滤渲染，但不引入
   未实现的过滤逻辑（只向前，不留半成品接口）。

边界条件
--------
- **去重口径**：append 与迁移都按 (category, fact) 精确匹配（strip 后），
  跨 scope 不去重——同一 fact 以不同 scope 出现视为两条。
- **截断行为**：render 按字符数硬截断，先保证 section 头能放下再放条目；
  截断发生在条目边界，不会出现半行；空存储返回 ""（调用方据此跳过注入）。
- **旧格式兼容**：_normalize 同时接受纯字符串列表（v1 会话文件）与
  {"fact", "scope"} dict 列表（v2 落盘），dict 缺 scope 默认 "project"。
- **原子性**：写盘走 tmp + flush + fsync + os.replace，崩溃不留半截文件；
  进程内用 RLock 保护（同进程多线程/递归调用安全；跨进程不加文件锁，
  由单 writer 约束保证）。
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT as _RUNTIME_ROOT
except Exception:  # pragma: no cover
    _RUNTIME_ROOT = None

if _RUNTIME_ROOT is not None:
    _DEFAULT_STORE_FILE = str(_RUNTIME_ROOT / "data" / "memory" / "core_memory.json")
else:  # pragma: no cover
    _DEFAULT_STORE_FILE = os.path.join(os.path.expanduser("~"), ".floodmind", "core_memory.json")


def _legacy_session_dir() -> Path:
    """默认存储形态下的旧会话目录（data/sessions）。"""
    if _RUNTIME_ROOT is not None:
        return Path(_RUNTIME_ROOT) / "data" / "sessions"
    return Path(os.path.expanduser("~")) / ".floodmind" / "sessions"


class CoreMemoryStore:
    """项目级核心记忆存储。内存索引 + 原子落盘 + 进程锁。

    内存中维护 category -> [{fact, scope}] 索引；每次 append 立即落盘，
    load()/render() 只读内存，不落盘。store_file 为 None 时用项目级默认
    路径 data/memory/core_memory.json；测试/桌面版可显式注入路径。
    """

    def __init__(
        self,
        store_file: Optional[str] = None,
        *,
        migrate_legacy: bool = True,
        max_chars: int = 800,
    ):
        self.store_file = store_file or _DEFAULT_STORE_FILE
        self._max_chars = max_chars
        self._facts: Dict[str, List[Dict[str, str]]] = {}
        self._lock = threading.RLock()
        self._load()
        if migrate_legacy:
            self._migrate_legacy_sessions()

    # ── 读 ──────────────────────────────────────────────

    def load(self) -> Dict[str, Any]:
        """返回当前事实快照（深拷贝），磁盘契约见模块 docstring。

        updated_at 取调用时刻而非落盘时刻——load 是只读视图，不触发写盘。
        """
        with self._lock:
            return {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "facts": {cat: [dict(item) for item in items]
                          for cat, items in self._facts.items()},
            }

    def render(self, *, max_chars: Optional[int] = None) -> str:
        """渲染为注入块的文本；空返回 ""。

        截断策略：先放 "[核心记忆]" 头行，再按写入顺序逐 section 放
        "## category" 与 "- fact" 行；某行放不下时立即返回已积累部分
        （条目粒度截断，绝不截半行）。max_chars 省略时用构造参数默认值。
        """
        limit = max_chars if max_chars is not None else self._max_chars
        with self._lock:
            if not any(self._facts.values()):
                return ""
            header = "[核心记忆] 本项目持久关键事实（可经 CoreMemoryAppend 维护）："
            lines = [header]
            total = len(header)
            for cat, items in self._facts.items():
                if not items:
                    continue
                section = f"## {cat}"
                if total + len(section) > limit:
                    break
                lines.append(section)
                total += len(section) + 1
                for item in items:
                    line = f"- {item['fact']}"
                    if total + len(line) > limit:
                        return "\n".join(lines)
                    lines.append(line)
                    total += len(line) + 1
            return "\n".join(lines)

    # ── 写 ──────────────────────────────────────────────

    def append(self, category: str, fact: str, *, scope: str = "project") -> bool:
        """追加一条事实；按 (category, fact) 去重，重复返回 False。

        空 category/fact（含纯空白）拒绝写入并返回 False；scope 传空串
        归一为 "project"。写成功才落盘。
        """
        category = str(category).strip()
        fact = str(fact).strip()
        if not category or not fact:
            return False
        with self._lock:
            items = self._facts.setdefault(category, [])
            if any(item["fact"] == fact for item in items):
                return False
            items.append({"fact": fact, "scope": scope or "project"})
            self._save()
        return True

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        """从磁盘恢复内存索引；文件缺失或损坏时降级为空索引（不抛异常）。

        损坏容忍：JSON 解析失败/结构异常只记 error 日志，避免启动即崩溃——
        核心记忆是增强能力，不应阻断会话。
        """
        with self._lock:
            if not os.path.exists(self.store_file):
                self._facts = {}
                return
            try:
                raw = json.loads(Path(self.store_file).read_text(encoding="utf-8"))
                self._facts = self._normalize(raw.get("facts", {}))
            except Exception as e:
                logger.error("[核心记忆] 加载失败: %s", e)
                self._facts = {}

    def _save(self):
        """原子落盘：tmp 写 + flush + fsync + os.replace。

        任何一步失败只记 error 日志不抛出——调用方（append/迁移）已持有
        更新后的内存索引，丢失的是本次持久化而非内存状态。
        """
        try:
            os.makedirs(os.path.dirname(self.store_file), exist_ok=True)
            tmp_path = self.store_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": 2, "facts": self._facts},
                    f, ensure_ascii=False, indent=2,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.store_file)
        except Exception as e:
            logger.error("[核心记忆] 保存失败: %s", e)

    # ── 旧会话迁移 ──────────────────────────────────────

    def _migrate_legacy_sessions(self) -> int:
        """扫描旧会话 core_memory.json，只读聚合导入（(category, fact) 去重）。

        迁移幂等：事实先并入内存索引再按去重口径过滤，重复执行不会翻倍。
        旧文件一律只读，成功导入也不删除（由后续清理任务负责）。单个文件
        读取失败跳过该文件；整体失败降级为警告，不阻断启动。

        旧会话目录的定位：默认项目存储（data/memory/core_memory.json）对应
        data/sessions；显式注入 store_file（测试/桌面版）时，旧会话目录即
        store_file 同级的 sessions/——保证被替换的存储与其旧会话文件始终
        聚在同一数据根下，测试也才能用临时目录完整演练迁移。
        """
        if os.path.abspath(self.store_file) == os.path.abspath(_DEFAULT_STORE_FILE):
            sessions_dir = _legacy_session_dir()
        else:
            sessions_dir = Path(self.store_file).parent / "sessions"
        if not sessions_dir.is_dir():
            return 0
        added = 0
        try:
            entries = []  # (category, fact)
            for child in sorted(sessions_dir.iterdir()):
                legacy = child / "core_memory.json"
                if not legacy.is_file():
                    continue
                try:
                    raw = json.loads(legacy.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning("[核心记忆] 旧会话文件读取失败 %s: %s", legacy, e)
                    continue
                for cat, facts in (raw.get("facts") or {}).items():
                    for fact in facts or []:
                        if isinstance(fact, str) and fact.strip():
                            entries.append((str(cat), fact.strip()))
            with self._lock:
                for cat, fact in entries:
                    items = self._facts.setdefault(cat, [])
                    if any(item["fact"] == fact for item in items):
                        continue
                    items.append({"fact": fact, "scope": "project"})
                    added += 1
                if added:
                    self._save()
        except Exception as e:
            logger.warning("[核心记忆] 旧会话迁移失败（非致命）: %s", e)
        if added:
            logger.info("[核心记忆] 已从旧会话文件导入 %d 条", added)
        return added

    @staticmethod
    def _normalize(facts: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
        """兼容两种落盘形态：纯字符串列表（旧）或 dict 列表（新）。

        空串/空白 fact 丢弃；dict 缺 scope 键时默认 "project"（实现用
        item.get("scope", "project")，只兜缺键；scope 显式传空串时此处
        不归一，由 append 的 `scope or "project"` 负责）。
        """
        out: Dict[str, List[Dict[str, str]]] = {}
        for cat, items in (facts or {}).items():
            norm: List[Dict[str, str]] = []
            for item in items or []:
                if isinstance(item, str) and item.strip():
                    norm.append({"fact": item.strip(), "scope": "project"})
                elif isinstance(item, dict) and str(item.get("fact", "")).strip():
                    norm.append({
                        "fact": str(item["fact"]).strip(),
                        "scope": str(item.get("scope", "project")),
                    })
            out[str(cat)] = norm
        return out
