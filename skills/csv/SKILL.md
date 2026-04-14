---
name: csv
description: "处理CSV/TSV文件时触发，包括：读取/编辑CSV文件、预览数据、创建/转换CSV、数据清洗、统计分析。**数据预览时必须使用preview_data.py仅读取前几行，禁止直接读取整个文件！**"
---

# Requirements for Outputs

## 执行工具

优先使用 `run_script` 执行 skill 内置脚本（推荐方式）：
```
run_script(skill_name='csv', script_name='脚本名.py', args='[...]')
```

如果需要运行本地临时 `.py` 文件，使用 `exec_python_file`：
```
exec_python_file(script_path='脚本路径.py', args='[...]', timeout=120)
```

如果需要先生成临时脚本或文本文件，优先使用 `write_text_file` 写文件，再调用 `exec_python_file` 执行。

`exec_bash` 仅用于真正的 PowerShell 命令，不要再用 `exec_bash(command='python ...')` 承载 Python 脚本执行。

## 数据预览

### preview_data.py - 预览文件前N行（内存安全）

**重要：这是内存安全的预览脚本，默认只读取5行，最大限制100行。**

预览 CSV/TSV/TXT 文件的前几行，用于快速了解数据结构。

**必要条件：**
- 文件必须存在
- 文件大小不超过 50MB
- 支持的格式：CSV (.csv)、TSV (.tsv)、TXT (.txt)

**用法：**
```bash
python preview_data.py --file_path "path/to/file.csv" [options]
```

**参数：**
| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| --file_path | ✅ | - | 文件路径（绝对路径） |
| --n_rows | ❌ | 5 | 预览行数（1-100，默认5） |
| --delimiter | ❌ | , | 分隔符（默认逗号） |
| --encoding | ❌ | utf-8 | 文件编码 |

**调用示例：**
```
run_script(
    skill_name='csv',
    script_name='preview_data.py',
    args='["--file_path", "D:\\data\\test.csv", "--n_rows", "5"]'
)
```

**安全措施：**
- 默认只读取5行
- 最大限制100行
- 文件大小限制50MB
- 超过10000行时停止统计

**输出示例：**
```
============================================================
文件: test.csv
总行数: 15000
预览行数: 6
列数: 4
============================================================

【表头】
Time | Flow | Rainfall | Temperature

【数据预览（前5行）】
2025-01-01 00:00 | 120.5 | 5.2 | 15.3
2025-01-01 01:00 | 121.3 | 3.8 | 14.9
...

============================================================
```

## All CSV files

### Professional Format
- Use consistent, professional format for all deliverables unless otherwise instructed
- Use appropriate column headers
- Handle missing values consistently

### Zero Data Errors
- Every CSV analysis MUST be delivered with ZERO processing errors

### Preserve Existing Templates (when updating templates)
- Study and EXACTLY match existing format, style, and conventions when modifying files
- Never impose standardized formatting on files with established patterns
- Existing template conventions ALWAYS override these guidelines

---

# CSV creation, editing, and analysis

## Overview

A user may ask you to create, edit, or analyze the contents of a .csv file. You have different tools and workflows available for different tasks.

## Reading and analyzing data

### Data analysis with pandas

For data analysis, visualization, and basic operations, use **pandas** which provides powerful data manipulation capabilities:

```python
import pandas as pd

# Read CSV
df = pd.read_csv('file.csv')  # Default: comma delimiter
df = pd.read_csv('file.tsv', sep='\t')  # TSV file
df = pd.read_csv('file.csv', encoding='gbk')  # Chinese encoding

# Read with options
df = pd.read_csv('file.csv', nrows=1000)  # Only first 1000 rows
df = pd.read_csv('file.csv', usecols=['col1', 'col2'])  # Specific columns

# Analyze
df.head()      # Preview data
df.info()      # Column info
df.describe()  # Statistics
df.dtypes      # Data types

# Write CSV
df.to_csv('output.csv', index=False)
df.to_csv('output.csv', encoding='utf-8-sig')  # Excel-compatible
```

