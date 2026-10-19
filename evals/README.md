# 内部评测集（G-14 / G-17 / C1）

用途：在单元测试之外，用**确定性脚本轨迹**验证 Agent 主循环的跨组件行为，作为提交门禁的一部分。

## 四层集合 + held-out

| 层 | 目的 | 当前用例数（JSON + case pack） |
| --- | --- | --- |
| `smoke` | 每次提交运行，验证主循环基本可用 | 21 = 3 JSON + 18 pack |
| `tool` | 真实工具层：走生产 `ToolExecutionService` 与真实文件工具 | 12 = 4 JSON + 8 pack |
| `regression` | 安全/契约回归，失败即阻断 | 201 = 6 JSON + 195 pack |
| `safety` | 安全边界：越界路径拒绝、planning 硬门、只读对照 | 9 pack |
| held-out | `heldout=true`，默认不跑，定期校准防过拟合 | 10 pack |

全量门禁（`--tier all` 或不带参数）要求四个常规层级全部非空；held-out 不参与门禁。

`tool` / `safety` 层与其余层的区别是工具不模拟：用例声明 `"tool_mode": "real"`，执行器加载真实 `Read`/`Write`/`Glob`/`Grep`/`Edit` 工具，经真实权限检查与 `ToolExecutionService` 执行。路径服务只允许评测临时 workspace 内的读写，workspace 之外的路径一律拒绝；planning 模式用例额外接入生产 `PermissionService`，验证规划模式写类硬门。journal authority 使用空实现，不产生真实 journal。`workspace_files` 用于声明 fixture，`expect.workspace_contains` / `expect.workspace_not_contains` 直接校验工具落盘后的文件状态与副作用缺失。

单元与故障注入回归仍由 `pytest tests/` 承担；本目录只覆盖“模型轨迹 → 执行器 → 工具 → 下一轮上下文”这类跨组件行为。

## Case pack（评测集扩容 C1）

`evals/case_packs.py` 提供 `load_cases()`，以**参数化家族**确定性生成用例包，与 `cases/*.json` 手工用例合并加载：

- 家族示例（`_regression_pack` + `_regression_extended_pack`，ID 前缀 `regression-pack-` / `regression-ext-`）：
  - 中断点扫描：abort_after_model_calls 1..10（单工具与多工具轮两种粒度）、中断永不触发、中断×usage 共存；
  - 迭代上界：max_iterations 1..8（单工具、多工具、截断守卫——未到达的脚本轮绝不被请求）；
  - token 预算：截断位置扫描（第 1..10 轮）、粒度 20/30 扫描、+1/-1 边界、答案轮边界、非法轮计费、total-only usage 形状、预算 0 不限、预算优先于迭代上界；
  - 连续失败：失败 1..4 后恢复、5 次强制终止、软错误内容（status=completed 但内容前缀“错误”）同样计数、计数按工具名隔离与重置、换工具恢复、失败反馈可区分回填；
  - 非法参数：12 种 malformed 形态（含数组/字符串/数字/布尔根、尾逗号、未闭合、坏转义、单引号）、单轮 2/3 个非法调用、非法+合法交替、非法+终态组合、同轮非法+stop（该轮按工具轮处理）；
  - 终态映射：canonical 与别名 wire 值全表（stop/end_turn/tool_calls/tool_use/length/max_tokens/content_filter/filtered/refusal/refused/pause_turn/paused/aborted/error/unknown）、error/timeout 事件文案与 is_timeout；
  - max_tokens 续写：length/max_tokens 别名续写成功、续写耗尽、续写轮带 usage、续写后继续工具调用（held-out）；
  - DOOM LOOP：同参 4 次拦截、异参对照、滑动窗口（同→异→同恢复）、双工具交错、成对重复序列、失败尝试同样计数、按连续签名而非累计；
  - 深链与轮转：单工具深链 2..12、双工具交替 2..12、每轮双工具 2..10、三工具轮转 3 站及 4..8 站、单轮四工具——全部异参避开 DOOM LOOP，逐例断言回填链深度；
  - 多工具部分失败：先错后对、先对后错、2 错 1 对、跨轮失败；
  - 真实工具回归子族（regression 层、tool_mode=real）：读-写-读自愈闭环、覆盖写、空文件、Unicode 文件名、嵌套 Glob/Read、Grep 正则语义（点号匹配任意字符）、写改改读生命周期、大内容回读、未知工具后接真实 Read 恢复；
  - 其他契约：空工具轮（无调用事件按答案轮处理）、工具轮纯推理、工具后同轮文本、用户指令贯穿上下文链；
