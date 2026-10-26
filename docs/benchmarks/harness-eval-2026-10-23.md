# FloodMind Harness 能力评测记录（2026-10-23）

## 目的

评测不只回答“能否按设计路线跑通”，还回答三个实际问题：

1. 任务是否真的完成（用客观产物判定，不采信模型自述）；
2. 完成得是否高效（墙钟、LLM 轮次、token、工具调用）；
3. 工具与技能使用是否规范（工具错误、重复调用、重复获取 schema、技能该触发时是否触发）。

评测分两层：BFCL 测“函数调用契约”，端到端场景测“真实任务闭环”。

## 环境与模型

| 项 | 取值 |
|---|---|
| 模型 | `gpt-5.6-sol`（OpenAI 兼容端点） |
| 端点 | 内网 OpenAI 兼容端点（`http://<host>:3000/v1`） |
| 上下文窗口 | 200000（显式传给 `ModelClient`） |
| 密钥 | 仅从 `FM_KEY` 环境变量读取，不落盘 |

## 第一层：BFCL 工具调用评测

`tools/bench_bfcl.py` 把 BFCL v3 的 function schema 转成 `ToolSpec` 注入 bare runtime，
用 `tool_loading=False`（eager）对齐 BFCL “一次给全函数”的设定。评分只依赖事件流中
记录的工具调用，与模型自述无关。

数据来源（未入库，按需下载）：

- `https://hf-mirror.com/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard/resolve/main/BFCL_v3_simple.json`
- 同理 `BFCL_v3_multiple.json`、`BFCL_v3_parallel.json`、`BFCL_v3_irrelevance.json`
- 标注文件在 `possible_answer/` 子目录；`irrelevance` 无标注（期望不调用工具）

运行（每类 50 条，共 200 条）：

```bash
FM_BASE=http://192.168.30.112:3000/v1 FM_KEY=... FM_MODEL=gpt-5.6-sol \
FM_CONTEXT_WINDOW=200000 python tools/bench_bfcl.py \
  --data-dir <bfcl-data-dir> --limit 50 \
  --categories simple,multiple,parallel,irrelevance --out bfcl.json
```

### 结果

| 类别 | 通过 | 通过率 | token | 耗时 |
|---|---:|---:|---:|---:|
| simple（单函数单调用） | 46/50 | 92% | 119075 | 143.7s |
| multiple（多函数调用） | 42/50 | 84% | 124578 | 144.4s |
| parallel（同函数并行多次） | 40/50 | 80% | 123421 | 174.0s |
| irrelevance（不应调用） | 46/50 | 92% | 126788 | 277.3s |
| **合计** | **174/200** | **87.0%** | **493862** | **739.4s** |

仅有 2 次调用落到 harness 自带的辅助工具（`GetTool`/后台任务工具），其余调用都
指向 BFCL 函数；即 harness 注入额外工具没有明显污染函数选择。

### 失败分类（26 条）

- 数学表达式写法差异：`x^2` vs `x**2`、`3x+2` vs `3x + 2`（4 条 simple、1 条 multiple），
  属于模型输出与标注的规范化差异；
- 过度/不足调用：一条请求合成一次调用、或把两个独立调用合并成一次
  （`parallel_9`、`multiple_7/26`、`parallel_26`）；
- 单位与精度换算：利率 4 与 0.04（`parallel_19`）、`rounding` 多传/少传；
- 实体名过度具体：`Washington, DC` vs `Washington DC`、`Los Angeles Lakers` vs `Lakers`、
  蛋白质全名 vs 缩写；
- 无关请求仍调用工具：4 条 irrelevance。

这些属于模型行为而非链路故障；harness 对每次调用都完成了参数校验、执行与状态回报。

## 第二层：端到端场景评测

`tools/live_eval.py` 覆盖 11 个场景：简单问答、文件读写、CSV 技能、纯计算（不应触发
技能）、Unicode 路径、多步依赖、子代理委派、并行委派、错误恢复、权限拒绝、跨轮记忆。

