# BFCL 工具契约注入整改记录（2026-11-02）

## 改动与验证边界

- `ToolLoadingConfig(mode="catalog")` 将 registry 中全部工具完整 description 和参数 JSON Schema 注入系统提示词，同时保留原生结构化 tools schema；不要求 `GetTool` 预加载。
- `eager` 仍直接提供请求 tools schema，不在系统提示词重复参数；`progressive` 仍采用每 executor 独立加载状态。
- catalog 注入通用工具调用规则：不编造缺失值、不擅改标识符、尊重单位/类型/默认值、不做未请求的准备操作，不把独立操作随意合并。
- BFCL CLI 新增独立的 `--tool-loading eager|catalog`、`--offset` 和评分模式；具体兼容边界见 [tools/README.md](../../tools/README.md)。
- 默认 CLI 评分 `python-ast-compat` 是独立实现的固定源码兼容子集，不是官方 evaluator 或官方排行榜成绩；历史 `simplified` 明确保留。

## 数据与旧报告离线重评

BFCL v3 数据 revision：`cd9429ccf3d4d04156affe883c495b3b047e6b64`。
四类各前 20 条，共 80 条。模型为内网端点的 `deepseek-flash`。
兼容规则参考 checker revision：`58f57e9124ea981403792dd51e00a6577e621fae`。

| 已有模型输出 | 原 simplified | 离线 python-ast-compat | simple / multiple / parallel / irrelevance |
| --- | ---: | ---: | --- |
| 原始运行 | 66/80 | 69/80 | 18 / 16 / 17 / 18 |
| eager GetTool 注册修复后运行 | 67/80 | 69/80 | 19 / 17 / 17 / 16 |

原始报告保留；兼容重评分别写入本地 `.bfcl-deepseek-flash-80-compat.json` 和
`.bfcl-deepseek-flash-80-eager-fixed-compat.json`。旧报告未记录加载模式的字段在重评中仍为
`unknown`，表格中的运行背景来自实验记录，未伪造报告元数据。

重评没有发模型请求。字符串规则可接受部分表达式写法差异，但严格元素类型检查又拒绝了
`simple_13` 的整数 interval 元素；不能把规则更新描述为模型能力提升。两次单次采样也不能证明稳定收益。

## 本地验证

- 最终组合回归：`python -m pytest -q` → **1722 passed, 2 skipped**（90.25s）。
- 评分与工具加载专项：**66 passed**（53 个评分测试、13 个加载测试）。
- `python scripts/check_doc_examples.py` → `Contract examples passed: 1`。
- `git diff --check` 无空白错误。
- catalog 相关构造与提示词重建路径已获独立静态复核，未发现可证实的新问题；新增测试直接覆盖 bare 初始构造，未覆盖全部自定义模板组合。
- 评分模块的 Fable 独立审查因 HTTP 503 中断；改用当前会话模型后完成只读独立审查，未发现已验证的评分/CLI 正确性缺陷，专项测试 53 passed。该轮未能重新获取上游源码，未完成官方差分验证；独立审查、既有源码核查及自动测试均不代表完整官方等价。
- 以上为含现有 WIP 的工作区验证，不是干净提交提取后的发布验证；未提交、推送或发布。

## 已完成的 catalog 实测

用户随后提供了连接参数和凭据，两组 catalog 真实模型评测均已完成；凭据仅通过进程环境传入，没有写入项目文件。

| catalog 运行 | simple | multiple | parallel | irrelevance | 总计 | token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 原样本，每类前 20 条 | 19/20 | 17/20 | 17/20 | 18/20 | 71/80（88.75%） | 261612 |
| 留出集，每类 offset=20、limit=20 | 20/20 | 17/20 | 19/20 | 15/20 | 71/80（88.75%） | 263478 |

两组运行错误均为 0。原样本相对相同兼容评分下的 eager 修复运行从 69/80 变为 71/80，
增加 2 条；token 从 197825 增至 261612（约 32.2%）。这只是各一次采样，不证明稳定收益。
留出集没有对应 eager 运行，不能据此声称相对提升。仍未达到 100%。

