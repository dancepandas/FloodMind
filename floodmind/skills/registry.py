"""Skill 注册表 — skill 体系的单一发现源与结构化根配置。

根目录按显式优先级逐个扫描；同名 skill 的胜者可预测，单个坏根或坏 skill 不会
阻断其余发现。威胁扫描仍由 :mod:`floodmind.skills.base` 的解析器负责。
"""

from __future__ import annotations

import logging
import os
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Union

from floodmind.skills.base import (
    Skill,
    discover_skills,
    generate_skill_catalog,
    is_reparse_point,
)

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent.parent

_ORIGIN_PRIORITY = {
    "builtin": 500,
    "host": 400,
    "project": 300,
    "claude_compat": 200,
    "ephemeral": 100,
}

PathLike = Union[str, os.PathLike]

_WINDOWS_RESERVED_SKILL_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_PORTABLE_NAME_FORBIDDEN = set('<>:"/\\|?*')


def _normalize_path(path: PathLike) -> Path:
    return Path(path).expanduser().resolve()


def _path_key(path: PathLike) -> str:
    return os.path.normcase(str(_normalize_path(path)))


def _reject_existing_symlink(path: PathLike, label: str) -> None:
    """Reject symlinks in every currently existing component of ``path``.

    Checking only the final component is insufficient: a writable root or skill
    directory can be redirected later through a symlinked ancestor.  Broken
    symlinks are covered because ``Path.is_symlink()`` does not require the
    target to exist.  Windows junction 属于目录 reparse point 但
    ``Path.is_symlink()`` 对其返回 False，须用兼容判定一并拒绝。
    """
    expanded = Path(path).expanduser().absolute()
    for component in [*reversed(expanded.parents), expanded]:
        if component.is_symlink() or is_reparse_point(component):
            raise ValueError(f"{label} 不能包含符号链接/junction: {component}")


@dataclass(frozen=True)
class SkillRoot:
    """不可变的 skill 根元数据。priority 越大，发现优先级越高。"""

    path: Path
    origin: str
    priority: int
    read_only: bool
    order: int

    def __post_init__(self) -> None:
        if self.origin not in _ORIGIN_PRIORITY:
            raise ValueError(f"未知 skill root origin: {self.origin}")
        object.__setattr__(self, "path", _normalize_path(self.path))


def default_roots() -> List[Path]:
    """默认发现根（CWD 无关）：内置 + 项目 + Claude Code 兼容。"""
    return [
        _HERE,
        _PROJECT_ROOT / "skills",
        _PROJECT_ROOT / ".claude" / "skills",
    ]


def _default_root_specs() -> List[SkillRoot]:
    builtin, project, compat = default_roots()
    return [
        SkillRoot(builtin, "builtin", _ORIGIN_PRIORITY["builtin"], True, 0),
        SkillRoot(project, "project", _ORIGIN_PRIORITY["project"], False, 0),
        SkillRoot(compat, "claude_compat", _ORIGIN_PRIORITY["claude_compat"], True, 0),
    ]


def _dedupe_specs(specs: Iterable[SkillRoot]) -> List[SkillRoot]:
    result: List[SkillRoot] = []
    seen = set()
    for spec in specs:
        key = _path_key(spec.path)
        if key in seen:
            continue
        seen.add(key)
        result.append(spec)
    return result


def _normalize_root_configuration(
    specs: Iterable[SkillRoot],
    writable_root: PathLike,
    *,
    mark_writable: bool = False,
) -> tuple[Path, List[SkillRoot]]:
    """Canonicalize and de-duplicate roots, optionally marking the writable one."""
    writable = _normalize_path(writable_root)
    writable_key = _path_key(writable)
    normalized = [
        SkillRoot(
            spec.path,
            spec.origin,
            spec.priority,
            _path_key(spec.path) != writable_key,
            spec.order,
        )
        if mark_writable and spec.origin in {"project", "host"}
        else spec
        for spec in specs
    ]
    return writable, sorted(
        _dedupe_specs(normalized), key=lambda spec: (-spec.priority, spec.order)
    )


class _CallbackRef:
    """Bound methods are weak; ordinary callables are intentionally strong."""

    def __init__(self, callback: Callable[[], None]):
        self._strong = None
        self._weak = None
        if getattr(callback, "__self__", None) is not None:
            try:
                self._weak = weakref.WeakMethod(callback)
            except TypeError:
                self._strong = callback
        else:
            self._strong = callback

    def get(self) -> Optional[Callable[[], None]]:
        return self._strong if self._strong is not None else self._weak()

    def matches(self, callback: Callable[[], None]) -> bool:
        return self.get() == callback


