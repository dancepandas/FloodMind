---
name: skill-creator
description: 引导用户创建符合 FloodMind 规范、可被自动注册的自定义 skill。当用户想把某段重复工作流封装成 skill、询问 skill 怎么写/放哪里、或想新建一个能力时使用。即便用户只是含糊地说"帮我做个能干 X 的东西"，只要本质是可复用的工作流封装，就用本 skill。
---

# Skill Creator（FloodMind 元 skill）

引导用户创建一个 **FloodMind 能自动注册、能真正跑起来**的自定义 skill。本 skill 只做这一件事——不负责对已有 skill 做定量评估或触发率优化（那需要独立的评估闭环，不在范围内）。

## 你要达成什么

结束时，用户手里应有一个放在正确位置、符合规范、被 FloodMind 自动加载的 skill 目录。具体地：

1. 明确这个 skill 做什么、何时触发
2. 在正确的注册目录下建好 `<skill-name>/SKILL.md`（含合规 frontmatter）
3. 按需补 `scripts/` / `references/` / `assets/` / `tools.py`
4. 让用户知道怎么验证它被注册、怎么触发

---

## 一、先懂 FloodMind 的 skill 注册机制

这是"放对位置"的依据，必须先讲清楚，否则用户建了 skill 也加载不进来。

### 注册根（自动扫描，无需手动注册）

FloodMind 启动时会扫描以下目录下的所有 `*/SKILL.md`（`floodmind/skills/__init__.py`）：

| 目录 | 用途 |
|---|---|
| `floodmind/skills/` | **内置 skill**（随 FloodMind 发布，不建议用户往这里塞） |
| `<项目根>/skills/` | 项目级自定义 skill（推荐用户放这里） |
| `<项目根>/.claude/skills/` | 项目级自定义（Claude 风格路径，同样支持） |

**用户自定义 skill 应放 `<项目根>/skills/<skill-name>/`**。放好后无需改任何代码，FloodMind 启动时自动发现。运行期想不重启就刷新，调用 `refresh_skill_registry()`。

### 三层渐进披露（progressive disclosure）

skill 内容按需加载，不是一次性全塞进上下文：

1. **元数据**（name + description）——始终在 agent 上下文里（catalog），约几十字。**description 是触发依据**，必须写清"做什么 + 何时用"。
2. **SKILL.md body**——agent 决定用这个 skill 时，调用 `get_skill(name)` 把 body 加载进上下文。**建议 ≤500 行**（超了启动时 warning，但不阻止）。
3. **scripts / references / assets**——按需读取或执行，不占常驻上下文。

理解这点很重要：**SKILL.md body 不是越长越好**。把详细参考、大段示例、多分支说明移到 `references/`，body 只留"让 agent 能正确执行任务的最小必要指令 + 指向 references 的明确入口"。

---

## 二、skill 目录规范

```
<skill-name>/
├── SKILL.md          # 必填：frontmatter + 执行指令
├── scripts/          # 可选：确定性/重复性任务的 .py 脚本（非下划线开头自动发现）
├── references/       # 可选：按需加载的参考文档（.md/.txt/.pdf）
├── assets/           # 可选：产出用的模板、图标、字体等资源
└── tools.py          # 可选：若 skill 提供新工具，FloodMind 会自动 import 加载
```

### SKILL.md frontmatter 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | skill 标识，与目录名一致 |
| `description` | ✅ | **触发依据**：做什么 + 何时触发。见下方"写好 description" |
| `version` | ❌ | 版本号，默认 `1.0` |
| `category` | ❌ | `execution`（有脚本/工具，默认）或 `knowledge`（纯知识） |
| `provides_tools` | ❌ | 列出 `tools.py` 提供的工具名 |
| `compatibility` | ❌ | 依赖说明（如依赖某 MCP、某运行时） |

无 `scripts/` 且无 `tools.py` 的 skill 会被识别为 `is_knowledge_only`（纯知识型）。

### 安全扫描

FloodMind 加载 skill 时会做内容安全扫描（`permission_service.scan_content_threats`），命中威胁模式的 skill 会被跳过。所以 skill 内容要正经、可审计，不要夹带越权/数据外泄类内容。

---

## 三、创建流程

### 第 1 步：捕获意图

先搞清楚用户要的到底是什么。当前对话里可能已经有线索（用户说"把这个流程做成 skill"），优先从历史里提取：用过哪些工具、步骤顺序、修正点、输入输出格式。然后补齐：

1. 这个 skill 让 FloodMind 能做什么？
2. 什么场景/用户话语应该触发它？
3. 期望的输出格式是什么？（文件？数据？一段回复？）
4. 有没有可客观验证的成功标准？（有 → 适合写测试脚本；纯主观输出 → 不必）

### 第 2 步：访谈边界

主动问清楚：输入输出格式、边界情况、示例文件、依赖（是否依赖某 MCP、某 Python 包、某运行时）。**这些没理清就动手写，后面一定返工。**

如果用户也不确定某细节，可以先用最小可行版本，跑通再迭代。

### 第 3 步：写 SKILL.md

按下面的 Writing Guide 写。写完前先确认 frontmatter 合规（name + description 必填）。

### 第 4 步：放到注册目录 + 校验

- 目录建在 `<项目根>/skills/<skill-name>/`（用户自定义）或 `floodmind/skills/`（要内置的话）
- 跑校验脚本确认结构合规：
  ```bash
  cd floodmind/skills/skill-creator && python -m scripts.quick_validate <skill-path>
  ```
