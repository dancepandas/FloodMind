---
name: jingzhou-hydro-intake
description: "TRIGGER when: 用户输入中只要提到靖州、靖州案例、水文模型 、靖州数据等内容时。DO NOT TRIGGER when: 用户要运行敖江案例，或只是泛泛咨询水文模型概念而不需要执行接口调用时。"
---

# Jingzhou Hydro Intake

## 可执行脚本

使用 `run_script` 工具执行以下脚本：

### run_jingzhou_hydro_model.py - 调用靖州案例水文模型接口

该脚本会调用 `/jz/hydro_model`，并且**只允许**通过 `--input_file` 传入完整 JSON 文件。

不要使用 `--payload`，也不要使用分字段参数方式。所有请求体都必须先落到本地 `input.json`，再通过文件调用。

**必要条件：**
- 服务已启动，默认地址 `http://192.168.30.108:3500`
- 请求体必须包含 `forecastTime`、`historyDuration`、`futureDuration`
- 请求体必须包含 `modelDataParams`、`modelForecastRainfallParams`、`modelRunParam`

**用法：**
```bash
python run_jingzhou_hydro_model.py --input_file "input.json" [--output_file "result.json"] [options]
```

**参数：**
| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| --input_file | 是 | - | 完整请求体 JSON 文件路径 |
| --output_file | 否 | - | 把结构化结果保存为 JSON 文件，父目录会自动创建 |
| --base_url | 否 | `http://192.168.30.108:3500` | 服务基础地址 |
| --timeout | 否 | `120` | 请求超时时间（秒） |

**输出形式：**
- 返回 Markdown 结果，包含接口地址、返回码、结果条数、失败模型列表和逐时流量结果
- 如果提供 `--output_file`，会把结构化结果保存为 JSON 文件，同时 stdout 仍返回 Markdown 结果

### export_jingzhou_hydro_result_to_excel.py - 结构化 JSON 转 Excel

将 `run_jingzhou_hydro_model.py --output_file` 生成的结构化 JSON 转成标准 `.xlsx` 文件。

**用法：**
```bash
python export_jingzhou_hydro_result_to_excel.py --input_file "result.json" [--output_file "result.xlsx"]
```

**参数：**
| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| --input_file | 是 | - | `run_jingzhou_hydro_model.py --output_file` 生成的结构化 JSON 文件 |
| --output_file | 否 | 与输入文件同名的 `.xlsx` | 导出的 Excel 文件路径 |

**输出工作表：**
- 会按接口 `response.data` 中的断面拆分工作表
- 如果有 7 个断面，就会生成 7 个 sheet
- sheet 名默认优先使用 `sectionName`
- 如果没有 `sectionName`，则回退到 `sectionCode`，再回退到 `stationCode`
- 每个 sheet 保存该断面的全部记录和全部字段，不裁剪列
- 如果没有可分组的断面结果，则生成一个提示性工作表 `结果明细`

**适用场景：**
- 已通过 `run_jingzhou_hydro_model.py` 获得结构化 JSON 结果
- 需要把接口结果按断面拆分成 Excel，便于按断面查看和下游使用

**调用示例：**

```python
run_script(
    skill_name='jingzhou-hydro-intake',
    script_name='export_jingzhou_hydro_result_to_excel.py',
    args=['--input_file', 'jingzhou_result.json', '--output_file', 'jingzhou_result.xlsx']
)
```

## 输入文件策略

1. 所有请求体都必须先生成本地 `input.json`
```json示例
{
  "forecastTime": "2023-07-01 00:00:00",
  "historyDuration": "2",
  "futureDuration": "3",
  "modelDataParams": [
    {"time": "2023-06-30 00:00:00", "stationCode": "S001", "rainfallValue": 5.2}，
    {"time": "2023-07-01 00:00:00", "stationCode": "S001", "rainfallValue": 4.8}
  ],
  "modelForecastRainfallParams": [
    {"time": "2023-07-01 01:00:00", "stationCode": "S001", "rainfallValue": 10.5}，
    {"time": "2023-07-01 02:00:00", "stationCode": "S001", "rainfallValue": 11.2}，
    {"time": "2023-07-01 03:00:00", "stationCode": "S001", "rainfallValue": 8.3}
  ],
  "modelRunParam": {
    "XAJ": []
  }
}
```
**"historyDuration"后跟的数字字符串的值与"modelDataParams"对应列表中的元素个数必须相同；"futureDuration"后跟的数字字符串的值与"modelForecastRainfallParams"对应列表中的元素个数必须相同，，不足的长度用0值填充**
2. 调用脚本时只允许使用 `--input_file`
3. 禁止使用分字段参数方式拼接请求体
4. 若原始数据来自表格、日志或长时序文本，应先整理成完整 JSON 文件，再执行接口
5. 若当前只有自然语言描述、原始 Excel/CSV/JSON/TXT 文件，优先先使用 `hydro-input-prep` skill 自动整理为标准中间 Excel，并转换成 `input.json`

## 对应接口

- 接口路径：`/jz/hydro_model`
- 服务示例：`http://192.168.30.108:3500/jz/hydro_model`
- 执行脚本：`run_jingzhou_hydro_model.py`

## 调用示例

使用文件调用：

```python
run_script(
    skill_name='jingzhou-hydro-intake',
    script_name='run_jingzhou_hydro_model.py',
    args=['--input_file', 'input.json', '--output_file', 'jingzhou_result.json', '--base_url', 'http://192.168.30.108:3500']
)
```




### 参数含义

靖州案例接口字段含义如下：

| 参数 | 用户需要提供什么 |
|------|------------------|
| `forecastTime` | 预测起算时间，格式 `yyyy-MM-dd HH:mm:ss` |
| `historyDuration` | 起算前需要的历史数据时长，单位小时 |
| `futureDuration` | 起算后要预测的时长，单位小时 |
| `modelDataParams` | 历史降雨数据数组，单条通常包含 `time`、`stationCode`、`rainfallValue` |
| `modelForecastRainfallParams` | 未来降雨预测数组，单条通常包含 `time`、`stationCode`、`rainfallValue` |
| `modelRunParam` | 模型参数对象，通常包含 `XAJ`、`GR4J`、`HYMOD`、`MASKGEN` |


如果服务未启动，需要先启动 `all_flask.py`，否则脚本会返回网络连接错误。