- 每个用例都带中文 `description` 与真实断言，不凑数；
- 纯函数生成：同一版本多次调用返回等价集合，可在 CI 无密钥环境重复运行；
- 生成用例打 `_generated` 标记，JSON 报告里以 `generated: true` 区分来源。

写家族时必须遵守的执行器语义（case_packs.py 模块 docstring 亦有说明）：

- DOOM LOOP（同工具同参数连续 3 次）在工具执行**之前**检测，优先级高于连续失败终止（5 次）；需要走到连续失败阈值时每次调用参数必须不同；
- scripted 执行器按调用 id（`tool_results`）优先、工具名（`tools`）兜底查结果，同名工具多轮不同结果用 `tool_results` 按调用 id 声明；
- 中断在每轮流式消费完成后生效：`abort_after_model_calls=k` = 前 k-1 轮工具执行 + 第 k 轮整轮丢弃。

## Held-out

`heldout=true` 的用例默认不加载，仅 `--heldout` 参与运行。用途：定期换血/校准，避免评测集被针对性过拟合。held-out 覆盖多类组合场景（错误+非法+恢复混合、预算与迭代上界交互、续写后继续工具、planning 只读对照、append 写入等）。

## 运行

```bash
python -m evals.runner                       # 全量（CI 门禁，四层齐备）
python -m evals.runner --tier smoke          # 只跑冒烟集
python -m evals.runner --tier tool           # 只跑真实工具集
python -m evals.runner --tier regression     # 只跑回归集
python -m evals.runner --tier safety         # 只跑安全集
python -m evals.runner --heldout             # 全量 + held-out
python -m evals.runner --case regression-token-budget
python -m evals.runner --json report.json    # 机器可读报告
```

成功打印 `Summary: N/N passed`；任一用例失败退出码为 1。全量模式下缺少任一常规层的用例同样拒绝通过。

执行器直接构造真实的 `NativeAgentExecutor`。脚本用例替换模型客户端与工具执行器；真实层只替换模型客户端，工具走真实 `ToolExecutionService` 与真实文件工具。状态根 `FLOODMIND_HOME` 在执行器导入前重定向到临时目录，不会读写用户真实配置或记忆。

## 新增用例

### 方式一：JSON 手工用例（少量高价值场景）

在 `cases/` 下新增 JSON 文件即可，无需改执行器：

```json
{
  "id": "regression-example",
  "tier": "regression",
  "tool_mode": "scripted",
  "description": "一句话说明验证的行为",
  "user_text": "用户输入",
  "max_iterations": 5,
  "max_total_tokens": 0,
  "abort_after_model_calls": null,
  "system_prompt": "可选",
  "workspace_files": {
    "data/input.md": "可选：真实工具用例的 fixture"
  },
  "tools": {
    "ToolName": {
      "description": "工具描述",
      "parameters": {"type": "object", "properties": {}},
      "result": {"content": "工具输出", "status": "completed"}
    }
  },
  "tool_results": {
    "call_1": {"content": "按调用 id 声明的结果，优先于 tools", "status": "completed"}
  },
  "rounds": [
    {"events": [
      {"type": "tool_call_done", "tool_call": {"id": "call_1", "name": "ToolName", "arguments": {}}},
      {"type": "done", "terminal_reason": "tool_calls"}
    ]},
    {"events": [
      {"type": "token", "content": "最终答案"},
      {"type": "done", "terminal_reason": "stop"}
    ]}
  ],
  "expect": {
    "final_output_contains": ["最终答案"],
    "final_output_not_contains": [],
    "final_output_equals": "可选：精确匹配",
    "model_calls": 2,
    "tool_calls": ["ToolName"],
    "tool_statuses": ["completed"],
    "invalid_calls": 0,
    "is_timeout": false,
    "workspace_contains": {"data/output.md": "期望落盘内容"},
    "workspace_not_contains": ["不应存在的文件"],
    "prompt_contains": [{"call_index": 1, "contains": "工具输出"}],
    "prompt_not_contains": []
  }
}
```

