# tools — 真实模型评测工具

用真实模型验证 FloodMind Harness 的评测脚本。不属于安装包，不进入 CI，按需运行。密钥只从环境变量读取。

## 工具

| 文件 | 作用 |
| --- | --- |
| `bench_bfcl.py` | BFCL v3 适配器：simple / multiple / parallel / irrelevance，按调用集合与参数判分 |
| `live_eval.py` | 11 个端到端场景，按客观产物判定完成度，并统计效率与工具使用质量 |
| `live_probe.py` | 最小 live 探针，验证完整 runtime 的真实链路 |

## 运行

```bash
# BFCL（数据需另行下载到独立目录）
FM_BASE=... FM_KEY=... FM_MODEL=... FM_CONTEXT_WINDOW=... \
  python tools/bench_bfcl.py --data-dir <dir> --limit 50 --out bfcl.json

# 已有报告按最新规则离线重算
python tools/bench_bfcl.py --data-dir <dir> --rescore bfcl.json

# 端到端场景（--repeat 取样稳定性）
FM_BASE=... FM_KEY=... FM_MODEL=... python tools/live_eval.py --out report.json --repeat 2

# 链路探针
FM_BASE=... FM_KEY=... FM_MODEL=... python tools/live_probe.py
```

## BFCL 评分模式与复核边界

`--scoring python-ast-compat`（CLI 默认）是本项目独立实现的 Python JSON-call
兼容子集，不是官方 evaluator，也未通过完整官方差分验证。参考固定源码
[`ast_checker.py` @ `58f57e9124ea981403792dd51e00a6577e621fae`](https://github.com/ShishirPatil/gorilla/blob/58f57e9124ea981403792dd51e00a6577e621fae/berkeley-function-call-leaderboard/bfcl_eval/eval_checker/ast_eval/ast_checker.py)。
本次通过 jsDelivr 的固定 commit 镜像只读核查源码，未执行下载源码。

- 字符串按参考实现删除 ASCII 空格及 `,./-_*^`，转小写，单引号统一为双引号；
  不删除 tab、换行、冒号。数组中的直接字符串元素也标准化，但不重排数组。
- 校验 schema 的 required、拒绝 schema 或答案中没有的额外参数；字典拒绝额外键。
  答案候选中的空字符串仅允许省略非 required 参数。
- Python 类型精确检查（布尔值不是整数）；float 容许 int，integer 不容许 float；
  数值使用精确相等而非简化模式的 `1e-6` 容差。候选表示变量时保留其大小写。
- 支持 Python 标量、单层数组、简单字典和字典数组的候选匹配；不支持 Java/JS 类型转换、
  AST 解析、执行检查、模型专用函数名转换或排行榜聚合。深层结构不保证官方等价；
  不支持的 schema/type 判失败，不静默切回简化规则。只处理适配器已有的四类，
  simple/multiple 预期单调用、parallel 按参考实现贪心无序一一配对。
- `--scoring simplified` 明确保留历史规则（忽略额外参数、字符串仅 trim/lower、数值容差）；
  不应将两种模式的分数混为同一指标。低层 `score_result()` 的默认仍为历史简化模式。
- `--tool-loading eager|catalog` 独立于评分，默认 eager 保留历史可比性。
  `--offset N` 每类跳过前 N 个样本，`--limit` 仍为每类数量；两者都必须非负。
- 每行报告标注 `scoring`、`checker_revision`、`tool_loading`。旧报告离线重评时，
  未记录的工具加载方式为 `unknown`，绝不按新 CLI 参数猜测；历史耗时、调用、错误均保留。
  任何运行错误仍判失败，包括 irrelevance；评分不是执行成功率或模型单独能力。

```bash
# 不发模型请求、不需密钥；写入新文件，不覆盖输入报告
python tools/bench_bfcl.py --data-dir <dir> --rescore bfcl.json \
  --scoring python-ast-compat --out bfcl-rescored.json
# 历史简化规则可单独复算
python tools/bench_bfcl.py --data-dir <dir> --rescore bfcl.json --scoring simplified
```

## 完整请求诊断（显式启用）

`request_capture.py` 提供本地 transport 包装器，在 provider 适配之后、SDK 发送之前，
逐次保存完整 `messages`、独立 `tools`、`tool_choice`、`extra_body` 等请求字段。
不拼接或截断消息，工具返回与历史保留原有结构；不记录 HTTP headers、query 或 timeout。
现有文件拒绝覆盖。此工具不属于 SDK 公共 API，不会默认开启。

```python
from tools.request_capture import RequestCaptureTransport

# client 为已经构造的 ModelClient；必须在 agent.run() 之前包装。
client._transport = RequestCaptureTransport(client._transport, "new-requests.jsonl")
# 按正常方式创建 Agent、执行多轮；每次底层发送记录一行。
```

文件包含完整用户内容、skill 内容及工具输出，可能含业务敏感信息；只在隔离工作区使用，
不要提交、上传或把真实凭据放入 prompt/工具参数。它记录 SDK 调用参数，不是抓包，
不能单凭此文件证明服务端收到了相同字节，也不会自动识别正文中的秘密。
每行以 `representation: sdk_arguments` 标记；`extra_body` 保留为独立参数，SDK 会将其
合并到 HTTP 正文并覆盖同名字段，因此核查时不能只看外层字段。离线 HTTP MockTransport
回归覆盖了这一行为，不会向真实服务发送请求。
离线回归使用真实 Agent、provider codec、工具执行及会话历史，仅将响应 transport 替换为
固定的本地流，验证三次发送中的契约、工具结果 `42` 和跨用户轮历史；不产生模型费用。

## 约定

- 密钥只读环境变量，不落盘；脚本在导入 FloodMind 前把 `FLOODMIND_HOME` 指向临时目录。
- BFCL 数据不入库；结果与模型、提示词、上下文窗口绑定，换配置需重新测量。
- 真实调用产生费用与网络依赖，不要接入 pytest 默认流程。
- 历史演示/生成脚本（PPT、图片、文档转换）属于本地辅助材料，不是评测链路的一部分。
