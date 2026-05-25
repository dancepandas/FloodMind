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

from skills.base import Skill, discover_skills, discover_skills_from_roots, generate_skill_catalog
from tools import set_skill_registry

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
_CLAUDE_SKILLS_DIR = _PROJECT_ROOT / ".claude" / "skills"

SKILL_REGISTRY: list[Skill] = discover_skills_from_roots([_HERE, _CLAUDE_SKILLS_DIR])

set_skill_registry(SKILL_REGISTRY)

SKILL_CATALOG: str = generate_skill_catalog(SKILL_REGISTRY)

__all__ = [
    "Skill",
    "discover_skills",
    "discover_skills_from_roots",
    "generate_skill_catalog",
    "SKILL_REGISTRY",
    "SKILL_CATALOG",
]