- 重启 FloodMind（或调 `refresh_skill_registry()`），在日志里确认"发现 N 个技能"计数增加、没有 frontmatter/body 警告。

### 第 5 步：让用户验证触发

告诉用户：用一句贴近真实场景的话提问，看 FloodMind 是否在 catalog 里选用了这个 skill（或让用户主动 `get_skill(<name>)`）。触发不准 → 回去调 description，不是调 body。

---

## 四、Writing Guide（核心约束）

这部分是写好 skill 的关键，请认真遵循。

### 4.1 写好 description（最重要）

`description` 是 FloodMind 决定是否调用 skill 的**唯一常驻线索**。两条要求：

- **写清"做什么" + "何时用"**：所有"何时使用"的信息放 description，**不要放 body**。
- **适当"推一把"**：FloodMind 默认倾向"少触发"（怕误用）。所以 description 要明确列出触发场景，甚至稍微激进。例如与其写"做水文预报"，不如写"做水文预报。当用户提到径流/入库流量/洪水预报/水库断面计算、或上传水文数据要求预测时，都应使用本 skill，即使用户没明说'预报'二字。"

### 4.2 body 精简、关键前置

- **≤500 行**是软目标。超了就把详细内容移 `references/`，body 里留指针（"详细参数见 `references/xxx.md`"）。
- **执行步骤前置**：用户要能照着 body 头几段就开始干活，不要把"怎么跑"埋在第 400 行。
- **必填字段/校验要求醒目列在顶部**：如果 skill 产出的结果会被下游校验（如必须含某字段、某格式），**在 body 开头用一个显眼的列表列出所有必填项**。例：

  ```markdown
  ## ⚠️ 结果必填字段（缺一会被下游校验拒绝）
  - `modelRunParam`：模型运行参数（必须非空）
  - `forecastSeries`：预报序列（数组）
  - `stationId`：断面/站点编号
  ```

  这一条能直接避免"agent 跑到最后才发现缺字段、整轮失败"的浪费。

### 4.3 写作模式

- **用祈使句**："读取 X""校验 Y"，而不是"你应该读取 X"。
- **定义输出格式用模板**：
  ```markdown
  ## 结果结构
  严格按此结构输出 result.json：
  { "stationId": "...", "series": [...] }
  ```
- **用示例**：给 1-2 个真实输入→输出示例，比解释 10 行规则管用。
- **重复性工作抽成脚本**：如果发现 agent 每次都要写类似的辅助代码（如"读 xlsx 转 json"），就把它固化进 `scripts/`，body 里只说"调 `scripts/xxx.py`"。这避免每次调用都重新发明轮子、还容易写错。

### 4.4 写作风格

- **解释 why，少用强硬 MUST**：今天的 LLM 足够聪明，给它理由比堆"必须/禁止"更有效。尽量说明"为什么要这么做"，让 agent 理解后能举一反三，而不是死记硬背。如果发现自己在大量使用全大写的 ALWAYS/NEVER，那是黄色警告——试着改写成"做 X 的原因是 Y，所以请……"。
- **通用而非过拟合**：写出来的 skill 要能应对一类任务，而不是只针对某个具体例子有效。不要把某个测试用例的特定值写死进 skill。
- **写完用新鲜眼光复审一遍**：通读，删掉没在"拉货"的内容（不帮助执行的废话、重复说明、过度限制）。

---

## 五、skill 里能用什么工具/MCP

创建 skill 时，要基于 FloodMind 实际可用的能力写指令，不要假设 Claude Code 的工具集：

- **通用工具**：`Bash`（Windows 下是 PowerShell，注意别用 `&&`，用 `;` 或写脚本文件）、`Read`、`Grep`、`Glob`、`Write`、`Edit`、`GetSkill`（加载其他 skill）、`ExperienceSearch`（查历史经验）
- **委派**：`SubAgent` / `ParallelTask`（把子任务交给 specialist）
- **MCP**：已接入的外部 MCP（如 `hydro-rag`，提供检索类工具）。skill 里用到 MCP 工具时，写明工具全名 `mcp:<server>:<tool>` 和参数
- **本 skill 自带工具**：放 `tools.py`，在 frontmatter `provides_tools` 声明，FloodMind 自动加载

如果 skill 强依赖某 MCP 或运行时，在 frontmatter `compatibility` 注明（如 `compatibility: "需 hydro-rag MCP"`），catalog 会展示该依赖。

---

## 六、可选：打包分发

skill 写好、校验通过后，如果用户想把它打包成单文件分发：

```bash
cd floodmind/skills/skill-creator && python -m scripts.package_skill <path/to/skill-folder>
```

产物是 `.skill` 包，可拷贝到其他 FloodMind 项目的注册目录解压使用。

---

## 检查清单（交付前自检）

写完一个 skill，对照确认：

- [ ] 放在 `<项目>/skills/` 或 `floodmind/skills/` 下，目录名 = frontmatter `name`
- [ ] frontmatter 有 `name` + `description`，description 含"做什么 + 何时触发 + 推一把"
- [ ] body ≤500 行，超了的部分已移 `references/` 并留指针
- [ ] 执行步骤前置；若有下游校验必填字段，顶部醒目列出
- [ ] 用祈使句、解释 why、给示例；重复工作已固化进 `scripts/`
- [ ] `quick_validate` 通过；重启后日志无 frontmatter/body 警告
- [ ] 用真实场景话术验证能触发

做到这些，用户就得到了一个 FloodMind 真能用、能注册、能复用的 skill。
