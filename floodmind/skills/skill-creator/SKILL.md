---
name: skill-creator
description: 引导创建符合 FloodMind 规范、可被 SkillRegistry 自动发现的 Skill。当用户想把重复工作流封装成 skill、询问 skill 怎么写/放哪里、或想新建一个能力时使用。即便用户只是含糊地说"帮我做个能干 X 的东西"，只要本质是可复用的工作流封装，就用本 skill。
---

# Skill Creator（FloodMind 元 skill）

引导创建一个 **FloodMind 能自动发现、Agent 能自行维护**的 Skill。本 skill 只做这一件事——不负责对已有 skill 做定量评估或触发率优化。

## 你要达成什么

结束时，用户手里应有一个被 FloodMind 正确加载的 Skill。有两路径可选：

1. **Agent 自创建（推荐）**：调用 `CreateSkill` 工具直接落盘 → 自动 `RefreshSkills`
2. **手动创建**：写 SKILL.md 到 writable_root → 调 `RefreshSkills` 或重启

---

## 一、FloodMind Skill 体系统一架构

理解下面的架构才能做出正确决策。

### SkillRegistry 单例（唯一权威源）

所有 Skill 查询/发现/catalog/CRUD 统一经 `get_skill_registry()` 这一个入口。线程安全（`threading.Lock`）。

### 三根自动发现（CWD 无关，基于包定位）

| 根目录 | 用途 |
|---|---|
| `floodmind/skills/` | 内置 Skill（随包发布，不建议用户写） |
| `<项目根>/skills/` | **用户/项目 Skill**——`CreateSkill` 落盘目标，也是手动创建的推荐位置 |
| `<项目根>/.claude/skills/` | Claude Code 兼容 |

启动时 `SkillRegistry._scan()` 扫描所有根的 `*/SKILL.md`。加 `refresh_callbacks` 自动清 `GetSkill` 的 lru_cache。

### 三层渐进披露

1. **元数据**（name + description）→ 始终在 agent system prompt 的 `catalog` 里。**description 是触发依据**
2. **SKILL.md body** → agent 调用 `GetSkill(name)` 时加载进上下文。建议 ≤500 行
3. **scripts / references / assets** → 按需读取或执行，不占常驻上下文

### Agent 自维护 CRUD 工具

FloodMind 可在运行时**自己创建/修改/删除 Skill**（仅 orchestrator 可用）：

| 工具 | 功能 | policy |
|---|---|---|
| `CreateSkill(name, description, body)` | 写 writable_root/name/SKILL.md → refresh | state_write |
| `UpdateSkill(name, action, content)` | append/replace_body/replace_section/remove_section → refresh | state_write |
| `RemoveSkill(name)` | 委托 curator.archive_skill → .archived/（可恢复） | state_write |
| `ListSkills` | 列出所有 skill（name/version/category/source） | readonly |
| `RefreshSkills` | 重扫所有根 + 重建 system prompt | state_write |

**写操作全部过 `_validate_skill_name`**（拒绝 `/`、`\\`、`..`、`.` 开头），防路径穿越。

### SkillCurator 自动维护

- `GetSkill` 每次调用自动 `record_skill_usage`（累计使用次数/成功率、自动 re-activate stale）
- 定期巡检（6h）：标记 stale（30d 未用）→ 归档（90d 未用）→ 重复检测
- 归档到 `.archived/`，可 `restore_skill`，从不硬删除

---

## 二、创建流程

### 路径 A：Agent 自创建（推荐）

直接用 `CreateSkill` 工具，一步完成写盘 + 注册 + refresh：

```
CreateSkill(
    name="my-skill",           # kebab-case，唯一标识
    description="做什么 + 何时触发",  # 触发依据，见 §三
    body="## 执行步骤\n1. ...\n2. ...",  # SKILL.md body
    version="1.0",             # 可选
    category="execution",      # 可选：execution(默认) 或 knowledge
)
```

