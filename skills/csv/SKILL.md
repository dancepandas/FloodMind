---
name: csv
description: "Use this skill any time a CSV, TSV, or tabular text file is the primary input or output. This includes: reading, editing, or creating .csv/.tsv files; previewing CSV data; cleaning or restructuring messy tabular data; statistical analysis of CSV contents; converting between tabular formats. Trigger whenever the user references a CSV file by name or path — even casually like 'the csv in my downloads' — and wants something done to it. Also trigger for data cleaning tasks on messy tabular files. Do NOT trigger when the primary deliverable is an Excel file with formulas/formatting (use xlsx skill instead), or when the data is already in a database."
---

# CSV 文件处理

## 执行工具

优先使用 `Bash` 执行 skill 内置脚本：
```
Bash(command='python preview_data.py --file_path "path/to/file.csv"', description='Preview CSV data')
```

需要运行本地临时 `.py` 文件时，也使用 `Bash`：
```
Bash(command='python temp_script.py', description='Run temp Python script')
```

需要先生成临时脚本，先用 `Write` 写文件，再用 `Bash` 执行。

## 数据预览

### preview_data.py — 预览文件前 N 行（内存安全）

**默认只读取 5 行，最大限制 100 行。这是预览 CSV 的首选方式，禁止直接读取整个文件。**

```bash
python preview_data.py --file_path "path/to/file.csv" [options]
```

| 参数 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `--file_path` | ✅ | - | 文件路径（绝对路径） |
| `--n_rows` | ❌ | `5` | 预览行数（1-100） |
| `--delimiter` | ❌ | `,` | 分隔符 |
| `--encoding` | ❌ | `utf-8` | 文件编码 |

**调用示例：**
```
Bash(
    command='python preview_data.py --file_path "D:\\data\\test.csv" --n_rows 5',
    description='Preview CSV data'
)
```

安全措施：默认 5 行、最大 100 行、文件大小限制 50MB、超过 10000 行停止统计。

## CSV 创建与编辑

### 用 pandas 创建

```python
import pandas as pd

data = {'Time': ['2025-01-01 00:00', '2025-01-01 01:00'], 'Flow': [120.5, 121.3]}
df = pd.DataFrame(data)
df.to_csv('output.csv', index=False, encoding='utf-8-sig')
```

### 用 pandas 编辑

```python
import pandas as pd

df = pd.read_csv('existing.csv')
df['NewColumn'] = df['Column1'] + df['Column2']
df.to_csv('modified.csv', index=False)
```

### 用 pandas 分析

```python
import pandas as pd

df = pd.read_csv('file.csv')
df.head()
df.info()
df.describe()
df.groupby('Category')['Value'].sum()
```

## 最佳实践

- 使用 `encoding='utf-8-sig'` 保证 Excel 兼容
- 指定数据类型避免推断问题：`pd.read_csv('file.csv', dtype={'id': str})`
- 大文件分块读取：`pd.read_csv('large_file.csv', chunksize=10000)`
- 日期列指定解析：`pd.read_csv('file.csv', parse_dates=['date_column'])`

## 代码风格

- 写精简的 Python 代码，不添加不必要的注释
- 不加多余的 print 语句
- CSV 文件本身应记录列含义和数据来源