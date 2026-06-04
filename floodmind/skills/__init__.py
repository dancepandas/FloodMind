"""
技能注册表 - 自动发现版本

采用 OpenClaw / Claude Code 风格：
- 自动扫描 skills/ 目录下的所有 SKILL.md
- 无需手动注册，只需创建技能文件夹
- 支持渐进式披露（元数据 -> 指令 -> 资源）

新增技能只需：
1. 在 skills/ 下创建 <skill-name>/ 文件夹
2. 创建 SKILL.md（填写 frontmatter + 使用说明，name 必填，description 强烈建议填写）
3. 可选：创建 scripts/ 目录放置脚本
4. 可选：创建 references/ 目录放置参考文档
5. 可选：创建 assets/ 目录放置模板、图标等资源文件

无需修改任何代码！
"""

import logging
from pathlib import Path

from floodmind.skills.base import Skill, discover_skills, discover_skills_from_roots, generate_skill_catalog
from floodmind.tools import set_skill_registry

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
_EXTRA_SKILLS_DIRS = [
    Path.cwd() / "skills",
    Path.cwd() / ".claude" / "skills",
]

SKILL_REGISTRY: list[Skill] = discover_skills_from_roots([_HERE] + _EXTRA_SKILLS_DIRS)

set_skill_registry(SKILL_REGISTRY)

SKILL_CATALOG: str = generate_skill_catalog(SKILL_REGISTRY)

__all__ = [
    "Skill",
    "discover_skills",
    "discover_skills_from_roots",
    "generate_skill_catalog",
    "SKILL_REGISTRY",
    "SKILL_CATALOG",
    "refresh_skill_registry",
]


def refresh_skill_registry() -> list[Skill]:
    """重新扫描 skills/ 和 .claude/skills/ 目录，更新全局注册表"""
    global SKILL_REGISTRY, SKILL_CATALOG
    SKILL_REGISTRY = discover_skills_from_roots([_HERE] + _EXTRA_SKILLS_DIRS)
    set_skill_registry(SKILL_REGISTRY)
    SKILL_CATALOG = generate_skill_catalog(SKILL_REGISTRY)
    logger.info(f"Skill 注册表已刷新: {len(SKILL_REGISTRY)} 个技能")
    return SKILL_REGISTRY
