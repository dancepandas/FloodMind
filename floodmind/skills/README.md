# floodmind/skills — Skill 体系与内置技能

Skill 的发现、解析、注册、查询与生命周期管理，以及随包发布的只读内置技能。

## 核心模块

| 文件 | 职责 |
| --- | --- |
| `base.py` | `Skill` 数据结构、`SKILL.md` 解析、发现与 catalog 生成 |
| `registry.py` | `SkillRoot`、`SkillRegistry`、按根优先级发现与刷新 |
| `skill_curator.py` | 使用统计、stale/archived 生命周期，只归档不删除 |
| `__init__.py` | 公共导出与全局兼容视图 |

## 技能组织

每个技能是一个目录，必需 `SKILL.md`（YAML frontmatter + Markdown 正文），可选 `scripts/`、`references/`、`assets/`、`templates/`。frontmatter 至少包含 `name`、`description`。

内置技能：`csv`、`data-analysis`、`doc-coauthoring`、`docx`、`mcp-builder`、`pdf`、`plotting`、`pptx`、`skill-creator`、`xlsx`。

## 发现优先级

`builtin`（本目录，500）> `host`（宿主显式 `skill_roots`，400）> `project`（仓库根 `skills/`，300）> `.claude/skills`（200）> `ephemeral`（100）。同名技能按优先级取胜。

## 约定

- 技能名禁止 `..`、`<>:"/\|?*`、控制字符与 Windows 保留名。
- 根路径或技能目录不得是符号链接/junction。
- 可写根默认是仓库根 `skills/`；内置技能只读，Curator 不修改也不删除。
- 新增技能需同步 `pyproject.toml` 的 package-data，否则 wheel 安装后缺失。
- 嵌入式场景使用 `create_skill_registry()` 为每个 Agent 建独立 registry，避免全局单例串味。