原样本失败 ID：simple_13、multiple_3/7/10、parallel_3/9/19、irrelevance_1/7。
留出集失败 ID：multiple_24/25/26、parallel_26、irrelevance_26/27/29/32/33。
应继续核查请求、原始 schema 和标签，不能仅根据失败理由断言全部为模型错误。
本地报告为 `.bfcl-deepseek-flash-80-catalog-compat.json` 和
`.bfcl-deepseek-flash-80-catalog-heldout-compat.json`。

后续以环境变量提供连接和凭据后，可运行：

```bash
python tools/bench_bfcl.py --data-dir <data-dir> --limit 20 \
  --tool-loading catalog --scoring python-ast-compat --out bfcl-catalog.json
python tools/bench_bfcl.py --data-dir <data-dir> --offset 20 --limit 20 \
  --tool-loading catalog --scoring python-ast-compat --out bfcl-catalog-heldout.json
```

这些是真实付费模型调用；输出应使用新文件名并保留基线。应在同一评分模式下比较，逐例区分
模型误用、schema/适配问题与标签争议，不通过答案硬编码或放宽规则追求全通过。

## 完整请求与多轮契约排查

本轮不再发起付费请求，采用离线、真实 runtime 链路定位本项目问题。

### 已确认并修复的本项目缺陷

1. **bare 提示词刷新不一致**：初始构造与刷新使用不同路径，specialist 刷新可能丢失宿主提示词、保留旧目录。现在两个 executor 共用 bare 提示词构造逻辑；测试验证宿主保留、工具契约替换、skill 目录更新/移除及重复刷新不累积。
2. **嵌套 schema 类型遗漏转换**：`additionalProperties`、`$defs`、`definitions` 和组合 schema 等位置的 BFCL `dict/float` 类型未递归适配。现在按 schema 关键字递归，保留 `default` 等数据字段，不修改输入对象。这不表示任意 BFCL 方言都已兼容；位置型 `items` 等边界仍需单独验证。
3. **执行失败仍可能得分**：匹配调用的失败状态原先未参与判分；辅助工具调用还会被过滤掉。现在两种评分模式均先检查保留的执行状态；报告新增 `auxiliary_call_results`，离线重评同样检查。旧报告没有记录的状态不会被补造。

### 请求证据与边界

- `tools/request_capture.py` 为显式启用的本地诊断包装器，每次 transport 发送保存完整、有序 `messages` 和独立 `tools`，不截断历史；文件独占创建，拒绝覆盖。
- 记录明确标为 `representation: sdk_arguments`，不是 HTTP 抓包。真实 SDK 配合 `httpx.MockTransport` 的离线测试确认：`extra_body` 会合并到正文并覆盖同名字段；核查时不能只看 SDK 参数外层值。
- 三次发送的真实 Agent 离线回归覆盖宿主提示词、完整工具/参数说明、结构化 schema、工具实际求和结果 `42`、结果关联 ID，以及下一用户轮的历史保留。
- 未记录 headers/query/timeout；正文仍可能含敏感信息，不自动识别或脱敏，不应提交或上传。
- 此证据验证了所测路径的信息传递，不证明历史 BFCL 失败样本当时的请求相同，也不证明远端服务实际接收字节或模型遵守了契约。

### 结果验证不能混同

BFCL 使用模拟工具返回，且 `max_iterations=1`，主要测调用集合与参数匹配；不能以该分数证明业务执行结果正确。多轮离线测试验证真实工具返回和历史传递，但模型回复是固定响应，因此也不是模型自主核验能力的证据。
后续真实任务仍应分开检查调用契约、执行状态与可客观核验的结果/产物。

## 修复后重新实测与逐例归因

用户要求重新测试后，使用相同模型 `deepseek-flash`、`catalog` 加载和
`python-ast-compat` 评分完成以下两次真实调用。报告为
`.bfcl-postfix-20261102-152416-original.json` 与
`.bfcl-postfix-20261102-152416-heldout.json`，未覆盖历史报告。

