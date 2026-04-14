# Plotting Guide

## Minimal template

```python
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

input_path = r"D:\path\data.csv"
output_path = r"D:\path\output.png"

df = pd.read_csv(input_path)
required_columns = ["x", "y"]
missing = [col for col in required_columns if col not in df.columns]
if missing:
    raise ValueError(f"Missing columns: {missing}; available columns: {list(df.columns)}")

output_file = Path(output_path)
output_file.parent.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(10, 5))
ax.plot(df["x"], df["y"], linewidth=1.5)
ax.set_title("Custom Chart")
ax.set_xlabel("x")
ax.set_ylabel("y")
fig.autofmt_xdate()
fig.savefig(output_file, dpi=200, bbox_inches="tight")
```

## Recommended tool order

- First try `search_artifacts` to find reusable plotting scripts in current or reusable outputs
- If a matching script exists, use `read_artifact` to inspect it and adapt it with minimal edits
- Use `write_text_file` to write the task-specific `.py` script
- Use `exec_python_file` to run the script
- Use `exec_bash` only for actual PowerShell commands, not for embedding long Python code

## Pre-flight checklist

- Confirm the input file exists
- Confirm required columns exist
- Confirm the output filename and format
- Keep the script focused on one chart request
- If the first run fails, fix the script instead of rerunning the same command

## Common unit expressions

When generating chart titles, axis labels, legends, table headers, or Markdown summaries, prefer standard unit notation and keep it consistent across the whole output.

### Recommended forms

- Flow / discharge: `m³/s`
- Rainfall depth: `mm`
- Water level: `m`
- Reservoir volume: `m³`
- Area: `㎡` or `m²`
- Areal rainfall intensity / depth per area context: keep the business term first, then add unit, for example `计算面降雨量(mm)`

### Practical guidance

- In Chinese labels, prefer forms such as `流量（m³/s）`、`入库流量（m³/s）`、`面积（㎡）`
- In English labels, prefer forms such as `Flow (m³/s)`、`Area (m²)`
- If a user already specifies a unit style, follow the user's wording first
- Do not mix `m3/s` and `m³/s` in the same chart or report unless a downstream system explicitly requires ASCII-only output
- For plain text or CSV headers where superscripts are inconvenient, `m3/s` and `m2` are acceptable fallbacks, but in chart rendering and Markdown summaries prefer `m³/s` and `㎡` / `m²`

### Python string examples

```python
ax.set_ylabel('入库流量（m³/s）')
ax.set_xlabel('面积（㎡）')
ax.set_title('霍口水库未来24小时入库流量过程线')
```

```python
summary = '峰值流量：1520.4 m³/s\n汇水面积：128.6 ㎡'
```

## Reuse-first workflow

```text
search_artifacts(query='plot line csv png', scope='reusable', limit=5)
```

```text
read_artifact(artifact_path='data/sessions/session-xxxx/outputs/plot_line.py')
```

If no good template is found, create a fresh script with the templates below.

## `write_text_file` Templates

### Line chart from CSV

```text
write_text_file(file_path='artifacts/plot_line.py', content='''
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

input_path = r'D:\path\data.csv'
output_path = r'D:\path\flow_line.png'

df = pd.read_csv(input_path)
required = ['Time', 'Flow']
missing = [c for c in required if c not in df.columns]
if missing:
    raise ValueError(f'Missing columns: {missing}; available columns: {list(df.columns)}')

df['Time'] = pd.to_datetime(df['Time'])
output_file = Path(output_path)
output_file.parent.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(10, 5))
ax.plot(df['Time'], df['Flow'], color='#1f77b4', linewidth=1.8)
ax.set_title('Flow Time Series')
ax.set_xlabel('Time')
ax.set_ylabel('Flow')
ax.grid(True, alpha=0.3)
fig.autofmt_xdate()
fig.savefig(output_file, dpi=200, bbox_inches='tight')
''')
```

```text
exec_python_file(script_path='artifacts/plot_line.py', timeout=120)
```

### Bar chart from CSV

```text
write_text_file(file_path='artifacts/plot_bar.py', content='''
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

input_path = r'D:\path\data.csv'
output_path = r'D:\path\rain_bar.png'

df = pd.read_csv(input_path)
required = ['Station', 'Rainfall']
missing = [c for c in required if c not in df.columns]
if missing:
    raise ValueError(f'Missing columns: {missing}; available columns: {list(df.columns)}')

output_file = Path(output_path)
output_file.parent.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(10, 5))
ax.bar(df['Station'], df['Rainfall'], color='#2a9d8f', width=0.7)
ax.set_title('Station Rainfall')
ax.set_xlabel('Station')
ax.set_ylabel('Rainfall')
ax.tick_params(axis='x', rotation=30)
ax.grid(True, axis='y', alpha=0.3)
fig.savefig(output_file, dpi=200, bbox_inches='tight')
''')
```

