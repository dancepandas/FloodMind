---
name: hydro-input-prep
description: "TRIGGER when: 用户要把自然语言描述、Excel/CSV/JSON/TXT 等原始降雨输入整理成水文模型标准输入时；或用户提到要生成、检查、修正中间 Excel、input.json 时。DO NOT TRIGGER when: 用户已经明确给出可直接调用 aojiang/jingzhou 水文脚本的完整 input.json，且不需要再整理输入时。"
---

# Hydro Input Prep

用于把用户的原始输入自动整理成水文模型可执行输入。

标准流程为：

`自然语言 / 原始文件 -> 标准中间 Excel -> input.json -> 案例水文脚本`

## 核心规则

- 用户没有明确提到时间时，使用系统提示词中的当前系统时间作为 `forecastTime`
- 请求体统一优先使用 `stationCode`
- 默认 `stationCode` 为 `33c76b8bd9384486a945c2fc7fd622eb`
- 用户没有明确指定时间步长时，默认按 `1` 小时展开
- `historyDuration` 和 `futureDuration` 的校验按每个 `stationCode` 分别计算，不按总行数计算
- 若某个 `stationCode` 存在缺失时段，例如 `08:00` 和 `10:00` 有值但 `09:00` 缺失，则需要补 `09:00` 且 `rainfallValue=0`
- 若自然语言描述与上传文件内容冲突，必须先向用户确认
- 中间文件统一使用 Excel，便于用户检查和追踪

## 标准中间 Excel

标准中间 Excel 固定包含以下工作表：

- `metadata`：关键参数与来源说明
- 每个 `stationCode` 一个独立工作表，sheet 名默认直接使用 `stationCode`
- 若 `stationCode` 超过 Excel 工作表名长度限制，sheet 名会被截断，实际站点编码以工作表内 `stationCode` 列为准
- 每个 `stationCode` 工作表固定列为 `phase`、`time`、`rainfallValue`、`stationCode`、`isAutoFilled`
- `phase` 只允许 `history` 或 `forecast`
- `isAutoFilled=Y` 表示该行是系统为保持时间连续性自动补的 0 值行
- `notes`：自动识别结果、默认值补齐说明、提醒信息

## 可执行脚本

使用 `run_script` 工具执行以下脚本。

### build_hydro_input_excel_from_text.py - 自然语言转标准中间 Excel

用于把用户的自然语言描述整理成标准中间 Excel。

输出规则：
- 默认生成单个 `stationCode` 工作表
- 每个工作表内按 `history` / `forecast` 两个阶段逐小时展开
- 自然语言规则本身已完整覆盖的时段不会额外补 0
- 当 `case_name=aojiang` 且未显式指定 `stationCode` 时，会优先按 `skills/aojiang-hydro/station.md` 的高频规则自动解析站点和联合预报范围
- 例如“山仔水库入库流量”会自动展开为霍口水库和霍口~山仔区间；“敖江最终出口”会自动展开为主干流和支流相关 `stationCode`

**适用场景：**
- 用户直接说“未来 8 小时都下 12mm，之前 24 小时都没下雨”
- agent 已经从自然语言中提取出持续时长、降雨值、模型等信息

**用法：**
```bash
python build_hydro_input_excel_from_text.py --output_file "hydro_input.xlsx" --description "未来8个小时的降雨均为12mm，之前一整天都没下雨" --forecast_time "2026-04-10 10:00:00"
```

**参数：**
| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| --output_file | 是 | - | 输出标准中间 Excel |
| --description | 是 | - | 用户自然语言描述 |
| --forecast_time | 是 | - | 预报起算时间；用户未明确时应传当前系统时间 |
| --case_name | 否 | `aojiang` | 案例名称，仅用于中间文件标记 |
| --station_code | 否 | `33c76b8bd9384486a945c2fc7fd622eb` | 默认 stationCode |
| --station_codes_json | 否 | - | 显式 stationCode 数组 JSON，优先于自动解析 |
| --model_types | 否 | `XAJ` | 模型类型，逗号分隔 |
| --time_step_hours | 否 | `1` | 时间步长（小时） |
| --history_duration | 否 | 从描述解析 | 历史时长 |
| --future_duration | 否 | 从描述解析 | 未来时长 |
| --history_uniform_rainfall | 否 | 从描述解析 | 历史阶段统一降雨量 |
| --future_uniform_rainfall | 否 | 从描述解析 | 未来阶段统一降雨量 |
| --history_values_json | 否 | - | 历史降雨数组 JSON，长度必须等于 historyDuration |
| --future_values_json | 否 | - | 未来降雨数组 JSON，长度必须等于 futureDuration |

**建议：**
- 当自然语言很规整时，可直接使用 `--description`
- 当自然语言较复杂时，先由 agent 提取结构化参数，再显式传入 `--history_duration`、`--future_duration`、`--history_values_json`、`--future_values_json`
- 当用户已经明确给出多个站点时，可直接传 `--station_codes_json '["code1", "code2"]'`

