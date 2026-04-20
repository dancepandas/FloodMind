---
name: aojiang-hydro-intake
description: "TRIGGER when: 用户输入中只要提到敖江、敖江案例、敖江水文模型、敖江数据、霍口、山仔、牛溪、桂湖溪等内容时。DO NOT TRIGGER when: 用户要运行靖州案例，或只是泛泛咨询水文模型概念而不需要执行接口调用时。"
---

# Aojiang Hydro Intake

本 skill 用于执行敖江案例水文接口 `/aj/hydro_model`，并且必须在这里直接完成站点识别、任务拆解和 `stationCode` 映射。

## 使用原则
- 本 skill 只负责调用敖江案例水文接口 `/aj/hydro_model`
- 执行时只允许通过 `--input_file` 传入完整 `input.json`
- 如果用户当前只有原始 Excel/CSV/JSON/TXT 文件，先使用 `hydro-input-prep` skill 整理输入，再回到本 skill 执行
- 如果用户当前使用自然语言描述，先转换为结构化文件，再使用 `hydro-input-prep` skill 整理输入，再回到本 skill 执行
- 当用户提到霍口、山仔、牛溪、桂湖、最终出口等自然语言站点名称时，必须先完成站点归一化、任务类型判定和 `stationCode` 映射，再准备输入
- 当涉及多个站点时，必须完整阅读 `./aojiang-hydro/station.md` 文档的内容，这十分重要！！

## 必须遵守的硬规则

### 1. 先判定任务类型，再决定是否补充上游站点
- 不允许先凭直觉直接选 `stationCode`
- 必须先判断用户请求属于哪一种任务类型，再按对应规则拆解

### 2. 只有联合预报任务才补充主干流上游依赖
- 当目标站点位于敖江主干流，且结果依赖上游主干流过程时，属于联合预报任务
- 联合预报任务必须补充该目标位置依赖的全部上游子任务

### 3. 支流出口任务不考虑主干流上游站点
- `牛溪流域出口` 和 `桂湖溪流域出口` 是支流出口任务
- 这两个任务只使用各自对应的 `stationCode`
- 不要补充霍口水库、山仔水库、`temp-1`、`temp-2` 等主干流站点

### 4. 区间任务默认不补充上游站点
- 如 `霍口水库到山仔水库区间流量`、`山仔水库到 temp-1 区间流量`
- 这类任务直接使用对应区间 `stationCode`
- 不再向上递归补充更上游站点

### 5. 单断面任务默认不补充上游站点
- 如 `霍口水库未来 3 小时入库流量`
- 这类任务只做该断面自身预报

### 6. `temp-1` 和 `temp-2` 是虚拟站点
- 它们用于主干流任务拆解和拓扑表达
- 用户通常不会直接点名，但在联合预报拆解时可以使用

## 站点标准名与别称

| 标准站点名 | 常见别称 | 说明 |
|---|---|---|
| `霍口水库` | `霍口水库`、`霍口`、`霍口断面` | 主干流上游关键断面 |
| `山仔水库` | `山仔水库`、`山仔`、`山仔断面` | 主干流断面 |
| `temp-1` | 无 | 虚拟站点，用于主干流任务拆解 |
| `牛溪流域出口` | `牛溪`、`牛溪流域`、`牛溪出口` | 支流牛溪入汇主干流位置 |
| `桂湖溪流域出口` | `桂湖`、`桂湖溪流域`、`桂湖出口`、`桂湖溪出口` | 支流桂湖溪入汇主干流位置 |
| `temp-2` | 无 | 虚拟站点，主干流最下游出口 |

## 主干流相对位置关系

主干流从上游到下游依次为：

`霍口水库 -> 山仔水库 -> temp-1 -> temp-2`

支流入汇位置为：

- `牛溪流域出口` 位于 `temp-1` 下游
- `桂湖溪流域出口` 位于 `牛溪流域出口` 下游

```text
霍口水库 -> 山仔水库 -> temp-1 -> (牛溪流域出口入汇) -> (桂湖溪流域出口入汇) -> temp-2
```

## 子任务与 stationCode 映射