- `tool_mode`：`scripted`（默认，工具结果由用例声明）或 `real`（加载真实文件工具）。
- 真实模式下 `tools` 只需列出工具名（如 `{"Read": {}, "Grep": {}}`），其余字段被忽略；可用工具为 `Read` / `Write` / `Glob` / `Grep` / `Edit`。
- `tool_results` 按调用 id 声明 scripted 结果，优先于 `tools` 里的按工具名结果；同名工具的多轮不同结果（先失败后成功等）必须用它。
- `workspace_files` 只在真实模式下有意义：执行前写入临时 workspace，用于构造确定性输入。
- `workspace_contains` / `workspace_not_contains` 在执行后校验文件存在性与内容，仅用于真实工具用例。
- `mode`：可选 `planning` / `execution`（默认）；planning 用例验证规划模式硬门。
- `heldout`：可选 `true`，标记为 held-out，默认不加载。

### 方式二：case pack 家族（系统化扩容）

在 `evals/case_packs.py` 中新增家族函数并在 `_PACK_BUILDERS` 注册。遵守三条原则：

1. 确定性：纯函数、无随机/时间/环境依赖；
2. 可解释：每个用例有中文 description 说明验证的契约点；
3. 真实断言：每个用例至少一个 expect 断言，不凑数。

ID 以 `<tier>-pack-<family>` 为前缀，与 JSON 用例不冲突；`load_cases()` 内部有重复 id 自检，`_load_cases` 合并时再校验一次全局唯一性（撞 id 直接报错退出）。

支持的事件类型：`token` / `reasoning` / `usage`（可写 `usage` 对象）/ `tool_call_done` / `invalid_tool_call` / `error` / `timeout` / `done`；`done` 可带 `terminal_reason`。
脚本化模型对异常一律返回“不可重试”建议，因此 `error`/`timeout` 用例是确定性的终态验证。
断言全部为确定性状态/轨迹检查，不使用 LLM 评审，因此可在 CI 无密钥环境重复运行。

## 轨迹指标

每个用例输出并写入 JSON 报告：

- `model_calls`：模型调用轮数；
- `tool_calls`：实际进入工具执行器的调用序列；
- `invalid_calls`：工具结果中 `status="error"` 的数量（参数非法、权限拒绝、执行失败等）；
- `tool_accuracy`：实际工具序列与 `expect.tool_calls` 完全一致时为 1.0，否则为 0.0；未声明期望序列的用例为 `null`；
- `generated` / `heldout`：用例来源与 held-out 标记。

报告的 `metrics` 汇总全量用例，`tiers` 给出各层通过数。

## 门禁含义

- 用例失败 = 主循环契约或安全边界回归，必须先修复再合并；
- 冒烟层用例应保持少量、快速、稳定；工具层只放少量高价值真实链路，新增行为优先加到回归层；
- 用例依赖真实执行器接口，因此接口变更导致用例失败是预期信号，应同步评估契约变更本身；
- 扩容优先写参数化家族而不是堆近重复 JSON；每加一个家族先想清楚它断言的契约点。