| 运行 | simple | multiple | parallel | irrelevance | 总计 | token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 原样本，offset=0，每类 20 条 | 19/20 | 17/20 | 17/20 | 18/20 | 71/80（88.75%） | 264650 |
| 留出集，offset=20，每类 20 条 | 19/20 | 18/20 | 17/20 | 15/20 | 69/80（86.25%） | 262801 |

两组报告的运行错误均为 0；合计 140/160（87.5%），历史两组为 142/160。
本轮没有表现出总分提升，单次采样也不足以解释变化原因。
CLI 因存在未通过样本而退出 1，并非请求运行异常。首次批量启动脚本使用
`check=True` 导致第一组结束后未继续第二组；第二组随后单独运行完成，没有因此重跑第一组。

相对历史 catalog 结果，原样本 `multiple_3` 转为通过、`multiple_13` 转为失败；
留出集 `multiple_24`、`irrelevance_32` 转为通过，`simple_35`、`parallel_31`、
`parallel_38`、`irrelevance_37` 转为失败。

### 核查方法与证据边界

逐例对照固定 revision 数据中的原始 question、完整 function 定义、ground truth 与新报告
`tool_calls`，并用本地评分器离线重放全部 20 条失败，全部复现。
这些调用来自 runtime 事件记录，不是本轮 live 请求抓取；因此不能据此证明远端收到的完整
提示词与原始 schema 完全一致。此前三次发送的离线请求回归也不能替代这些样本的 live 证据。
本节排查没有新增模型请求、修改数据标签或放宽评分。

### 已确认的题目、描述或标签不一致

| ID | 原始证据与实际调用 | 判定 |
| --- | --- | --- |
| `multiple_7` | 题目要求 deer 的 “population growth … and their impact on woodland”，同时提供增长评估和生态影响工具；实际对两个目标分别调用，GT 却只含增长评估 | 题目双目标与单调用答案覆盖不一致，不能将第二个调用简单定性为无关调用 |
| `irrelevance_7` | 题目求 `3x^2` 在 1 到 5 的曲线下面积；工具明确为 “Calculate the definite integral … over a specific interval”，三个必填值均可由题目取得；实际调用该工具，却按 irrelevance 要求零调用 | no-call 标签与工具公开能力直接不一致；不代表已经验证字符串表达式的真实执行 |
| `simple_35` | 题目要求至少开到 11 PM；`operating_hours` 描述既说 “if 11 is given … at or after 11 PM”，又说 “24 hour format”；实际传 11，GT 要求 23 | 工具说明内部矛盾；实际遵循示例，答案遵循 24 小时制 |
| `irrelevance_27` | 题目求 3 kg 水升温 4°C 所需能量；`calculate_heat` 明确支持，描述直接给出水的比热；实际按描述填齐参数，却要求零调用 | no-call 标签与描述不一致；此外描述的 `4.184 J/kg·°C` 单位/数值有误，4.184 对应 J/g·°C 或 kJ/kg·°C，实际调用遵循了错误描述 |

这里的“确认”限定于所用固定数据版本的公开契约与标签矛盾，不代表已运行工具实现或获得上游认可。

### 契约歧义、GT 覆盖限制与评分表示边界