class SkillRegistry:
    """Skill 的单一权威源：发现、查询、catalog 与 lifecycle。线程安全。"""

    def __init__(
        self,
        roots: Optional[List[PathLike]] = None,
        writable_root: Optional[PathLike] = None,
        *,
        root_specs: Optional[List[SkillRoot]] = None,
        threat_scanner: Optional[Callable[[str], Any]] = None,
    ):
        if roots is not None and root_specs is not None:
            raise ValueError("roots 与 root_specs 不能同时提供")

        raw_writable = writable_root or (_PROJECT_ROOT / "skills")
        _reject_existing_symlink(raw_writable, "writable_root")
        if root_specs is not None:
            specs = list(root_specs)
            mark_writable = False
        elif roots is not None:
            # 旧构造器把调用方给出的根视为 host 根；匹配 writable_root 的根保持可写。
            specs = [
                SkillRoot(root, "host", _ORIGIN_PRIORITY["host"], True, order)
                for order, root in enumerate(roots)
            ]
            mark_writable = True
        else:
            specs = _default_root_specs()
            mark_writable = True

        writable, specs = _normalize_root_configuration(
            specs, raw_writable, mark_writable=mark_writable
        )
        if not any(_path_key(spec.path) == _path_key(writable) for spec in specs):
            specs.append(
                SkillRoot(writable, "host", _ORIGIN_PRIORITY["host"], False, len(specs))
            )
            writable, specs = _normalize_root_configuration(specs, writable)

        self._validate_writable_root(writable, specs)
        self._root_specs = specs
        self._writable_root = writable
        self._threat_scanner = threat_scanner
        self._skills: List[Skill] = []
        self._ephemeral_skills: Dict[str, Skill] = {}
        self._skill_roots: Dict[str, Optional[SkillRoot]] = {}
        self._disabled: set = set()
        self._catalog = ""
        self._refresh_callbacks: List[_CallbackRef] = []
        self._lock = threading.Lock()
        self._state_revision = 0
        skills, roots_by_name, catalog = self._scan_snapshot(
            self._root_specs,
            self._ephemeral_skills,
            self._disabled,
            self._threat_scanner,
        )
        self._skills = skills
        self._skill_roots = roots_by_name
        self._catalog = catalog

    @staticmethod
    def _validate_writable_root(writable: Path, specs: Iterable[SkillRoot]) -> None:
        _reject_existing_symlink(writable, "writable_root")
        key = _path_key(writable)
        for spec in specs:
            if _path_key(spec.path) == key and spec.read_only:
                raise ValueError(f"writable_root 与只读根冲突: {writable}")

    @staticmethod
    def _scan_snapshot(
        root_specs: Iterable[SkillRoot],
        ephemeral_skills: Dict[str, Skill],
        disabled: set,
        threat_scanner: Optional[Callable[[str], Any]] = None,
    ) -> tuple[List[Skill], Dict[str, Optional[SkillRoot]], str]:
        ephemeral = list(ephemeral_skills.values())
        seen = set()
        merged: List[Skill] = []
        roots_by_name: Dict[str, Optional[SkillRoot]] = {}

        for spec in root_specs:
            try:
                if threat_scanner is None:
                    discovered = discover_skills(spec.path)
                else:
                    discovered = discover_skills(spec.path, threat_scanner=threat_scanner)
            except Exception:
                logger.warning("扫描 skill 根失败，继续其余根: %s", spec.path, exc_info=True)
                continue
            for skill in discovered:
                if skill.name in seen:
                    winner = roots_by_name[skill.name]
                    logger.warning(
                        "重复 skill '%s': 保留 %s，忽略 %s",
                        skill.name,
                        winner.path if winner else "ephemeral",
                        spec.path,
                    )
                    continue
                if skill.name in disabled:
                    continue
                seen.add(skill.name)
                merged.append(skill)
                roots_by_name[skill.name] = spec

        for skill in ephemeral:
            if skill.name in seen:
                logger.warning("重复 skill '%s': 磁盘 skill 优先于 ephemeral", skill.name)
                continue
            if skill.name in disabled:
                continue
            seen.add(skill.name)
            merged.append(skill)
            roots_by_name[skill.name] = None

        return merged, roots_by_name, generate_skill_catalog(merged)

    def _rescan_and_swap(self) -> List[Skill]:
        """Scan without the registry lock, then publish an internally consistent snapshot.

        If a concurrent mutation occurs during the scan, retry against the newer
        ephemeral/disabled state so the slow scan cannot overwrite it.
        """
        while True:
            with self._lock:
                revision = self._state_revision
                ephemeral = dict(self._ephemeral_skills)
                disabled = set(self._disabled)
                root_specs = tuple(self._root_specs)
            skills, roots_by_name, catalog = self._scan_snapshot(
                root_specs, ephemeral, disabled, self._threat_scanner
            )
            with self._lock:
                if revision != self._state_revision:
                    continue
                self._skills = skills
                self._skill_roots = roots_by_name
                self._catalog = catalog
                return list(skills)

    def _notify_changed(self) -> None:
        with self._lock:
            refs = list(self._refresh_callbacks)
        dead: List[_CallbackRef] = []
        for ref in refs:
            callback = ref.get()
            if callback is None:
                dead.append(ref)
                continue
            try:
                callback()
            except Exception:
                logger.warning("skill refresh callback 失败", exc_info=True)
        if dead:
            with self._lock:
                self._refresh_callbacks = [r for r in self._refresh_callbacks if r not in dead]

    def add_refresh_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """注册变更回调并返回幂等 unsubscribe。回调始终在 registry 锁外执行。"""
        ref = _CallbackRef(callback)
        with self._lock:
            self._refresh_callbacks.append(ref)

        def unsubscribe() -> None:
            with self._lock:
                if ref in self._refresh_callbacks:
                    self._refresh_callbacks.remove(ref)

        return unsubscribe

    def remove_refresh_callback(self, callback: Callable[[], None]) -> None:
        """按 callable 移除已注册回调。"""
        with self._lock:
            self._refresh_callbacks = [
                ref for ref in self._refresh_callbacks if not ref.matches(callback)
            ]

    @property
    def roots(self) -> List[Path]:
        return [spec.path for spec in self._root_specs]

    @property
    def root_specs(self) -> List[SkillRoot]:
        return list(self._root_specs)

    @property
    def writable_root(self) -> Path:
        return self._writable_root

    def all_skills(self) -> List[Skill]:
        with self._lock:
            return list(self._skills)

    def get_skill(self, name: str) -> Optional[Skill]:
        with self._lock:
            return next((skill for skill in self._skills if skill.name == name), None)

    def get_skill_root(self, name: str) -> Optional[SkillRoot]:
        with self._lock:
            return self._skill_roots.get(name)

    def get_skill_source(self, name: str) -> Optional[Path]:
        skill = self.get_skill(name)
        return skill.skill_dir if skill else None

    def get_skill_content(self, name: str) -> Optional[str]:
        """返回 SKILL.md 原文；编程式 skill 返回其 prompt。"""
        skill = self.get_skill(name)
        if skill is None:
            return None
        if not skill.skill_dir:
            return skill.prompt
        try:
            return (skill.skill_dir / "SKILL.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            logger.warning("读取 skill 内容失败: %s", name, exc_info=True)
            return None

    def load_skill(self, name: str) -> Optional[str]:
        return self.get_skill_content(name)

    def list_skills(self) -> List[Dict[str, Any]]:
        with self._lock:
            result = []
            for skill in self._skills:
                root = self._skill_roots.get(skill.name)
                result.append({
                    "name": skill.name,
                    "description": skill.description,
                    "version": skill.version,
                    "category": skill.category,
                    "source": str(skill.skill_dir) if skill.skill_dir else "",
                    "disabled": skill.name in self._disabled,
                    "origin": root.origin if root else "ephemeral",
                    "read_only": root.read_only if root else False,
                })
            return result

    def catalog(self) -> str:
        with self._lock:
            return self._catalog

    @staticmethod
    def validate_skill_name(name: str) -> str:
        value = name.strip() if name else ""
        if not value or value in {".", ".."} or value.startswith("."):
            raise ValueError(f"非法 skill name: {name!r}")
        if value != name or value.endswith((".", " ")):
            raise ValueError(f"非法 skill name: {name!r}")
        if ".." in value or any(ch in _PORTABLE_NAME_FORBIDDEN or ord(ch) < 32 for ch in value):
            raise ValueError(f"非法 skill name: {name!r}")
        # Windows reserves these basenames even when an extension is present.
        if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_SKILL_NAMES:
            raise ValueError(f"非法 skill name: {name!r}")
        return value

    def writable_skill_dir(self, name: str) -> Path:
        """解析并验证 writable_root 下的 skill 目录（目录无需存在）。"""
        value = self.validate_skill_name(name)
        _reject_existing_symlink(self._writable_root, "writable_root")
        root = self._writable_root.resolve()
        unresolved = root / value
        _reject_existing_symlink(unresolved, "skill 目录")
        candidate = unresolved.resolve()
        if candidate.parent != root:
            raise ValueError(f"skill 路径逃逸 writable_root: {name!r}")
        return candidate

    def writable_skill_path(self, name: str) -> Path:
        path = self.writable_skill_dir(name) / "SKILL.md"
        _reject_existing_symlink(path, "SKILL.md 路径")
        root = self._writable_root.resolve()
        if path.resolve().parent != root / self.validate_skill_name(name):
            raise ValueError(f"skill 路径逃逸 writable_root: {name!r}")
        return path

    def validate_writable_skill_path(self, name: str, require_source: bool = False) -> Path:
        """CRUD 共用校验：可写根/skill 目录非 symlink（含 junction），且现有来源确在可写根下。"""
        if self._writable_root.is_symlink() or is_reparse_point(self._writable_root):
            raise ValueError(f"writable_root 不能是符号链接/junction: {self._writable_root}")
        path = self.writable_skill_path(name)
        skill = self.get_skill(name)
        if skill and skill.skill_dir:
            source = skill.skill_dir.resolve()
            if source != path.parent:
                raise ValueError(f"skill '{name}' 的实际来源不在 writable_root")
        elif require_source:
            raise ValueError(f"skill '{name}' 没有可写磁盘来源")
        return path

    def is_writable_skill(self, name: str) -> bool:
        try:
            self.validate_writable_skill_path(name, require_source=True)
            return True
        except ValueError:
            return False

    def refresh(self) -> List[Skill]:
        result = self._rescan_and_swap()
        self._notify_changed()
        logger.info("Skill 注册表已刷新: %d 个技能", len(result))
        return result

    def register_skill(self, skill: Skill) -> None:
        with self._lock:
            self._state_revision += 1
            if skill.skill_dir:
                self._skills = [s for s in self._skills if s.name != skill.name]
                self._skills.append(skill)
                self._skill_roots[skill.name] = None
            else:
                # Retain the programmatic value even while a disk skill shadows it,
                # so it becomes the fallback if that disk source later disappears.
                self._ephemeral_skills[skill.name] = skill
                current_root = self._skill_roots.get(skill.name)
                if current_root is None:
                    self._skills = [s for s in self._skills if s.name != skill.name]
                    self._skills.append(skill)
                    self._skill_roots[skill.name] = None
            self._catalog = generate_skill_catalog(self._skills)
        self._notify_changed()
        logger.info("注册 Skill: %s", skill.name)

    def set_disabled(self, name: str, disabled: bool) -> None:
        with self._lock:
            if disabled:
                self._disabled.add(name)
            else:
                self._disabled.discard(name)
            self._state_revision += 1
        self._rescan_and_swap()
        self._notify_changed()


def create_skill_registry(
    additional_roots: Optional[Iterable[PathLike]] = None,
    writable_root: Optional[PathLike] = None,
    threat_scanner: Optional[Callable[[str], Any]] = None,
) -> SkillRegistry:
    """创建独立 registry；host 根追加到默认根，自定义 writable 自动作为 host 根。"""
    specs = _default_root_specs()
    host_order = 0
    for root in additional_roots or ():
        specs.append(SkillRoot(root, "host", _ORIGIN_PRIORITY["host"], True, host_order))
        host_order += 1

    raw_writable = writable_root or (_PROJECT_ROOT / "skills")
    _reject_existing_symlink(raw_writable, "writable_root")
    writable, specs = _normalize_root_configuration(
        specs, raw_writable, mark_writable=True
    )
    if not any(_path_key(spec.path) == _path_key(writable) for spec in specs):
        specs.append(
            SkillRoot(writable, "host", _ORIGIN_PRIORITY["host"], False, host_order)
        )
        writable, specs = _normalize_root_configuration(specs, writable)

    return SkillRegistry(
        root_specs=specs,
        writable_root=writable,
        threat_scanner=threat_scanner,
    )


_registry: Optional[SkillRegistry] = None
_singleton_lock = threading.Lock()


def get_skill_registry() -> SkillRegistry:
    """全局唯一 SkillRegistry（首次访问惰性创建 + 扫描）。"""
    global _registry
    if _registry is None:
        with _singleton_lock:
            if _registry is None:
                _registry = create_skill_registry()
    return _registry
