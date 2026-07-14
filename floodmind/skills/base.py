"""
技能基础模块

提供技能发现、解析和管理的核心功能。
采用自动扫描 SKILL.md 文件的方式发现技能。
"""

import logging
from pathlib import Path
from typing import List, Optional, Any
from dataclasses import dataclass, field
import yaml

logger = logging.getLogger(__name__)

_SKILL_BODY_SOFT_LIMIT = 500


@dataclass
class Skill:
    name: str
    description: str
    prompt: str = ""
    scripts: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    assets: List[str] = field(default_factory=list)
    is_knowledge_only: bool = False
    skill_dir: Optional[Path] = None
    version: str = "1.0"
    provides_tools: List[str] = field(default_factory=list)
    category: str = "execution"
    compatibility: Optional[str] = None

    def get_script_path(self, script_name: str) -> Optional[Path]:
        if not self.skill_dir:
            return None
        script_path = self.skill_dir / "scripts" / script_name
        if script_path.exists():
            return script_path
        return None

    def get_reference_path(self, ref_name: str) -> Optional[Path]:
        if not self.skill_dir:
            return None
        ref_path = self.skill_dir / "references" / ref_name
        if ref_path.exists():
            return ref_path
        root_ref = self.skill_dir / ref_name
        if root_ref.exists():
            return root_ref
        return None

    def has_tools(self) -> bool:
        if self.provides_tools:
            return True
        tools_path = self.skill_dir / "tools.py" if self.skill_dir else None
        return tools_path is not None and tools_path.exists()

    def load_tools_module(self) -> Optional[Any]:
        if not self.skill_dir:
            return None
        tools_path = self.skill_dir / "tools.py"
        if not tools_path.exists():
            return None
        import importlib.util
        try:
            spec = importlib.util.spec_from_file_location(
                f"skill_tools_{self.name}",
                str(tools_path),
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
        except Exception as e:
            logger.warning(f"加载 skill {self.name} 的 tools.py 失败: {e}")
        return None

    def __repr__(self) -> str:
        return f"Skill(name={self.name}, v={self.version}, scripts={len(self.scripts)}, refs={len(self.references)}, assets={len(self.assets)})"


def _parse_skill_md(skill_md_path: Path) -> Optional[Skill]:
    """
    解析 SKILL.md 文件
    
    Args:
        skill_md_path: SKILL.md 文件路径
        
    Returns:
        Skill 对象，解析失败返回 None
    """
    try:
        content = skill_md_path.read_text(encoding='utf-8')

        lines = content.split('\n')

        if not lines or not lines[0].strip().startswith('---'):
            logger.warning(f"SKILL.md 缺少 frontmatter: {skill_md_path}")
            return None

        frontmatter_end = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                frontmatter_end = i
                break

        if frontmatter_end == -1:
            logger.warning(f"SKILL.md frontmatter 格式错误: {skill_md_path}")
            return None

        frontmatter_text = '\n'.join(lines[1:frontmatter_end])
        prompt_text = '\n'.join(lines[frontmatter_end + 1:])

        frontmatter = yaml.safe_load(frontmatter_text)

        if not frontmatter or 'name' not in frontmatter:
            logger.warning(f"SKILL.md 缺少必要的 name 字段: {skill_md_path}")
            return None

        description = frontmatter.get('description', '')
        if not description:
            logger.warning(f"SKILL.md 缺少 description 字段（触发条件不明确）: {skill_md_path}")

        body_lines = [l for l in prompt_text.split('\n') if l.strip()]
        if len(body_lines) > _SKILL_BODY_SOFT_LIMIT:
            logger.warning(
                f"SKILL.md body 有 {len(body_lines)} 行（建议不超过 {_SKILL_BODY_SOFT_LIMIT} 行），"
                f"考虑将详细内容移至 references/ 目录: {skill_md_path}"
            )

        skill_dir = skill_md_path.parent
        scripts = []
        references = []
        assets = []

        scripts_dir = skill_dir / "scripts"
        if scripts_dir.exists() and scripts_dir.is_dir():
            scripts = [f.name for f in scripts_dir.iterdir()
                       if f.is_file() and f.suffix == '.py' and not f.name.startswith('_')]

        refs_dir = skill_dir / "references"
        if refs_dir.exists() and refs_dir.is_dir():
            for ref_file in refs_dir.iterdir():
                if ref_file.is_file() and ref_file.suffix in ['.md', '.txt', '.pdf']:
                    references.append(str(ref_file.relative_to(skill_dir)))

        assets_dir = skill_dir / "assets"
        if assets_dir.exists() and assets_dir.is_dir():
            for asset_file in assets_dir.iterdir():
                if asset_file.is_file():
                    assets.append(str(asset_file.relative_to(skill_dir)))

        has_tools = bool(frontmatter.get('provides_tools')) or (skill_dir / "tools.py").exists()
        is_knowledge_only = len(scripts) == 0 and not has_tools

        skill = Skill(
            name=frontmatter.get('name', skill_dir.name),
            description=description,
            prompt=prompt_text.strip(),
            scripts=scripts,
            references=references,
            assets=assets,
            is_knowledge_only=is_knowledge_only,
            skill_dir=skill_dir,
            version=str(frontmatter.get('version', '1.0')),
            provides_tools=frontmatter.get('provides_tools', []),
            category=frontmatter.get('category', 'execution' if scripts else 'knowledge'),
            compatibility=frontmatter.get('compatibility'),
        )

        logger.debug(f"解析技能: {skill.name} (scripts={len(scripts)}, refs={len(references)}, assets={len(assets)})")

        # 安全扫描：检查 SKILL.md 内容是否包含威胁模式
        try:
            from floodmind.agent.runtime.services.permission_service import get_permission_service
            svc = get_permission_service()
            if svc:
                result = svc.scan_content_threats(prompt_text)
                if result.threat_detected:
                    logger.warning(
                        f"Skill {skill.name} 内容命中威胁模式，跳过加载: {result.threat_types}"
                    )
                    return None
        except Exception as e:
            logger.debug(f"Skill 安全扫描跳过: {e}")

        return skill

    except Exception as e:
        logger.error(f"解析 SKILL.md 失败 {skill_md_path}: {e}")
        return None


def discover_skills(skills_dir: Path) -> List[Skill]:
    """
    发现目录中的所有技能
    
    扫描指定目录下的所有 SKILL.md 文件，解析并返回技能列表。
    
    Args:
        skills_dir: 技能目录路径
        
    Returns:
        技能列表
    """
    if not skills_dir.exists():
        logger.warning(f"技能目录不存在: {skills_dir}")
        return []
    
    skills = []
    
    for skill_md in skills_dir.glob("*/SKILL.md"):
        skill = _parse_skill_md(skill_md)
        if skill:
            skills.append(skill)
    
    logger.info(f"在 {skills_dir} 中发现 {len(skills)} 个技能")
    return skills


def discover_skills_from_roots(roots: List[Path]) -> List[Skill]:
    """
    从多个根目录发现技能
    
    扫描多个根目录，合并所有发现的技能。
    
    Args:
        roots: 根目录列表
        
    Returns:
        合并后的技能列表
    """
    all_skills = []
    seen_names = set()
    
    for root in roots:
        if not root.exists():
            logger.debug(f"技能根目录不存在: {root}")
            continue
        
        skills = discover_skills(root)
        
        for skill in skills:
            if skill.name not in seen_names:
                all_skills.append(skill)
                seen_names.add(skill.name)
            else:
                logger.debug(f"跳过重复的技能: {skill.name}")
    
    logger.info(f"总共发现 {len(all_skills)} 个技能")
    return all_skills


def generate_skill_catalog(skills: List[Skill]) -> str:
    if not skills:
        return "当前没有可用技能。"

    lines = ["我目前具备以下技能，可用于处理各类专业任务：", ""]

    for skill in skills:
        desc = skill.description if skill.description else "请调用 get_skill 获取详细说明"
        suffix = ""
        if skill.provides_tools:
            suffix = f" [提供工具: {', '.join(skill.provides_tools)}]"
        elif skill.has_tools():
            suffix = " [提供工具: 详见 tools.py / get_skill]"
        compat = f" [依赖: {skill.compatibility}]" if skill.compatibility else ""
        lines.append(f"- **{skill.name}**（{skill.category}）：{desc}{suffix}{compat}")

    lines.extend([
        "",
        "所有技能在使用前均需先调用 `get_skill` 获取详细说明，确保参数和流程准确合规。",
        "如需了解某项技能的具体用法（例如参数说明、脚本列表、参考文档），请告诉我技能名称，我将立即为您查询。"
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 运行时注册（编程式，用于 SDK 嵌入场景）
# ---------------------------------------------------------------------------

def register_skill(skill: Skill) -> None:
    """将 Skill 对象注册到全局注册表（编程式，无需 SKILL.md 文件）。

    用法:
        from floodmind.skills import register_skill, Skill
        register_skill(Skill(name="my-skill", description="...", prompt="..."))

    委托到唯一权威源 ``SkillRegistry``（去重：同名替换）。懒导入避免 base↔registry 环。
    """
    from floodmind.skills.registry import get_skill_registry
    get_skill_registry().register_skill(skill)

