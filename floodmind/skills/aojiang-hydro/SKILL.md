---
name: aojiang-hydro
description: "TRIGGER when: 用户输入中只要提到敖江、敖江案例、敖江水文模型、敖江数据、霍口、山仔、牛溪、桂湖溪等内容时。DO NOT TRIGGER when: 用户要运行靖州案例，或只是泛泛咨询水文模型概念而不需要执行接口调用时。"
---

# Aojiang Hydro

调用敖江案例水文接口 `/aj/hydro_model` 完成站点识别、任务拆解、`input.json` 构建与执行。本 skill 在一次对话内直接完成上述全部步骤。

## 使用原则
- 只负责调用敖江案例水文接口 `/aj/hydro_model`，通过 `--input_file` 传入完整 `input.json`
- 执行链路：判定任务类型 → 站点/stationCode 映射 → 处理用户数据 → 构建 `input.json` → 自检数据一致性（不一致则回退到构建步骤）→ 执行接口 → 校验返回降雨与上传是否一致（不一致则回退到处理数据步骤）

## ⚠️ input.json 必填字段（缺一会被接口校验拒绝，务必全部带上）

| 字段 | 说明 | 易错点 |
|---|---|---|
| `forecastTime` | 预报起报时刻，如 `2023-07-01 00:00:00` | 格式必须 `YYYY-MM-DD HH:MM:SS` |
| `historyDuration` | 历史时长（小时，字符串），如 `"2"` | 字符串而非数字 |
| `futureDuration` | 未来时长（小时，字符串），如 `"3"` | 字符串而非数字 |
| `modelDataParams` | 历史降雨数组，元素含 `time`/`stationCode`/`rainfallValue` | 每站点元素数 = `historyDuration` |
| `modelForecastRainfallParams` | 未来降雨数组，同上结构 | 每站点元素数 = `futureDuration` |
| `modelRunParam` | 模型运行参数对象，如 `{"XAJ": []}` | **最常被遗漏**，必须非空对象 |

补充约束：
- 所有时间频率均为 **1 小时**
- 同一 `stationCode` 缺失时段按等时间隔补 `0`，保证时序连续
- 完整构建示例见 `references/input_examples.md`（覆盖四种任务类型）

## 快速决策表（最常用，先查这里）

| 用户目标 | 任务类型 | 是否补上游 | stationCode |
|---|---|---|---|
| `霍口水库` | 单断面预报 | 否 | `33c76b8bd9384486a945c2fc7fd622eb` |
| `山仔水库` | 联合预报 | 是 | `33c76b8bd9384486a945c2fc7fd622eb`、`20001` |
| `牛溪流域出口` | 支流出口 | 否 | `GE2AF000000R` |
| `桂湖溪流域出口` | 支流出口 | 否 | `GE2AG000000L` |
| `霍口~山仔区间` | 区间预报 | 否 | `20001` |
| `山仔~temp-1区间` | 区间预报 | 否 | `30001` |
| `temp-1~temp-2区间` | 区间预报 | 否 | `40001` |
| `敖江流域最终出口` | 联合预报 | 是 | `33c76b8bd9384486a945c2fc7fd622eb`、`20001`、`30001`、`40001`、`GE2AG000000L`、`GE2AF000000R` |

"敖江流域最终出口流量"按主干流最终出口 `temp-2` 理解，需同时纳入主干流过程与两条支流出口过程。

## 站点标准名与别称

| 标准站点名 | 常见别称 | 说明 |
|---|---|---|
| `霍口水库` | `霍口水库`、`霍口`、`霍口断面` | 主干流上游关键断面 |
| `山仔水库` | `山仔水库`、`山仔`、`山仔断面` | 主干流断面 |
| `temp-1` | 无（虚拟） | 主干流任务拆解用；用户常称"水动力模型上游" |
| `牛溪流域出口` | `牛溪`、`牛溪流域`、`牛溪出口` | 支流牛溪入汇主干流位置 |
| `桂湖溪流域出口` | `桂湖`、`桂湖溪流域`、`桂湖出口`、`桂湖溪出口` | 支流桂湖溪入汇主干流位置 |
| `temp-2` | 无（虚拟） | 主干流最下游出口 |

## 子任务与 stationCode 映射

| 子任务名称 | stationCode | 使用场景 |
|---|---|---|
| `霍口水库断面预报` | `33c76b8bd9384486a945c2fc7fd622eb` | 霍口单断面；或联合预报上游组件 |
| `霍口~山仔区间断面预报` | `20001` | 山仔联合预报组件；或该区间单独预报 |
| `山仔~temp-1 区间断面预报` | `30001` | temp-1 或更下游联合预报组件（用户常称"山仔~水动力模型区间"） |
| `temp-1~temp-2 区间断面预报` | `40001` | 主干流最终出口联合预报组件（用户常称"水动力模型区间"） |
| `桂湖溪流域出口断面预报` | `GE2AG000000L` | 桂湖溪支流出口任务 |
| `牛溪流域出口断面预报` | `GE2AF000000R` | 牛溪支流出口任务 |