```text
exec_python_file(script_path='artifacts/plot_bar.py', timeout=120)
```

### Scatter chart with color mapping

```text
write_text_file(file_path='artifacts/plot_scatter.py', content='''
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

input_path = r'D:\path\data.csv'
output_path = r'D:\path\scatter.png'

df = pd.read_csv(input_path)
required = ['Rainfall', 'Flow', 'WaterLevel']
missing = [c for c in required if c not in df.columns]
if missing:
    raise ValueError(f'Missing columns: {missing}; available columns: {list(df.columns)}')

output_file = Path(output_path)
output_file.parent.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(8, 6))
sc = ax.scatter(df['Rainfall'], df['Flow'], c=df['WaterLevel'], cmap='viridis', alpha=0.8)
ax.set_title('Rainfall vs Flow')
ax.set_xlabel('Rainfall')
ax.set_ylabel('Flow')
fig.colorbar(sc, ax=ax, label='WaterLevel')
fig.savefig(output_file, dpi=200, bbox_inches='tight')
''')
```

```text
exec_python_file(script_path='artifacts/plot_scatter.py', timeout=120)
```

### Dual-axis chart

```text
write_text_file(file_path='artifacts/plot_dual_axis.py', content='''
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

input_path = r'D:\path\data.csv'
output_path = r'D:\path\dual_axis.png'

df = pd.read_csv(input_path)
required = ['Time', 'Flow', 'Rainfall']
missing = [c for c in required if c not in df.columns]
if missing:
    raise ValueError(f'Missing columns: {missing}; available columns: {list(df.columns)}')

df['Time'] = pd.to_datetime(df['Time'])
output_file = Path(output_path)
output_file.parent.mkdir(parents=True, exist_ok=True)

fig, ax1 = plt.subplots(figsize=(10, 5))
ax2 = ax1.twinx()

line1 = ax1.plot(df['Time'], df['Flow'], color='#1d3557', linewidth=1.8, label='Flow')
bars = ax2.bar(df['Time'], df['Rainfall'], color='#8ecae6', alpha=0.55, width=0.03, label='Rainfall')

ax1.set_title('Flow and Rainfall')
ax1.set_xlabel('Time')
ax1.set_ylabel('Flow')
ax2.set_ylabel('Rainfall')
ax1.grid(True, alpha=0.25)

handles = line1 + [bars]
labels = [h.get_label() for h in handles]
ax1.legend(handles, labels, loc='upper left')

fig.autofmt_xdate()
fig.savefig(output_file, dpi=200, bbox_inches='tight')
''')
```

```text
exec_python_file(script_path='artifacts/plot_dual_axis.py', timeout=120)
```

### Boxplot with seaborn

```text
write_text_file(file_path='artifacts/plot_boxplot.py', content='''
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

input_path = r'D:\path\data.csv'
output_path = r'D:\path\boxplot.png'

df = pd.read_csv(input_path)
required = ['Station', 'Flow']
missing = [c for c in required if c not in df.columns]
if missing:
    raise ValueError(f'Missing columns: {missing}; available columns: {list(df.columns)}')

output_file = Path(output_path)
output_file.parent.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(10, 5))
sns.boxplot(data=df, x='Station', y='Flow', ax=ax, color='#84a59d')
ax.set_title('Flow Distribution by Station')
ax.set_xlabel('Station')
ax.set_ylabel('Flow')
ax.tick_params(axis='x', rotation=30)
fig.savefig(output_file, dpi=200, bbox_inches='tight')
''')
```

```text
exec_python_file(script_path='artifacts/plot_boxplot.py', timeout=120)
```

### Heatmap from correlation matrix

```text
write_text_file(file_path='artifacts/plot_heatmap.py', content='''
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

input_path = r'D:\path\data.csv'
output_path = r'D:\path\corr_heatmap.png'

df = pd.read_csv(input_path)
numeric_df = df.select_dtypes(include='number')
if numeric_df.shape[1] < 2:
    raise ValueError(f'Need at least 2 numeric columns; available columns: {list(df.columns)}')

output_file = Path(output_path)
output_file.parent.mkdir(parents=True, exist_ok=True)

corr = numeric_df.corr(numeric_only=True)
fig, ax = plt.subplots(figsize=(8, 6))
sns.heatmap(corr, annot=True, cmap='Blues', fmt='.2f', ax=ax)
ax.set_title('Correlation Heatmap')
fig.savefig(output_file, dpi=200, bbox_inches='tight')
''')
```

```text
exec_python_file(script_path='artifacts/plot_heatmap.py', timeout=120)
```

## Adaptation notes

- Replace file path, column names, title, and output filename first
- If the user needs Excel input, change `pd.read_csv(...)` to `pd.read_excel(...)`
- If the user already has prior tool output instead of a file, first look for a reusable script with `search_artifacts`; if none fits, write a small script that reconstructs a DataFrame first
- Keep the first version minimal; only add style polish after the script runs successfully