### 结果（修复后）

汇总口径为“最终一轮 + 复跑 multi-step”：

- 任务完成：11/11
- 行为合格：11/11
- token：约 346k
- 工具调用：60 次
- 工具错误：2 次（均为预期：读取缺失文件的 Read、被权限拒绝的 Read）
- 重复调用（同工具同参数）：0
- 重复 GetTool：0
- 用 Bash 代替 Write/Read 写文件：0

### 稳定性（5 个代表场景 × 2 次）

| 场景 | 结果 | 第一次 | 第二次 |
|---|---|---|---|
| csv-skill | 2/2 | 25.0s / 5 llm / 31806 tok | 45.7s / 8 llm / 77363 tok |
| multi-step | 2/2 | 10.9s / 3 llm / 16574 tok | 17.9s / 6 llm / 34920 tok |
| subagent | 2/2 | 23.0s / 7 llm / 34734 tok | 21.1s / 7 llm / 36344 tok |
| parallel | 2/2 | 26.3s / 10 llm / 49702 tok | 22.8s / 10 llm / 51162 tok |
| error-recovery | 2/2 | 13.1s / 5 llm / 27684 tok | 18.6s / 5 llm / 34295 tok |

结论：完成情况稳定，但同一提示词的 token/轮次波动可达 2 倍以上（csv-skill 31.8k 到
77.4k）。单次运行的效率指标不足以代表基线，评测应使用 `--repeat` 多次取样。

## 评测中发现并修复的问题

1. **工具失败被记成成功**：内置工具用文本前缀表达失败（如“错误：文件不存在”），
   执行层此前统一返回 `completed`。新增 `floodmind/common/tool_output.py`，在
   `ToolExecutionService` 归一为 `status="error"` 与 `error_code=TOOL_REPORTED_ERROR`，
   并更新对应用例期望。
2. **PowerShell 非终止错误退出码为 0**：多语句命令中途报错仍继续执行，最终可能返回
   0，被记为成功。`exec_bash` 现对 PowerShell/pwsh 注入
   `$ErrorActionPreference = 'Stop'`（模型显式设置时不覆盖），错误会以非 0 退出码
   回报，从而正确归一为 error。
3. **完整 runtime 重建宿主注入的 ModelClient**：会丢掉 `context_window`/超时等字段。
   已改为原样复用注入实例。
4. **非 SSE 端点缺少可诊断错误**：端点返回 HTML/JSON 时现在报错并提示核对 `base_url`
   是否以 `/v1` 结尾。
5. **行为指导过期**：`WORKFLOW_GUIDANCE` 仍写不存在的 `Task` 工具名（实际为
   `SubAgent`）；`ExperienceSearch` 被要求“每个新任务都先搜”；`update_plan` 被鼓励
   逐步标记。已改为按需检索、由系统乐观推进计划。
6. **评测自身缺陷**：multi-step 提示词未给定数字，产物不可判定；parallel 缺少
   “父代理不得代写”的行为检查；BFCL 嵌套值（数组/字典参数）被误判；这些均已修复，
   并为 `live_eval.py` 增加 `--repeat` 稳定性取样。

## 回归确认

上述修复完成后全量测试：1601 passed, 2 skipped（110.8s）。

## 复现

```bash
# 端到端场景（可加 --repeat 2 测稳定性）
FM_BASE=... FM_KEY=... FM_MODEL=... FM_CONTEXT_WINDOW=200000 \
  python tools/live_eval.py --out report.json

# BFCL（先下载数据到独立目录）
FM_BASE=... FM_KEY=... FM_MODEL=... FM_CONTEXT_WINDOW=200000 \
  python tools/bench_bfcl.py --data-dir <dir> --limit 50 --out bfcl.json

# 已有报告按最新评分规则重算，不重复调用模型
python tools/bench_bfcl.py --data-dir <dir> --rescore bfcl.json
```
