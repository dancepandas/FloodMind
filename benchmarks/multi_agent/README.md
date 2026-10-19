# 多代理收益基线（G-22）

本目录回答一个问题：**某类任务是否值得拆给子代理？** 它提供固定任务集与 single-agent /
delegated 两种策略的可重复对比，不引入多代理框架，也不改变 runtime 行为。

背景：蓝图缺口 G-22 指出项目“无多代理收益基线，无法判断是否值得拆分”。在拿到本机
证据之前，不预先建设框架、协议或调度层。

## 两种策略

两种策略都使用 `floodmind.Agent(bare=False)` 的完整 runtime、相同系统提示词、相同模型
与相同任务 fixture：

| 策略 | 工具面 | 说明 |
|---|---|---|
| `single` | 移除 `SubAgent` / `ParallelTask` | 模型只能自己完成；移除后刷新工具 schema 与系统提示词 |
| `delegated` | 保留 `SubAgent` / `ParallelTask` | 允许但不强制委派，由模型自行决定是否拆分、拆分几次 |

`delegated` 衡量的是“提供拆分能力后的实际效果”，而不是强制拆分；这样对比的是两种
产品配置，而不是两条写死的执行路径。

`single` 的移除是实验控制手段：若工具布局变化导致无法恰好移除两个委派工具，运行器
直接报错，不静默继续（否则两种策略不可比）。

每次执行使用独立临时 workspace：

- fixture 写入 `workspace/`；
- memory 持久化到 `workspace` 之外的临时 `state/`；
- 执行结束后整个临时根目录删除；
- 权限处理器放行临时工作区内的工具调用，使基准可无人值守运行。

两种策略共用同一提示词，只额外告知工作区绝对路径，并说明子任务读写工作区文件时其工作
目录应指向该路径。子代理默认在独立 sandbox 中运行，工作目录不指向父工作区就会看不到
fixture；这条是环境事实，不构成对某种策略的引导。

## 任务集

任务与 fixture 在 `tasks.py` 中声明，覆盖“拆分可能有益”和“拆分无益”两类：

| ID | 类别 | 任务 | 选择依据 |
|---|---|---|---|
| `station-audit` | split_beneficial | 三站点独立巡检 | 三个文件互不依赖，可并行读取 |
| `log-triage` | split_beneficial | 1200 行日志错误统计 | 长上下文可在子代理内消化，父代理只收聚合结果 |
| `single-file-fix` | split_not_beneficial | 单文件一行修复 | 上下文极小，委派交接开销高于收益 |
| `report-from-context` | split_not_beneficial | 根据 notes.md 写事故摘要 | 所有事实必须留在同一上下文，拆分易遗漏或编造 |

成功判定由 `TaskSuccess` 声明，支持：

- `answer_contains_all`：回答必须包含全部文本；
- `answer_contains_any`：回答至少包含一个文本；
- `answer_regex_all`：回答必须匹配全部正则；
- `workspace_contains`：产物文件必须存在且包含指定文本；
- `workspace_not_contains`：产物文件不得包含指定文本。

## 指标

每次策略执行记录：

- 成功率：任务声明的断言是否全部满足，且执行未报错；
- token：`Agent.last_usage` 的 prompt / completion / total；子代理产生的 `token_usage`
  事件经事件总线汇入父流，因此 delegated 的成本包含子代理；
- 墙钟时间：`Agent.run()` 的耗时（不含 Agent 构造）；
- 工具调用：父流可见的 `action_start` 名称序列（含子代理步骤事件）；
- 委派次数：工具名属于 `SubAgent` / `ParallelTask` 的调用数；
- 比值：delegated / single 的 token 与耗时，single 为 0 时记为 `n/a`。
- 失败运行的成本同样计入：超时或报错也消耗预算，不能只统计成功样本。

## 判定规则

默认阈值 `0.9`：

```text
success_ok   = delegated 成功率 >= single 成功率
efficiency_ok = token_ratio <= 0.9 或 time_ratio <= 0.9
worth_splitting = success_ok 且 efficiency_ok 且 delegated 成功率 > 0
且 delegated 实际发生委派
```

即：拆分不能让成功率下降，并且 token 或耗时至少一项至少降低 10%。阈值可通过
`--token-ratio` / `--time-ratio` 调整。分类汇总按 `split_beneficial` /
`split_not_beneficial` 分别应用同一规则，并额外统计“single 成功但 delegated 失败”的
逐任务退化；只要存在退化，该分类统一判定为不建议拆分，避免聚合成功率掩盖任务间交换。
若 delegated 策略没有实际调用委派工具，成本差异无法归因于拆分，同样判定为不建议拆分。

## 运行

默认命令**只列出任务，不执行、不调用模型**：

```bash
python -m benchmarks.multi_agent.runner
python -m benchmarks.multi_agent.runner --list
```

显式 `--live` 才会读取 `~/.floodmind/settings.json` 的模型配置并真实调用：

```bash
python -m benchmarks.multi_agent.runner --live
python -m benchmarks.multi_agent.runner --live --task log-triage --json report.json
python -m benchmarks.multi_agent.runner --live --model <model-key> \
    --token-ratio 0.85 --time-ratio 0.9
```

`--model` 使用模型目录中的 key；缺省使用当前激活模型。CI 不运行 `--live`。

离线复用入口是 `run_benchmark(tasks, executor)`：`executor` 只需实现
`execute(task, workspace, strategy) -> Observation`。`LiveStrategyExecutor` 也接受
`llm_factory`，便于宿主注入测试模型。

## 输出

人类可读表格包含每个任务的 single/delegated 成功率、token、耗时、工具调用、委派次数与
判定；随后按类别汇总。`--json` 额外写出机器可读报告，包含阈值、逐任务运行记录、比值与
判定原因。

## 结论与边界

- 本目录是“测量机制”，不是多代理框架。只有在目标任务的对照结果持续满足判定规则时，
  才值得考虑把拆分策略固化到产品路径。
- 每个任务当前只运行一次，适合建立基线与发现明显退化，不适合估计小方差差异。需要更
  稳定结论时应增加重复次数并比较分布，而不是只看单次比值。
- 结果依赖模型、提示词与任务 fixture。更换模型或任务后应重新测量，不沿用旧结论。
- live 运行会读取用户模型配置；任务 workspace 与 memory 持久化目录是隔离的，但底层
  长期记忆文件仍使用 FloodMind 默认位置，运行前应知悉这一点。
