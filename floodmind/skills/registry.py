"""Skill 注册表 — skill 体系的单一发现源、单 registry、单 catalog。

替代旧的双 registry（skills.SKILL_REGISTRY + tools._SKILL_REGISTRY）+ CWD 相关根 +
死掉的 refresh_skills。roots 用 ``_PROJECT_ROOT``（包定位）而非 ``Path.cwd()``，保证
任意 CWD 下发现集合一致；auto-gen 写 ``_PROJECT_ROOT/skills``（默认根之一）→ 即发现。

线程安全（持锁，对标 ``_InstanceToolRegistry`` / ``McpClientPool``）。GetSkill /
refresh_skills / CRUD 工具 / curator 全部经 ``get_skill_registry()`` 这一个源。
"""

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from floodmind.skills.base import Skill, discover_skills_from_roots, generate_skill_catalog

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent              # floodmind/skills/
_PROJECT_ROOT = _HERE.parent.parent                  # -> floodmind/ -> repo root


def default_roots() -> List[Path]:
    """默认发现根（CWD 无关）：内置 + 项目 + Claude-Code 兼容。"""
    return [
        _HERE,                                  # 内置技能（随包发布）
        _PROJECT_ROOT / "skills",              # 项目/用户技能（auto-gen 也写这里）
        _PROJECT_ROOT / ".claude" / "skills",  # Claude-Code 兼容
    ]


class SkillRegistry:
    """skill 单一权威源：发现、catalog、lifecycle。线程安全。"""

    def __init__(self, roots: Optional[List[Path]] = None, writable_root: Optional[Path] = None):
        self._roots: List[Path] = list(roots) if roots is not None else default_roots()
        # 写入根：CreateSkill/UpdateSkill/RemoveSkill(auto-gen) 落盘到此（默认项目 skills 目录）
        self._writable_root: Path = writable_root if writable_root is not None else (_PROJECT_ROOT / "skills")
        self._skills: List[Skill] = []
        self._disabled: set = set()
        self._catalog: str = ""
        self._refresh_callbacks: List[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._scan()  # 构造期填充（单例发布前，无需持锁）

    # ── 内部 ──────────────────────────────────────────────
    def _scan(self) -> None:
        """重扫所有根，应用 disabled 过滤，重建 catalog。调用者持锁（或构造期）。

        编程式注册的 skill（``register_skill``，无 ``skill_dir``、不落盘）在重扫时
        **保留**，不被磁盘发现覆盖丢失——SDK 嵌入用例依赖此不变量。同名时磁盘发现优先。
        """
        discovered = discover_skills_from_roots(self._roots)
        ephemeral = [s for s in self._skills if not s.skill_dir]  # register_skill 注册的
        seen: set = set()
        merged: List[Skill] = []
        for s in discovered + ephemeral:  # 磁盘优先，ephemeral 仅补磁盘没有的
            if s.name in seen or s.name in self._disabled:
                continue
            merged.append(s)
            seen.add(s.name)
        self._skills = merged
        self._catalog = generate_skill_catalog(self._skills)

    def _notify_changed(self) -> None:
        """变更后触发回调（锁外执行，回调自带的锁不会与注册表锁嵌套）。

        主要消费者：GetSkill 的 lru_cache 在 refresh/register 后失效，避免 stale 正文。
        """
        for cb in list(self._refresh_callbacks):
            try:
                cb()
            except Exception:
                logger.warning("skill refresh callback 失败", exc_info=True)

    def add_refresh_callback(self, cb: Callable[[], None]) -> None:
        """注册一个"skill 集合变更时"回调（如清缓存）。"""
        self._refresh_callbacks.append(cb)

    # ── 查询 ──────────────────────────────────────────────
    @property
    def roots(self) -> List[Path]:
        return list(self._roots)

    @property
    def writable_root(self) -> Path:
        """写入根：新 skill 落盘目录（CreateSkill/UpdateSkill/RemoveSkill/auto-gen）。"""
        return self._writable_root

    def all_skills(self) -> List[Skill]:
        with self._lock:
            return list(self._skills)

    def get_skill(self, name: str) -> Optional[Skill]:
        with self._lock:
            for s in self._skills:
                if s.name == name:
                    return s
            return None

    def list_skills(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "name": s.name,
                    "description": s.description,
                    "version": s.version,
                    "category": s.category,
                    "source": str(s.skill_dir) if s.skill_dir else "",
                    "disabled": s.name in self._disabled,
                }
                for s in self._skills
            ]

    def catalog(self) -> str:
        with self._lock:
            return self._catalog

    # ── lifecycle ─────────────────────────────────────────
    def refresh(self) -> List[Skill]:
        """重扫（原地：所有持单例引用者即见新）。返回最新列表。"""
        with self._lock:
            self._scan()
            result = list(self._skills)
        self._notify_changed()
        logger.info("Skill 注册表已刷新: %d 个技能", len(result))
        return result

    def register_skill(self, skill: Skill) -> None:
        """编程式注册（去重：同名替换）。不落盘——供 SDK 嵌入用。"""
        with self._lock:
            self._skills = [s for s in self._skills if s.name != skill.name]
            self._skills.append(skill)
            self._catalog = generate_skill_catalog(self._skills)
        self._notify_changed()
        logger.info("注册 Skill: %s", skill.name)

    def set_disabled(self, name: str, disabled: bool) -> None:
        """禁用/启用：不删盘，仅从 catalog 隐藏（disabled 集合在内存，重扫后仍生效）。"""
        with self._lock:
            if disabled:
                self._disabled.add(name)
            else:
                self._disabled.discard(name)
            self._scan()
        self._notify_changed()


# ── 单例 ──────────────────────────────────────────────────
_registry: Optional[SkillRegistry] = None
_singleton_lock = threading.Lock()


def get_skill_registry() -> SkillRegistry:
    """全局唯一 SkillRegistry（首次访问惰性创建 + 扫描）。"""
    global _registry
    if _registry is None:
        with _singleton_lock:
            if _registry is None:
                _registry = SkillRegistry()
    return _registry