| 子任务名称 | stationCode | 使用场景 |
|---|---|---|
| `霍口水库断面预报` | `33c76b8bd9384486a945c2fc7fd622eb` | 霍口水库单断面预报；或作为联合预报上游组成部分 |
| `霍口水库~山仔水库区间断面预报` | `20001` | 山仔水库联合预报组成部分；或该区间单独预报 |
| `山仔水库~temp-1 区间断面预报` | `30001` | `temp-1` 或更下游联合预报组成部分 |
| `temp-1~temp-2 区间断面预报` | `40001` | 主干流最终出口联合预报组成部分 |
| `桂湖溪流域出口断面预报` | `GE2AG000000L` | 桂湖溪支流出口任务 |
| `牛溪流域出口断面预报` | `GE2AF000000R` | 牛溪支流出口任务 |

## 任务类型判定

收到用户请求后，先归类为以下四类之一：

### A. 联合预报任务
- 目标是主干流某个下游位置的最终流量或入库流量
- 结果依赖多个上游主干流子过程共同组成
- 典型例子：`山仔水库未来3小时入库流量`、`敖江流域未来3小时最终出口流量`
- 处理原则：必须补充上游依赖，并拆解成多个子预报任务

### B. 支流出口计算任务
- 目标是 `牛溪流域出口` 或 `桂湖溪流域出口`
- 处理原则：只做该支流出口自身预报，不考虑主干流上游站点

### C. 区间预报任务
- 用户明确要求某一区间的流量预报
- 处理原则：直接使用对应区间 `stationCode`，不再补充更上游站点

### D. 单断面预报任务
- 用户明确要求单个断面或水库的流量预报
- 处理原则：只做该断面自身预报，不补充上游站点

## 标准拆解流程
- 步骤 1：识别目标站点并归一化到标准站点名
- 步骤 2：判定任务类型
- 步骤 3：决定是否补充上游依赖
- 步骤 4：拆解为一个或多个标准子任务
- 步骤 5：将每个子任务准确映射到 `stationCode`
- 步骤 6：按拆解结果准备 `input.json`

## 联合预报标准拆解

### 目标为 `霍口水库`
- 任务类型：单断面预报任务
- 拆解结果：`霍口水库断面预报`
- stationCode：`33c76b8bd9384486a945c2fc7fd622eb`

### 目标为 `山仔水库`
- 任务类型：联合预报任务
- 子任务：`霍口水库断面预报`、`霍口水库~山仔水库区间断面预报`
- stationCode：`33c76b8bd9384486a945c2fc7fd622eb`、`20001`

### 目标为 `temp-1`
- 任务类型：联合预报任务
- 子任务：`霍口水库断面预报`、`霍口水库~山仔水库区间断面预报`、`山仔水库~temp-1 区间断面预报`
- stationCode：`33c76b8bd9384486a945c2fc7fd622eb`、`20001`、`30001`

### 目标为主干流最终出口 `temp-2`
- 任务类型：联合预报任务
- 子任务：`霍口水库断面预报`、`霍口水库~山仔水库区间断面预报`、`山仔水库~temp-1 区间断面预报`、`temp-1~temp-2 区间断面预报`、`桂湖溪流域出口断面预报`、`牛溪流域出口断面预报`
- stationCode：`33c76b8bd9384486a945c2fc7fd622eb`、`20001`、`30001`、`40001`、`GE2AG000000L`、`GE2AF000000R`

说明：当用户说“敖江流域最终出口流量”时，按主干流最终出口 `temp-2` 理解。

## 快速决策表

| 用户目标 | 任务类型 | 是否补充上游 | stationCode |
|---|---|---|---|
| `霍口水库` | 单断面预报任务 | 否 | `33c76b8bd9384486a945c2fc7fd622eb` |
| `山仔水库` | 联合预报任务 | 是 | `33c76b8bd9384486a945c2fc7fd622eb`、`20001` |
| `牛溪流域出口` | 支流出口计算任务 | 否 | `GE2AF000000R` |
| `桂湖溪流域出口` | 支流出口计算任务 | 否 | `GE2AG000000L` |
| `霍口水库~山仔水库区间` | 区间预报任务 | 否 | `20001` |
| `山仔水库~temp-1区间` | 区间预报任务 | 否 | `30001` |
| `temp-1~temp-2区间` | 区间预报任务 | 否 | `40001` |
| `敖江流域最终出口` | 联合预报任务 | 是 | `33c76b8bd9384486a945c2fc7fd622eb`、`20001`、`30001`、`40001`、`GE2AG000000L`、`GE2AF000000R` |