**调用示例：**
```python
run_script(
    skill_name='hydro-input-prep',
    script_name='build_hydro_input_excel_from_text.py',
    args=['--output_file', 'outputs/aojiang_input_mid.xlsx', '--description', '未来8个小时的降雨均为12mm，之前一整天都没下雨', '--forecast_time', '2026-04-10 10:00:00', '--case_name', 'aojiang']
)
```

### normalize_hydro_input_file_to_excel.py - 原始文件转标准中间 Excel

用于自动识别用户上传的原始文件，并整理成标准中间 Excel。

**支持格式：**
- `.xlsx`
- `.xlsm`
- `.csv`
- `.tsv`
- `.txt`
- `.json`

**处理规则：**
- 优先识别时间列、降雨列、站点列、阶段列
- 若文件中存在 `stationName`、`sectionName`、`站点名称`、`断面名称` 等列，且 `case_name=aojiang`，会自动按敖江站点语义映射到 `stationCode`
- 若文件没有站点列，但提供了 `--task_description`，且 `case_name=aojiang`，会按任务描述自动展开对应的 `stationCode`
- 若文件自带 `history/forecast` 阶段列，则按阶段列拆分
- 若文件没有阶段列，则需要 `--forecast_time` 用于拆分历史/未来
- 会按 `stationCode` 分组，并为每个 `stationCode` 生成单独工作表
- 会按时间连续性自动补齐缺失时段，补齐行的 `rainfallValue=0`，并标记 `isAutoFilled=Y`
- `historyDuration` / `futureDuration` 会按所有站点的共同时间范围推断，再对每个站点分别补齐
- 若输入文件本身就是标准中间 Excel，会按标准重新写出，便于补充备注或统一格式
- 若输入文件本身就是标准 `input.json`，会自动反向整理成中间 Excel 供用户检查

**用法：**
```bash
python normalize_hydro_input_file_to_excel.py --input_file "rainfall.xlsx" --output_file "hydro_input.xlsx" --forecast_time "2026-04-10 10:00:00"
```

**参数：**
| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| --input_file | 是 | - | 原始文件路径 |
| --output_file | 是 | - | 输出标准中间 Excel |
| --case_name | 否 | `aojiang` | 案例名称 |
| --forecast_time | 否 | - | 当原始文件无阶段列时，用于拆分历史/未来 |
| --station_code | 否 | `33c76b8bd9384486a945c2fc7fd622eb` | 默认 stationCode |
| --model_types | 否 | `XAJ` | 模型类型，逗号分隔 |
| --source_note | 否 | - | 附加备注，写入 `metadata`/`notes` |
| --task_description | 否 | - | 任务描述；当原始文件无 `stationCode` 时，可用于按业务语义展开站点 |

**调用示例：**
```python
run_script(
    skill_name='hydro-input-prep',
    script_name='normalize_hydro_input_file_to_excel.py',
    args=['--input_file', 'raw/rainfall.csv', '--output_file', 'outputs/aojiang_input_mid.xlsx', '--forecast_time', '2026-04-10 10:00:00', '--case_name', 'aojiang']
)
```

### convert_hydro_input_excel_to_json.py - 标准中间 Excel 转 input.json

把标准中间 Excel 转成 `run_aojiang_hydro_model.py` / `run_jingzhou_hydro_model.py` 可直接使用的 `input.json`。

**校验规则：**
- `metadata.forecastTime` 不能为空
- 对每个 `stationCode`，`history` 行数都必须等于 `historyDuration`
- 对每个 `stationCode`，`forecast` 行数都必须等于 `futureDuration`
- 每一行都必须能得到 `phase`、`time`、`rainfallValue`、`stationCode`
- 缺失 `stationCode` 时，会自动回退到 `metadata.defaultStationCode`

**用法：**
```bash
python convert_hydro_input_excel_to_json.py --input_file "hydro_input.xlsx" --output_file "input.json"
```

**参数：**
| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| --input_file | 是 | - | 标准中间 Excel |
| --output_file | 是 | - | 输出的 input.json |

**调用示例：**
```python
run_script(
    skill_name='hydro-input-prep',
    script_name='convert_hydro_input_excel_to_json.py',
    args=['--input_file', 'outputs/aojiang_input_mid.xlsx', '--output_file', 'outputs/aojiang_input.json']
)
```

## 推荐工作流

### 1. 用户给自然语言

```text
用户描述
-> build_hydro_input_excel_from_text.py
-> 标准中间 Excel
-> convert_hydro_input_excel_to_json.py
-> aojiang / jingzhou 水文脚本
```

### 2. 用户上传原始文件

```text
原始 Excel/CSV/JSON/TXT
-> normalize_hydro_input_file_to_excel.py
-> 标准中间 Excel
-> convert_hydro_input_excel_to_json.py
-> aojiang / jingzhou 水文脚本
```

## 执行策略

- 优先把原始输入整理为中间 Excel，不要直接手工拼接长 JSON
- 生成中间 Excel 后，应优先让用户确认关键字段是否合理
- 重点检查每个 `stationCode` 工作表是否连续、是否存在自动补 0 行、`historyDuration` / `futureDuration` 是否与预期一致
- 若用户希望直接执行，也可在内部完成中间 Excel 检查后直接继续转 `input.json`
- 若自然语言与文件内容冲突，必须先询问，不要自行覆盖
