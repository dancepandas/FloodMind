---
name: chronos
description: "Use this skill whenever the user provides historical flow data and wants to predict future values using a time-series large model / AI model / data-driven model. Trigger when the user mentions: 时序大模型, AI预测, 数据驱动模型, Chronos, 流量预测, flood prediction, time series forecasting — even casually like '用AI预测一下流量' or '帮我预测未来的水位'. Do NOT trigger when the user only wants to preview or analyze data without prediction, or when they want to validate historical model accuracy (use validation skill instead)."
---

# chronos — 时序预测

基于时序大模型（Chronos）的洪水预测，支持单变量和协变量模式。

## 使用场景

- ✅ 用户提供历史流量数据并要求预测未来值
- ✅ 用户提到"时序大模型"/"AI模型"/"数据驱动模型"进行预测
- ❌ 仅预览或分析数据（用 xlsx/csv 技能的 `preview_data.py`）
- ❌ 评估模型历史精度（用 validation 技能）
- ❌ 没有历史数据

## 可执行脚本

### flood_prediction.py

| 参数 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `--times` | ✅ | - | 历史时间序列（JSON 数组，格式 `YYYY-MM-DD HH:MM:SS`） |
| `--flows` | ✅ | - | 历史流量序列（JSON 数组） |
| `--predict_steps` | ❌ | `8` | 预测步数 |
| `--mode` | ❌ | `univariate` | 预测模式 |
| `--past_covariates` | ❌ | - | 历史协变量（JSON 对象） |
| `--future_covariates` | ❌ | - | 未来协变量（JSON 对象） |
| `--output_file` | ❌ | - | 结构化预测结果保存为 JSON |

**预测模式：**

| 模式 | 说明 | 适用场景 |
|---|---|---|
| `univariate` | 单变量预测 | 仅用历史流量预测 |
| `past_covariates` | 含过去协变量 | 加入历史降雨、上游流量等 |
| `future_covariates` | 含未来协变量 | 加入气象预报等未来已知信息 |

**调用示例：**

基础预测：
```
Bash(
    command='python flood_prediction.py --times "[\"2025-01-01 00:00:00\", \"2025-01-01 01:00:00\", \"2025-01-01 02:00:00\", \"2025-01-01 03:00:00\", \"2025-01-01 04:00:00\"]" --flows "[120.5, 125.3, 130.2, 128.7, 132.1]" --predict_steps 8 --output_file chronos_result.json',
    description='Run Chronos flood prediction'
)
```

含协变量预测：
```
Bash(
    command='python flood_prediction.py --times "[\"2025-01-01 00:00:00\", ...]" --flows "[120.5, 125.3, ...]" --mode past_covariates --past_covariates "{\"rainfall\": [5.2, 3.1, ...]}"',
    description='Run Chronos prediction with covariates'
)
```

## 大时序输入策略

当 `--times`、`--flows`、`--past_covariates` 内容较长时：

1. 不要在 `args` 中直接内联超长 JSON 数组
2. 先整理到本地文件，再由脚本读取后执行
3. 数据来自表格/CSV/Excel 时，优先先做文件整理，不要手工拼接超长参数
4. 只有历史点数较少时才适合直接内联

## 数据输入方式

### 方式 1：用户直接提供流量数值

用户说"现在吴堡站流量为 150、160、170、180、165 m³/s（每隔30分钟），预测未来4小时"时：

1. 提取流量值：`[150, 160, 170, 180, 165]`
2. 推断时间间隔：30分钟
3. 计算预测步数：4小时 → 8步
4. 自动生成时间序列，从当前时间开始每隔30分钟
5. 调用 `flood_prediction.py`

### 方式 2：用户提供数据文件

使用 xlsx/csv 技能的 `preview_data.py` 预览文件结构，提取数据后再调用本技能。

## 注意事项

- 历史数据至少需要 5 个时间点
- 时间格式为 `YYYY-MM-DD HH:MM:SS`
- 协变量数据长度须与历史数据一致
- 模型首次加载较慢（约30秒），需要预热