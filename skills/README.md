# skills — 项目级 Skill（可写）

宿主/项目可加载的技能根目录。这里的技能属于项目级，默认也是可写根，SkillCurator 可以维护它们；内置技能在 `floodmind/skills/`，只读。

## 发现顺序

`floodmind/skills/`（builtin，只读）> 宿主 `Agent(skill_roots=...)`（host）> 本目录（project）> `.claude/skills`（只读）> ephemeral。

同名技能按上述优先级取胜。

## 现有内容

| 目录 | 说明 |
| --- | --- |
| `test-skill/` | 最小测试技能，验证发现、注册与 GetSkill 链路 |
| `shuge-greeting/` | 示例触发型技能（用户提到“树哥”时问候） |
| `.archived/test-skill/` | SkillCurator 归档位置，不会被加载 |

## 新增技能

```bash
mkdir skills/my-skill
cat > skills/my-skill/SKILL.md <<'EOF'
---
name: my-skill
description: 说明这个技能在什么场景下使用
---

# my-skill
执行步骤与注意事项。
EOF
```

frontmatter 至少包含 `name` 与 `description`；缺少 frontmatter 的目录会被跳过。

## 约定

- 只扫描根下一层 `*/SKILL.md`；`.archived/` 不参与发现。
- 可写操作只作用于可写根；内置与宿主只读根不可修改。
- 归档通过 `SkillCurator.archive_skill` 移入 `.archived/`，可恢复，不硬删除。
- 技能根与技能目录不得是符号链接或 Windows junction。
- `.floodmind/` 是本地状态目录，不提交。