要改已创建的 skill——`UpdateSkill`（支持 append/replace_body/replace_section/remove_section）。要归档——`RemoveSkill`（移到 `.archived/`，可恢复）。

### 路径 B：手动创建

1. 在 `<项目根>/skills/<skill-name>/` 建目录
2. 写 `SKILL.md`（含合规 frontmatter）
3. 放 `scripts/` / `references/` / `assets/`（按需）
4. 调用 `RefreshSkills` 或重启 FloodMind

---

## 三、SKILL.md 编写规范

### Frontmatter 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | skill 标识，kebab-case，与目录名一致 |
| `description` | ✅ | **触发依据**：做什么 + 何时触发。见下方"写好 description" |
| `version` | ❌ | 默认 `1.0` |
| `category` | ❌ | `execution`（有脚本/工具，默认）或 `knowledge`（纯知识） |
| `provides_tools` | ❌ | `tools.py` 提供的工具名列表 |
| `compatibility` | ❌ | 依赖说明（如依赖某 MCP、某 Python 包） |

无 `scripts/` 且无 `tools.py` 的 skill 自动标记 `is_knowledge_only`。

### 写好 description（最重要）

`description` 是 FloodMind 决定是否调用 skill 的**唯一常驻线索**：

- **写清"做什么" + "何时用"**：所有触发信息放 description，**不要放 body**
- **适当"推一把"**：FloodMind 默认倾向少触发（怕误用），description 要明确列出触发场景。例：与其写"做水文预报"，不如写"做水文预报。当用户提到径流/入库流量/洪水预报，或上传水文数据要求预测时，都应使用本 skill，即使用户没明说'预报'二字"

### Body 编写原则

- **≤500 行**软目标。超了就把详细内容移 `references/`，body 留指针（"详细参数见 `references/xxx.md`"）
- **执行步骤前置**：照着 body 头几段就能开始干活
- **必填字段/校验要求醒目列在顶部**：用 `## ⚠️ 结果必填字段` 开头
- **用祈使句**："读取 X""校验 Y"
- **用示例**：给 1-2 个真实输入→输出示例
- **解释 why**：给理由比堆"必须/禁止"更有效
- **重复性工作固化进 `scripts/`**：body 只说"调 `scripts/xxx.py`"
- **通用而非过拟合**：应对一类任务，不针对某个具体例子写死

### 安全扫描

FloodMind 加载 SKILL.md 时对 body 文本做 `scan_content_threats`，命中威胁模式的 skill 被跳过。

---

## 四、目录规范

```
<skill-name>/
├── SKILL.md          # 必填：frontmatter + body
├── scripts/          # 可选：.py 脚本（非下划线开头自动发现）
├── references/       # 可选：按需加载的参考文档（.md/.txt/.pdf）
├── assets/           # 可选：模板、图标、字体等资源
└── tools.py          # 可选：若提供新工具，FloodMind 自动 import
```

---

## 五、Skill 里可用的工具

- **通用**：`Bash`（Windows 用 `;` 不用 `&&`）、`Read`、`Grep`、`Glob`、`Write`、`Edit`、`GetSkill`、`ExperienceSearch`
- **委派**：`SubAgent` / `ParallelTask`
- **Skill 自维护**：`CreateSkill` / `UpdateSkill` / `RemoveSkill` / `ListSkills` / `RefreshSkills`
- **MCP**：已接入的 MCP 工具名格式 `mcp:<server>:<tool>`

---

## 检查清单

- [ ] name 为 kebab-case，description 含"做什么 + 何时触发 + 推一把"
- [ ] body ≤500 行，超了部分已移 `references/` 并留指针
- [ ] 执行步骤前置；有下游校验则顶部醒目列出必填字段
- [ ] 用祈使句、解释 why、给示例；重复工作固化进 `scripts/`
- [ ] 若用 Agent 自创建：`CreateSkill` 一步完成
- [ ] 若手动创建：放 `<项目根>/skills/` 下，调 `RefreshSkills` 生效
- [ ] 用真实场景话术验证能触发（调 `GetSkill(name)` 检查 body 内容）
