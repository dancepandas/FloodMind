# FloodMind 项目知识图谱

> 快照时间：2026-10-19
> 代码基线：`master` 工作区（包含 harness 调整与遗留内容清理，HEAD `b21f2e5`）
> 生成方式：Python AST 静态解析 + 人工语义分层映射，工具见 `scripts/knowledge_graph/`

这套知识图谱的目标不是再写一份“项目介绍”，而是建立一张可持续维护的**结构事实网**：从系统边界、子系统、模块、符号，一直到导入/调用/继承关系和落盘路径。后续做功能完善、缺陷定位、架构调整时，先在这里定位，再进代码验证。

---

## 1. 当前快照

| 指标 | 数值 |
|---|---:|
| 文件 | 380 |
| 符号 | 4608 |
| 关系边 | 8677 |
| import 边 | 1080 |
| call 边 | 2963 |
| 文件级循环 | 4 |
| 未解析内部导入 | 1 |
| 无入边文件 | 213 |
| 分层 | 21 |

范围说明：只统计主项目源码（`floodmind/`、`tests/`、`scripts/`、`skills/`、`evals/`、`benchmarks/`）。以下独立/实验/产物目录不进入图：`deepseek-harness/`、`desktop/`、`tools/`、`contrib/`、`node_modules/`、`dist/`、`build/`、`runtime/`。旧 `web/`、`tui-ts/`、`sdk-ts/` 已删除。

---

## 2. 产物地图

| 文件 | 内容 | 使用场景 |
|---|---|---|
| [01-system-overview.md](./01-system-overview.md) | 系统定位、宿主拓扑、分层总表、主调用链、核心不变量 | 第一次进入项目 |
| [02-sdk-entrypoints-config.md](./02-sdk-entrypoints-config.md) | SDK 公共 API、生命周期、事件契约、配置面、打包入口 | 宿主集成、改公共 API |
| [03-agent-execution.md](./03-agent-execution.md) | 执行状态机、一次轮次调用链、提示词/压缩/并发 | 改执行逻辑、查轮次问题 |
| [04-runtime-kernel.md](./04-runtime-kernel.md) | Canonical 契约、Reducer、Journal、工具事务、权限、Checkpoint、沙箱、子线程 | 改运行时与持久化 |
| [05-memory.md](./05-memory.md) | 记忆分层、读写路径、压缩、会话隔离 | 改记忆、查上下文丢失 |
| [06-tools-skills-mcp.md](./06-tools-skills-mcp.md) | ToolSpec/AgentTool、内置工具、Skill 注册表与策展、MCP 池 | 加工具/技能、查工具不可见 |
| [07-model-layer.md](./07-model-layer.md) | ModelClient、Provider Codec、流式事件、usage/reasoning、配置解析 | 换模型、接供应商 |
| [08-frontends-and-legacy.md](./08-frontends-and-legacy.md) | Gateway/Desktop 边界与已删除旧前端记录 | 判断宿主边界、查清理历史 |
| [09-persistence-and-config.md](./09-persistence-and-config.md) | 目录布局、Journal/Checkpoint/会话/产物、环境变量 | 排障、迁移、清理 |
| [10-tests-and-quality.md](./10-tests-and-quality.md) | 测试主题地图、子系统覆盖、运行方式、空白 | 回归、补测试 |
| [11-risk-register.md](./11-risk-register.md) | 风险、债务、死代码、循环依赖、文档漂移 | 架构评审、排优先级 |
| [12-change-playbooks.md](./12-change-playbooks.md) | 常见改动的影响面与验证清单 | 动手前查影响面 |

机器可读图与报告在 [`graph/`](./graph/)：

| 文件 | 内容 |
|---|---|
| `graph/graph.json` | 全量节点与边（文件、类、函数、方法；imports/calls/inherits/defines） |
| `graph/subsystems.json` | 语义层子系统节点与依赖边 |
| `graph/metrics.md` / `metrics.json` | 分层规模、跨层依赖、高连接文件/符号、循环、孤立文件 |
| `graph/layer-graph.mmd` / `layer-graph-core.mmd` | 分层依赖图（全量 / 去掉测试与工程脚本） |
| `graph/package-graph.mmd` / `package-graph-core.mmd` | 包级依赖图（全量 / 去掉测试） |
| `graph/subsystem-graph.mmd` | 子系统语义依赖图 |
| `graph/cycles.md` | 文件级循环依赖明细 |
| `graph/unresolved.md` | 指向仓库内命名空间但无法解析的导入 |
| [`graph/graph.html`](./graph/graph.html) | 单文件交互式可视化：文件/分层/子系统三视图，可搜索与下钻符号调用 |

---

## 3. 推荐阅读路径

### 第一次了解项目

1. 本文件 + `01-system-overview.md`
2. `03-agent-execution.md`（一次用户请求怎么跑完）
3. `04-runtime-kernel.md`（事实源、事务、恢复）
4. 按任务选读：记忆、工具/技能、模型、前端

