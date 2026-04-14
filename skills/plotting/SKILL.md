---
name: plotting
description: "TRIGGER when: 用户明确要求绘图、画图、可视化、生成图表、导出 PNG/SVG/PDF 图像，或要求自定义图表样式/布局时。DO NOT TRIGGER when: 用户只是要统计指标、模型验证指标、预测数值而未要求图表时。"
---

# Python 自定义绘图

这是知识引导型 skill，用于指导模型根据用户需求动态编写或复用 Python 绘图脚本，并通过 `exec_python_file` 执行脚本生成图表。

## 核心原则

1. 本 skill 不依赖固定绘图脚本，不要调用 `run_script`
2. 必须先明确绘图需求边界，再决定脚本内容
3. 优先顺序是：`search_artifacts` / `read_artifact` 复用历史脚本，其次 `write_text_file` 写出临时 `.py`，再用 `exec_python_file` 执行
4. `exec_bash` 只在确实需要执行 PowerShell 语句本体时使用，不要再用它承载长段 Python 代码
5. 如果信息不完整，先向用户追问，不要盲目猜测
6. 如果用户只是说“画个图”，优先追问最小必要信息，而不是误用 `stats` 或 `validation`

## 适用场景

✅ **USE when:**
- 用户要折线图、柱状图、散点图、箱线图、热力图、双轴图、组合图
- 用户要对已有分析/验证/预测结果进行可视化
- 用户要求控制标题、颜色、尺寸、标注、图例、字体、导出格式
- 用户要求生成 PNG、SVG、PDF 等图形文件

❌ **DON'T use when:**
- 用户只要统计指标，不要图
- 用户只要模型验证指标，不要图
- 用户只要预测结果表格，不要图

## 需求边界检查清单

在写脚本前，至少确认以下信息：

1. **数据来源**
   - 来自文件，还是来自上一轮工具结果
   - 如果是文件，路径是什么，格式是什么
2. **图表类型**
   - 折线图、柱状图、散点图、箱线图、热力图、面积图、组合图
3. **字段映射**
   - x 轴用什么
   - y 轴用什么
   - 是否有分组、颜色映射、子图、双轴
4. **展示要求**
   - 标题、副标题、坐标轴名、单位、图例、时间格式、排序方式
5. **输出要求**
   - 输出文件名
   - 输出格式（PNG/SVG/PDF）
   - 是否需要同时导出数据表

如果以上任一关键信息缺失，先提问，不要直接写脚本。

## 推荐执行流程

### 第一步：整理绘图方案

先用自然语言给自己明确以下内容：

- 数据从哪里来
- 画什么图
- 脚本需要读取哪些列
- 输出什么文件

### 第二步：优先搜索可复用绘图脚本

先尝试在当前会话或历史产物中搜索接近的模板：

```text
search_artifacts(query='plot line csv png', scope='reusable', limit=5)
```

如果找到了合适脚本：

- 用 `read_artifact` 读取脚本内容
- 仅对文件路径、列名、标题、输出文件名做最小修改
- 修改后仍通过 `write_text_file` 写出当前任务脚本，再用 `exec_python_file` 执行

### 第三步：通过 `write_text_file` 写 Python 脚本

优先使用 `write_text_file`，避免 PowerShell here-string 和复杂转义。

示例模式：

```text
write_text_file(file_path='artifacts/plot_chart.py', content='''
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

df = pd.read_csv(r'D:\path\data.csv')
required_columns = ['Time', 'Flow']
missing = [col for col in required_columns if col not in df.columns]
if missing:
    raise ValueError(f"Missing columns: {missing}; available columns: {list(df.columns)}")

output_path = Path(r'D:\path\output.png')
output_path.parent.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(10, 5))
ax.plot(df['Time'], df['Flow'], linewidth=1.5)
ax.set_title('Flow Time Series')
ax.set_xlabel('Time')
ax.set_ylabel('Flow')
fig.autofmt_xdate()
fig.savefig(output_path, dpi=200, bbox_inches='tight')
''')
```

### 第四步：通过 `exec_python_file` 执行脚本

```text
exec_python_file(script_path='artifacts/plot_chart.py', timeout=120)
```

更多现成模板见 `plotting_guide.md`，优先复用其中最接近用户需求的示例，再按字段名、标题、输出文件名做最小修改。

### 第五步：检查执行结果

- 如果脚本报错，先根据报错修正脚本，再重试
- 不要在完全相同的命令上无限重试
- 如果错误来自缺列、缺文件、格式不匹配，应先回到需求或数据检查

## 推荐库选择

- 常规静态图：`matplotlib`
- 需要更简洁统计绘图：`seaborn`
- 需要 DataFrame 处理：`pandas`

默认优先 `matplotlib + pandas`，除非用户明确要求更复杂风格。

## 脚本编写要求

1. 脚本只做当前用户需要的图，不要过度封装
2. 路径使用原始字符串或双反斜杠，兼容 Windows
3. 优先搜索历史可复用脚本模板；找不到时再新写脚本
4. 读取数据后先检查列是否存在，必要时打印可用列名
5. 保存图片前确保输出目录存在
6. 图表标题、坐标轴、图例尽量完整，避免生成无法解释的图片
7. 若用户提供中文标题或标签，优先沿用用户原文
8. 如果需求属于常见图型，优先复用 `plotting_guide.md` 里的 `write_text_file` + `exec_python_file` 模板
9. 涉及图表标题、坐标轴、图例、统计摘要中的单位表达时，优先参考 `plotting_guide.md` 中的 `Common unit expressions`

## 与其他 skill 的边界

- `stats`：负责统计指标，不负责画图
- `validation`：负责评估指标，不负责画图
- `prediction`：负责预测结果，不负责画图

当用户在这些结果基础上说“画图”“可视化”“导出图片”，应切换到本 skill，而不是回到原 skill 重跑。

## 常见失败原因与应对

1. **文件路径错误**：先确认文件是否存在，必要时检查 `script_path` 和输入数据路径
2. **列名不匹配**：先打印列名，再修正脚本
3. **时间列未解析**：用 `pd.to_datetime(...)`
4. **历史模板不完全匹配**：先最小修改模板，不要整段重写
5. **中文乱码或字体问题**：优先保持默认字体，必要时再单独设置字体
6. **用户需求过于模糊**：先提问，不要猜测图表类型

## 输出要求

- 最终向用户说明生成了什么图
- 如果生成了文件，只返回文件名或用户需要的结果说明，不要暴露系统内部路径
- 如果由于信息不足无法安全生成脚本，要明确告诉用户还缺什么信息
