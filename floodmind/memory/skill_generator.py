"""
经验→Skill 自动生成

当经验树分支密封(seal)后，检查该分支叶子数是否达到阈值，
满足条件则调用 LLM 合成 SKILL.md，写入 skills/ 目录。
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from floodmind.memory.experience_tree import ExperienceLeaf

logger = logging.getLogger(__name__)

SKILL_GEN_PROMPT = """你是一个技能文档编写专家。请基于以下历史任务执行经验，生成一份 SKILL.md 技能文档。

## 经验来源
{experiences}

## 摘要
{summary_text}

## 输出格式
请输出一份完整的 SKILL.md 内容，包含以下部分：

### YAML Frontmatter
```yaml
---
name: {skill_slug}
description: 一句话描述
version: 0.1.0
status: draft
created_at: {created_at}
source: auto-generated-from-experience
trigger_keywords: [keyword1, keyword2]
---
```

### 技能目标
- 一句话说明这个技能做什么

### 适用场景
- 列举 2-3 个典型使用场景

### 执行步骤
- 按顺序列出关键步骤及注意事项

### 常见坑点与解决方案
- 表格形式：坑点 | 原因 | 解决方案

### 可复用代码
- 代码片段（如果经验中包含）

## 规则
- 所有内容基于提供的经验，不要杜撰
- 步骤要可操作、具体
- 如果经验不足以生成完整 SKILL.md，输出 "SKIP"
- 标注 status: draft，等待人工审核
"""


def generate_skill_from_branch(
    leaves: List[ExperienceLeaf],
    summary_text: str,
    skill_slug: str,
    llm_service=None,
) -> Optional[str]:
    """基于经验分支生成 SKILL.md 内容"""
    if not leaves or len(leaves) < 2:
        return None

    # 质量门槛：importance > 0.6 的叶子占比 > 50%
    high_quality = sum(1 for l in leaves if l.importance >= 0.6)
    if high_quality / len(leaves) < 0.5:
        logger.info("Skill 生成跳过: 高质量经验占比不足 (%d/%d)", high_quality, len(leaves))
        return None

    experiences = "\n\n".join(
        f"经验{i+1}:\n{leaf.to_searchable_text()}"
        for i, leaf in enumerate(leaves)
    )

    keywords = list(set(kw for leaf in leaves for kw in leaf.domain_keywords))[:5]

    prompt = SKILL_GEN_PROMPT.format(
        experiences=experiences,
        summary_text=summary_text,
        skill_slug=skill_slug,
        created_at=datetime.now().strftime("%Y-%m-%d"),
    )

    if llm_service is None:
        return _fallback_skill_md(leaves, summary_text, skill_slug, keywords)

    try:
        response = llm_service.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2000,
        )
        raw = response.content if hasattr(response, 'content') else str(response)
        text = raw.strip()

        if text.startswith("SKIP") or "SKIP" in text[:100]:
            logger.info("Skill 生成跳过: LLM 判断经验不足")
            return None

        return text
    except Exception as e:
        logger.error("LLM Skill 生成失败: %s", e)
        return _fallback_skill_md(leaves, summary_text, skill_slug, keywords)


def _fallback_skill_md(leaves, summary_text, skill_slug, keywords) -> str:
    """无 LLM 时的规则化 Skill 生成"""
    pitfalls = []
    solutions = []
    for leaf in leaves:
        pitfalls.extend(leaf.pitfalls)
        solutions.extend(leaf.solutions)

    unique_pitfalls = list(dict.fromkeys(pitfalls))[:5]
    unique_solutions = list(dict.fromkeys(solutions))[:5]
    success_rate = sum(1 for l in leaves if l.final_outcome == "success") / len(leaves)

    lines = [
        "---",
        f"name: {skill_slug}",
        "description: 自动生成的技能文档",
        "version: 0.1.0",
        "status: draft",
        f"created_at: {datetime.now().strftime('%Y-%m-%d')}",
        "source: auto-generated-from-experience",
        f"trigger_keywords: {json.dumps(keywords, ensure_ascii=False)}",
        "---",
        "",
        f"# {skill_slug}",
        "",
        "## 技能目标",
        f"基于 {len(leaves)} 条历史经验自动生成。成功率: {success_rate:.0%}。",
        "",
        "## 摘要",
        summary_text,
        "",
        "## 常见坑点",
    ]
    for p in unique_pitfalls:
        lines.append(f"- {p}")
    lines.append("")
    lines.append("## 解决方案")
    for s in unique_solutions:
        lines.append(f"- {s}")
    lines.append("")

    return "\n".join(lines)


def write_skill_to_disk(skill_content: str, skill_dir: str) -> str:
    """将 SKILL.md 写入 skills 目录"""
    os.makedirs(skill_dir, exist_ok=True)
    filepath = os.path.join(skill_dir, "SKILL.md")

    if os.path.exists(filepath):
        logger.warning("Skill 文件已存在，跳过写入: %s", filepath)
        return ""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(skill_content)

    logger.info("Skill 生成完成: %s", filepath)
    return filepath