### 修一个具体 bug

1. 在 `graph/metrics.md` 找到相关模块的连接度
2. 用 `graph/graph.json` 查该文件/符号的入边与出边
3. 读对应子系统文档的“关键调用链”和“风险”
4. 用 `12-change-playbooks.md` 的验证清单回归

### 做架构调整

1. `11-risk-register.md` 的循环依赖与双事实源
2. `graph/layer-graph-core.mmd` 看依赖方向
3. 对应子系统文档的“修改影响提示”

---

## 4. 重新生成

```bash
python -m scripts.knowledge_graph
# 或指定输出目录
python -m scripts.knowledge_graph --out docs/knowledge-graph/graph
# 只分析 Python
python -m scripts.knowledge_graph --no-ts
```

每次生成会同时写出 `graph/graph.html`，直接用浏览器打开即可，无需启动服务或联网：

- 文件视图：380 个文件的 import 关系，按分层着色，节点大小反映连接度；
- 分层视图：跨层依赖与边计数，用于判断依赖方向；
- 子系统视图：语义层职责与关系；
- 搜索、拖拽、缩放、单击详情；双击文件或点“展开此文件的符号”进入符号调用图。

查询单个文件或符号：

```bash
# 文件的导入与被导入
python -m scripts.knowledge_graph.query floodmind/agent/native/executor.py

# 只看被谁导入
python -m scripts.knowledge_graph.query floodmind/memory/dual_memory.py --in

# 文件定义的符号与行号
python -m scripts.knowledge_graph.query floodmind/tools/base_tools.py --symbols

# 类的调用关系（自动包含其方法）
python -m scripts.knowledge_graph.query NativeAgentExecutor --out
```

工具细节：

- Python 使用 `ast` 解析，绝对/相对导入都会解析到仓库内文件。
- TypeScript/JavaScript 用正则提取 `import` / `export ... from` / `require` / 动态 `import`，仅解析相对路径与 `@/` 别名。
- 调用边是启发式的：只解析同模块、`from ... import` 的符号、`模块.函数`、`self.method`，不执行代码，不处理反射/动态注册。
- 每次生成会覆盖 `graph/` 下的同名文件。

---

## 5. 图数据 schema

### 节点

```json
{
  "id": "file:floodmind/agent/api.py",
  "kind": "file | class | function | method",
  "name": "api.py",
  "qualified_name": "floodmind.agent.api",
  "path": "floodmind/agent/api.py",
  "language": "python | typescript | javascript",
  "line": 0,
  "end_line": 0,
  "package": "floodmind.agent",
  "layer": "sdk",
  "loc": 559
}
```

符号节点使用 `symbol:<module>.<qualname>`，例如 `symbol:floodmind.agent.api.Agent.run`。

### 边

| kind | src → dst | 含义 |
|---|---|---|
| `defines` | file → symbol | 文件定义符号 |
| `imports` | file → file | 模块导入 |
| `calls` | symbol/file → symbol | 启发式调用 |
| `inherits` | class → class | 类继承 |

### 语义层

`subsystems.json` 是人工维护的职责边界，节点字段包括 `id/title/layers/paths/responsibility`，边字段包括 `src/dst/kind/note`。修改目录边界时，同时更新：

- `scripts/knowledge_graph/layers.py`（路径 → 分层）
- `scripts/knowledge_graph/subsystems.py`（子系统与依赖）

---

## 6. 边界与已知局限

1. **静态视角**：不执行代码，动态导入、`getattr` 懒加载、运行时注册的工具/技能不会自动出现在调用图里。
2. **调用边不完整**：跨模块属性调用、继承来的方法、回调、线程池/队列投递不在 call 图中。定位调用链时以文档中的时序图为准，再用 `grep` 验证。
3. **TypeScript 只有文件级导入**：没有符号与调用边。
4. **独立项目未纳入**：`desktop/`、`deepseek-harness/`、`contrib/` 是独立边界，本图不覆盖。
5. **快照有时效**：文档中的行号和结论以本次基线为准；改动后重新生成图并核对。
6. **文档与代码可能漂移**：`docs/architecture/OVERVIEW.md` 等历史文档未全部同步到当前实现，本图谱以代码为准，差异记录在 `11-risk-register.md`。

---

## 7. 维护约定

- 新增/移动目录：先更新 `layers.py` 与 `subsystems.py`，再重新生成图。
- 新增子系统：在 `subsystems.py` 加节点与边，并在文档中加一节。
- 修改核心契约/状态机：同步更新 `03`/`04` 文档和 `11` 风险表。
- 不在本目录复制大段源码；结论必须能回到 `file:line`。
- 生成物（`graph.json` 等）可以重新生成，但应随代码一起提交，便于 diff 架构变化。