## CSV File Workflows

## CRITICAL: Use pandas, Not Hardcoded Values

**Always use pandas for data processing instead of calculating values manually.** This ensures the CSV remains dynamic and updateable.

### ❌ WRONG - Hardcoding Calculated Values
```python
# Bad: Calculating in Python and hardcoding result
total = df['Sales'].sum()
df['Total'] = 5000  # Hardcodes 5000

# Bad: Computing statistics in Python
mean_value = df['Value'].mean()
df['Mean'] = 42.5  # Hardcodes 42.5
```

### ✅ CORRECT - Using pandas Operations
```python
# Good: Let pandas calculate
df['Total'] = df['Sales'].sum()

# Good: pandas calculation for statistics
df['Mean'] = df['Value'].mean()

# Good: Using pandas for data transformations
df['Normalized'] = (df['Value'] - df['Value'].mean()) / df['Value'].std()
```

## Common Workflow
1. **Read**: Load CSV with pandas
2. **Analyze**: Explore data structure, types, statistics
3. **Modify**: Add/edit columns, transform data
4. **Save**: Write to CSV
5. **Verify**: Check output file

### Creating new CSV files

```python
import pandas as pd

# Create from data
data = {
    'Time': ['2025-01-01 00:00', '2025-01-01 01:00', '2025-01-01 02:00'],
    'Flow': [120.5, 121.3, 122.1],
    'Rainfall': [5.2, 3.8, 2.1]
}
df = pd.DataFrame(data)

# Save to CSV
df.to_csv('output.csv', index=False, encoding='utf-8-sig')
```

### Editing existing CSV files

```python
import pandas as pd

# Load existing file
df = pd.read_csv('existing.csv')
df = pd.read_csv('existing.csv', encoding='gbk')  # Chinese encoding

# Modify columns
df['NewColumn'] = df['Column1'] + df['Column2']

# Filter rows
df_filtered = df[df['Value'] > 100]

# Handle missing values
df_filled = df.fillna(0)
df_dropped = df.dropna()

# Save modifications
df.to_csv('modified.csv', index=False)
```

## Data Type Handling

### String Operations
```python
# String columns
df['Name'] = df['Name'].str.strip()
df['Name'] = df['Name'].str.upper()
df['Name'] = df['Name'].str.replace('old', 'new')

# Parse dates
df['Date'] = pd.to_datetime(df['Date'])
df['Year'] = df['Date'].dt.year
df['Month'] = df['Date'].dt.month
```

### Numeric Operations
```python
# Numeric conversions
df['Value'] = pd.to_numeric(df['Value'], errors='coerce')

# Aggregations
df.groupby('Category')['Value'].sum()
df.groupby('Category')['Value'].mean()
df.groupby(['A', 'B'])['Value'].agg(['sum', 'mean', 'count'])
```

## Best Practices

### Library Selection
- **pandas**: Best for data analysis, bulk operations, and data export
- **csv module**: For simple file operations when pandas overhead is unnecessary

### Working with pandas
- Specify data types to avoid inference issues: `pd.read_csv('file.csv', dtype={'id': str})`
- For large files, read specific columns: `pd.read_csv('file.csv', usecols=['A', 'C', 'E'])`
- Handle dates properly: `pd.read_csv('file.csv', parse_dates=['date_column'])`
- Use `encoding='utf-8-sig'` for Excel compatibility

### Handling Large Files
```python
# Read in chunks
for chunk in pd.read_csv('large_file.csv', chunksize=10000):
    process(chunk)

# Memory-efficient dtypes
df = pd.read_csv('file.csv', dtype={'id': 'int32', 'value': 'float32'})
```

## Code Style Guidelines

**IMPORTANT**: When generating Python code for CSV operations:
- Write minimal, concise Python code without unnecessary comments
- Avoid verbose variable names and redundant operations
- Avoid unnecessary print statements
- Use method chaining where appropriate

**For CSV files themselves**:
- Document column meanings
- Document data sources for hardcoded values
- Include notes for key calculations