## 请求体要求
- 必须包含 `forecastTime`、`historyDuration`、`futureDuration`
- 必须包含 `modelDataParams`、`modelForecastRainfallParams`、`modelRunParam`
- 降雨数组元素通常包含 `time`、`stationCode`、`rainfallValue`
- 对同一个 `stationCode`，`historyDuration` 应与 `modelDataParams` 中该站点的元素个数一致
- 对同一个 `stationCode`，`futureDuration` 应与 `modelForecastRainfallParams` 中该站点的元素个数一致
- 若某个 `stationCode` 存在缺失时段，应在输入准备阶段按等时间隔补 `0`，保证每个站点的时序连续
- 完整的 `input.json`文件内容示例（具体细节请仔细阅读`station.md`）：
```json
{
  "forecastTime": "2023-07-01 00:00:00",
  "historyDuration": "2",
  "futureDuration": "3",
  "modelDataParams": [
    {"time": "2023-06-30 00:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 5.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 4.8},
    {"time": "2023-06-30 00:00:00", "stationCode": "20001", "rainfallValue": 6.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "20001", "rainfallValue": 3.8}
  ],
  "modelForecastRainfallParams": [
    {"time": "2023-07-01 01:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 10.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 11.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 8.3},
    {"time": "2023-07-01 01:00:00", "stationCode": "20001", "rainfallValue": 7.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "20001", "rainfallValue": 8.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "20001", "rainfallValue": 7.3}
  ],
  "modelRunParam": {
    "XAJ": []
  }
}
```

## agent 执行口径

每次拆解任务时，至少明确回答以下五个问题：

1. 目标站点或区间是什么
2. 这属于哪一种任务类型
3. 是否需要补充上游站点
4. 要拆成哪些标准子任务
5. 每个子任务对应哪个 `stationCode`

如果上述五个问题中有任何一个没有明确回答，就说明当前拆解还不完整。

## 可执行脚本

### run_aojiang_hydro_model.py

调用敖江案例水文模型接口。

脚本会固定同时生成结构化 `result.json` 和对应的 `result.xlsx`。

- `result.json` 用于成果检查、校验和后续自动化验证
- `result.xlsx` 是 `result.json` 经过脚本固定转换得到的最终交付文件
- 只要 `result.json` 内容正确，则 `result.xlsx` 一定正确

```bash
python run_aojiang_hydro_model.py --input_file "input.json" [--output_file "data/sessions/session/outputs/result.json"] [--excel_output_file "data/sessions/session/outputs/result.xlsx"] [--base_url "http://192.168.30.108:3500"]
```

参数：
- `--input_file`：完整请求体 JSON 文件路径
- `--output_file`：保存结构化结果 JSON；示例： `data/sessions/session/outputs/result.json`
- `--excel_output_file`：自定义结果 Excel 保存路径；默认与 `--output_file` 同名 `.xlsx`；示例：`data/sessions/session/outputs/result.xlsx`
- `--base_url`：可选，服务基础地址
- `--timeout`：可选，请求超时时间，默认 `120`

## 调用示例

```python
run_script(
    skill_name='aojiang-hydro-intake',
    script_name='run_aojiang_hydro_model.py',
    args=['--input_file', 'data/sessions/session/outputs/input.json', '--output_file', 'data/sessions/session/outputs/result.json', '--base_url', 'http://192.168.30.108:3500']
)
```

上面这段调用完成后，会同时生成 `outputs/result.json` 和 `outputs/result.xlsx`。

如果服务未启动，需要先启动 `all_flask.py`，否则脚本会返回网络连接错误。

## 补充说明
- `station.md` 是更详细的说明和示例文档，必要时可以阅读。