| ID | 证据 | 判定与限制 |
| --- | --- | --- |
| `simple_13` | 题目区间 1 到 3，schema 为 float 数组；实际 `[1, 3]`，GT `[1.0, 3.0]` | 本地兼容评分器严格检查数组元素 Python 类型。仅在离线副本中把 interval 改为浮点数即通过，其余字段不变；这是表示层拒绝，不是区间数值算错。适配后的 JSON Schema `number` 可接受整数，但兼容评分的嵌套类型规则更严格。尚无新的官方差分验证，不能宣称评分器实现有 bug；不改实际报告或自动转换输出 |
| `parallel_9` | `movie` 和 `time` 都是数组，time 的说明为 “Show time for each movie”；实际把两部电影及时间放在一次调用，GT 要求两次单元素调用 | schema 没有限制单元素，存在批处理语义与 GT 调用数限制不一致；没有真实实现，不能证明批处理业务结果等价 |
| `parallel_19` | 题目明确 4%，参数只写 “The annual interest rate”；实际 4，GT 0.04 | 百分数与小数比例约定未写清，不能无条件称为模型单位错误；GT 采用比例表示 |
| `parallel_26` | `case_number` 明确为数组，实际按 Civil/Criminal 各批量查询两个编号，GT 要求四次单编号查询；实际 location 为题目的 `New York District court`，GT 备选均不含 court | GT 存在强表示限制；即使拆分调用，location 白名单仍会拒绝。不能将首个调用数错误当作全部原因，也不能证明真实批处理等价 |
| `parallel_31` | 题目未给年份；实际为 Treaty of Paris 补 1783、Magna Carta 补 1215，GT 仅允许省略或 0；schema 未禁止补充年份 | Treaty of Paris 存在多个同名条约，1783 是未经确认的消歧；Magna Carta 的 1215 有合理依据。不能把两者一概称为同类错误，GT 对可选年份也有限制 |
| `parallel_38` | 两段路线后接 “with optional parameter shortest route enabled”；GT 对两段都要求 true，实际只对第二段设置 true | 尾部修饰语作用域存在歧义；按 GT 第一段缺参，但不是无歧义的违约证据 |
| `irrelevance_1` | 题目求二次方程解，唯一工具求和；实际用 `[4, -12]` 计算判别式的一部分 | 不满足该单轮 no-call 标签；求和可用于组合求解中的中间步骤，但本轮既未验证完整求解，也不足以确认标签缺陷 |
| `irrelevance_33` | 题目 “Identify the genetic code sequence ATCG”；工具通过序列识别物种，未设长度限制 | “identify” 指物种还是解释序列不明；四碱基也不足以唯一定位物种。零调用标签可能有理由，不能确认其错误 |

### 可定位的模型调用问题或过度调用

| ID | 实际行为 | 归因 |
| --- | --- | --- |
| `multiple_10` | 未执行要求的删列，而调用备份，编造 `/backups/employees` 路径 | 明确的参数编造与当前评测轮目标未完成；`max_iterations=1` 不证明模型后续绝不会删列，但不能解释路径来源 |
| `multiple_13` | 已正确调用自包含的 `revenue_forecast`，又查 `product_price` | 相对 GT 多调用；forecast 不接收查价返回值，没有契约证据证明额外调用必要。不是已确认数据缺陷 |
| `parallel_3` | 将请求中的 `normal hemoglobin` 改写为 `human hemoglobin` | 标识符替换，无足够证据证明等价；不能为其补造答案别名 |
| `multiple_25` | 正确查询售价之外，又查询是否促销 | 相对 GT 过度调用；“cheapest” 使动机可理解，但不构成已确认标签缺陷 |
| `multiple_26` | 正确查 rewards 之外，又列 missions | 相对 GT 过度调用；“different missions” 使额外调用可理解。失败不在于省略可选 mission/trophy，它们的省略被接受 |
| `irrelevance_26` | 用封闭容器压力计算器回答加热产生多少气体 | 所求物理量与工具能力不同；能填齐参数并不代表工具相关 |
| `irrelevance_29` | 用细胞部位功能说明工具回答线粒体数量 | 功能说明与数量问题不匹配，题目还缺少具体细胞条件 |
| `irrelevance_37` | 用 fauna 工具找 Miami 野餐点，额外编造 birds 和 winter | 明确的工具错选与参数编造 |

本轮未发现可复现的评分器实现或 Harness runtime 新缺陷；这不等于证明全部请求链路无误。
评分器的精确调用数、候选值白名单与类别级 no-call 规则解释了当前拒绝结果。
`parallel_31`、`parallel_38` 理由中的额外 event/start_loc 错误来自尝试匹配另一条调用，
不应再算作独立参数错误。

最近一次已完成的全量回归日志 `.request-contract-verified-pytest.log` 记录
**1740 passed, 2 skipped**（91.30s）；这是工作区测试，不是干净提交发布验证。
后续若要证明这些失败时没有信息丢失，应在获准的定向真实调用中启用完整请求记录，
核对 catalog、结构化 tools、用户输入及实际 SDK 合并语义；不能用离线固定响应替代模型自主执行证据。
