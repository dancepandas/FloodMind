"""
技能注册表 - 默认全局兼容 + Agent 实例隔离。

``get_skill_registry()``、模块级 ``register_skill`` 和 live 视图继续操作默认全局
Registry；嵌入式 runtime 通过 ``create_skill_registry()`` 为每个 Agent 构造独立实例。
所有发现根均基于包位置或宿主显式路径，不依赖进程 CWD。
"""

import logging
from typing import List

from floodmind.skills.base import Skill, discover_skills, discover_skills_from_roots, generate_skill_catalog, register_skill
from floodmind.skills.registry import (
    SkillRegistry,
    SkillRoot,
    create_skill_registry,
    default_roots,
    get_skill_registry,
)

logger = logging.getLogger(__name__)


def refresh_skill_registry() -> List[Skill]:
    """重新扫描发现根，刷新单例（唯一权威源）。返回最新技能列表。"""
    return get_skill_registry().refresh()


def __getattr__(name):
    """向后兼容 + live 视图：``SKILL_REGISTRY`` / ``SKILL_CATALOG`` 每次访问取单例最新值。

    旧代码 ``from floodmind.skills import SKILL_REGISTRY`` 拿到的是单例的当前快照，
    ``register_skill`` / ``refresh`` 后再次访问即见新（修复旧 ``from import`` 快照在
    refresh 后 stale 的问题）。
    """
    if name == "SKILL_REGISTRY":
        return get_skill_registry().all_skills()
    if name == "SKILL_CATALOG":
        return get_skill_registry().catalog()
    raise AttributeError(f"module 'floodmind.skills' has no attribute {name!r}")


__all__ = [
    "Skill",
    "SkillRegistry",
    "SkillRoot",
    "create_skill_registry",
    "discover_skills",
    "discover_skills_from_roots",
    "generate_skill_catalog",
    "get_skill_registry",
    "default_roots",
    "SKILL_REGISTRY",
    "SKILL_CATALOG",
    "refresh_skill_registry",
    "register_skill",
]