## 主干流拓扑

```text
霍口水库 -> 山仔水库 -> temp-1 -> (牛溪流域出口入汇) -> (桂湖溪流域出口入汇) -> temp-2
```

- 主干流从上游到下游：`霍口水库 → 山仔水库 → temp-1 → temp-2`
- `牛溪流域出口` 位于 `temp-1` 下游；`桂湖溪流域出口` 位于 `牛溪流域出口` 下游
- `temp-1` / `temp-2` 是虚拟站点，用户通常不直接点名，但联合预报拆解时必须使用

## 任务类型判定与拆解规则

先归类为以下四类之一，再决定是否补充上游站点。**不要凭直觉直接选 stationCode。**

### A. 联合预报任务
- 目标是主干流某下游位置的最终流量/入库流量，结果依赖多个上游主干流子过程
- 典型：`山仔水库未来3小时入库流量`、`敖江流域未来3小时最终出口流量`
- **必须补充上游依赖，拆成多个子预报任务**

### B. 支流出口计算任务
- 目标是 `牛溪流域出口` 或 `桂湖溪流域出口`
- **只做该支流出口自身预报，不考虑主干流上游站点**

### C. 区间预报任务
- 用户明确要求某一区间的流量预报（如 `霍口~山仔区间`、`山仔~temp-1区间`）
- **直接用对应区间 stationCode，不再向上递归补上游**

### D. 单断面预报任务
- 用户明确要求单个断面/水库的流量预报（如 `霍口水库未来3小时入库流量`）
- **只做该断面自身预报，不补上游**

### 标准拆解步骤（按序执行，不要跳步）
1. 识别目标站点，别称归一化到标准站点名
2. 判定任务类型（A/B/C/D）
3. 决定是否补充上游依赖（仅 A 补）
4. 拆解为一个或多个标准子任务
5. 每个子任务映射到 stationCode
6. 按拆解结果准备 `input.json`（详细示例见 `references/input_examples.md`）

### 联合预报拆解示例
- **目标山仔水库** → 子任务：`霍口水库断面预报` + `霍口~山仔区间断面预报`（stationCode: `33c76b8bd9384486a945c2fc7fd622eb`、`20001`）
- **目标 temp-1** → 再加 `山仔~temp-1 区间断面预报`（+`30001`）
- **目标 temp-2（敖江流域最终出口）** → 再加 `temp-1~temp-2 区间断面预报` + `桂湖溪流域出口断面预报` + `牛溪流域出口断面预报`（+`40001`、`GE2AG000000L`、`GE2AF000000R`）

## agent 执行口径

每次拆解任务时，必须明确回答这五个问题；任何一个没答清，拆解就不完整：

1. 目标站点或区间是什么？
2. 属于哪一种任务类型？
3. 是否需要补充上游站点？
4. 要拆成哪些标准子任务？
5. 每个子任务对应哪个 stationCode？

## input.json 最小结构（完整示例见 references/input_examples.md）

```json
{
  "forecastTime": "2023-07-01 00:00:00",
  "historyDuration": "2",
  "futureDuration": "3",
  "modelDataParams": [
    {"time": "...", "stationCode": "...", "rainfallValue": 0.0}
  ],
  "modelForecastRainfallParams": [
    {"time": "...", "stationCode": "...", "rainfallValue": 0.0}
  ],
  "modelRunParam": { "XAJ": [] }
}
```

> 构建时务必对照"必填字段"表逐项核对；四种任务类型的完整真实示例在 `references/input_examples.md`，需要时读取。

## 执行脚本：run_aojiang_hydro_model.py

```bash
python scripts/run_aojiang_hydro_model.py \
  --input_file "input.json" \
  [--output_file "result.json"] \
  [--excel_output_file "result.xlsx"] \
  [--base_url "http://192.168.30.108:3500"] \
  [--timeout 120]
```

- 脚本固定同时生成 `result.json`（结构化，供校验）和 `result.xlsx`（最终交付，由 result.json 固定转换得到；result.json 正确则 xlsx 必正确）
- 服务未启动时需先启动 `all_flask.py`，否则返回网络连接错误
- 调用示例：`python scripts/run_aojiang_hydro_model.py --input_file input.json --base_url http://192.168.30.108:3500`

## result.json 结构（用于成果校验）

```json
{
  "case_name": "敖江",
  "success": true,
  "request": { "base_url": "...", "endpoint": "/aj/hydro_model", "payload": { /* 提交的 input.json */ } },
  "response": {
    "code": 200,
    "data": [
      {"flow": "345.11", "modelType": "XAJ", "sectionCode": "...", "time": "...", "rainfallValue": 1.5}
    ]
  }
}
```

校验要点：`response.data` 中每条 `rainfallValue` 应与 `request.payload` 中对应 stationCode/时间的降雨一致；`success` 必须为 `true`。
