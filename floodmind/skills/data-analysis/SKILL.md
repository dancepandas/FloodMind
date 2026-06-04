---
name: data-analysis
description: "TRIGGER when: 用户要求分析数据、统计分析、相关性分析、滞后分析、数据探索、异常检测、趋势分析、数据分布特征等。Use this skill when the user provides a data file and wants to understand its statistical properties. DO NOT TRIGGER when: 用户只是要预览数据内容（用 csv/xlsx skill），或用户只要求预测而不关心统计分析。"
---

# 数据分析 — 递进式统计引导

知识引导型 skill，不包含固定脚本。引导模型按统计学阶梯 **层层递进** 分析数据，每层分析完成后主动汇报关键发现。

## 核心原则

1. **数据优先，图表次之** — 先算指标、读数字，再决定是否画图
2. **层层递进** — 从描述性统计开始，逐步深入到高级分析，不跳跃
3. **每层必报** — 每完成一层分析，立即向用户汇报关键发现，确认后再进入下一层
4. **自动化优先** — 优先编写临时 `.py` 脚本用 `Bash` 执行，不要手工搬运数据
5. **善用现有依赖** — pandas、numpy、scipy、matplotlib、scikit-learn 均已安装

## 数据读取

先用 `Read`（小文件）或 `Bash` + pandas 读取数据：

```python
import pandas as pd
df = pd.read_csv('file_path', encoding='utf-8')  # 或 pd.read_excel()
print(f"形状: {df.shape}")
print(f"列名: {list(df.columns)}")
print(f"数据类型:\n{df.dtypes}")
```

---

## 第 1 层：数据探查

**目标**：了解数据全貌，发现明显问题。

### 检查项

| 检查项 | 方法 | 关注点 |
|--------|------|--------|
| 基本信息 | `df.info()` | 行列数、数据类型、内存 |
| 缺失值 | `df.isnull().sum()` | 缺失比例 >20% 的列 |
| 重复行 | `df.duplicated().sum()` | 完全重复的行数 |
| 前/后 N 行 | `df.head(10)`, `df.tail(5)` | 数据格式、异常值直觉 |
| 数值列概览 | `df.describe()` | min/max 是否合理 |

### 代码模板

```python
import pandas as pd
import numpy as np

print("=" * 50)
print("第1层：数据探查")
print("=" * 50)

# 缺失值
missing = df.isnull().sum()
missing_pct = (missing / len(df) * 100).round(1)
missing_report = pd.DataFrame({'缺失数': missing, '缺失率%': missing_pct})
print(missing_report[missing_report['缺失数'] > 0])

# 重复值
dup_count = df.duplicated().sum()
print(f"重复行: {dup_count} ({dup_count/len(df)*100:.1f}%)")

# 数值列描述
print(df.describe().round(2))
```

### 汇报要点
- 数据规模（行数 × 列数）
- 哪些列有缺失、缺失比例
- 数值列的 min/max 是否有明显异常（如负值水位）

---

## 第 2 层：基础统计

**目标**：每列数值数据的集中趋势、离散程度、分布形状。

### 指标

| 类别 | 指标 | 函数 | 解读 |
|------|------|------|------|
| 集中趋势 | 均值 | `df.mean()` | 数据中心 |
| | 中位数 | `df.median()` | 均值 vs 中位数差大 → 偏态 |
| | 众数 | `df.mode().iloc[0]` | 最频繁值 |
| 离散程度 | 标准差 | `df.std()` | 变异程度 |
| | 方差 | `df.var()` | 平方量纲 |
| | 极差 | `df.max()-df.min()` | 最值跨度 |
| | IQR | `Q3-Q1` | 中间 50% 范围 |
| | 变异系数(CV) | `std/mean` | 相对离散度，跨列可比 |
| 分布形状 | 偏度(Skewness) | `df.skew()` | >0 右偏, <0 左偏 |
| | 峰度(Kurtosis) | `df.kurt()` | >0 尖峰厚尾, <0 扁平 |

### 代码模板

```python
print("\n" + "=" * 50)
print("第2层：基础统计")
print("=" * 50)

numeric_cols = df.select_dtypes(include=[np.number]).columns

stats = pd.DataFrame({
    '均值': df[numeric_cols].mean().round(2),
    '中位数': df[numeric_cols].median().round(2),
    '标准差': df[numeric_cols].std().round(2),
    '最小值': df[numeric_cols].min().round(2),
    '最大值': df[numeric_cols].max().round(2),
    'Q1(25%)': df[numeric_cols].quantile(0.25).round(2),
    'Q3(75%)': df[numeric_cols].quantile(0.75).round(2),
    'IQR': (df[numeric_cols].quantile(0.75) - df[numeric_cols].quantile(0.25)).round(2),
    '偏度': df[numeric_cols].skew().round(2),
    '峰度': df[numeric_cols].kurt().round(2),
})

# 变异系数
stats['CV'] = (stats['标准差'] / stats['均值'].abs().replace(0, np.nan)).round(3)

print(stats)
```

### 汇报要点
- 哪些指标变异系数大（CV > 1 → 高离散）
- 偏度明显的列（|skew| > 1 → 强烈偏态，需关注）
- 峰度异常的列（|kurt| > 3 → 厚尾，可能有极端值）

---

## 第 3 层：分布分析

**目标**：判断数据分布形态、是否正态、是否存在多峰。

### 方法

| 方法 | 适用场景 | 输出 |
|------|----------|------|
| 直方图 + KDE | 直观判断分布形状 | 图形 |
| Shapiro-Wilk 检验 | n ≤ 5000，正态性检验 | p 值 |
| Kolmogorov-Smirnov 检验 | n > 5000，或与特定分布比较 | p 值 |
| Q-Q 图 | 判断偏离正态的方向和位置 | 图形 |
| 箱线图 | 直观展示分位数和异常值 | 图形 |

### 代码模板

```python
from scipy import stats

print("\n" + "=" * 50)
print("第3层：分布分析")
print("=" * 50)

for col in numeric_cols:
    data = df[col].dropna()
    if len(data) < 3:
        continue

    print(f"\n--- {col} ---")

    # 正态性检验
    if len(data) <= 5000:
        stat, p = stats.shapiro(data)
        test_name = "Shapiro-Wilk"
    else:
        stat, p = stats.kstest(data, 'norm', args=(data.mean(), data.std()))
        test_name = "K-S"

    is_normal = "正态" if p > 0.05 else "非正态"
    print(f"{test_name}检验: statistic={stat:.4f}, p={p:.4f} → {is_normal}")

    # 偏度峰度判断
    sk = data.skew()
    kt = data.kurt()
    print(f"偏度={sk:.2f} 峰度={kt:.2f}")
```

### 汇报要点
- 每列是否服从正态分布（p > 0.05）
- 非正态时指出偏态方向（左偏/右偏）
- 是否建议对数变换或其他变换

---

## 第 4 层：相关分析

**目标**：发现变量间的线性/单调关系。

### 方法

| 方法 | 适用场景 | 解读 |
|------|----------|------|
| Pearson 相关系数 | 正态连续变量 | 线性相关强度 |
| Spearman 秩相关系数 | 非正态或有序变量 | 单调相关强度 |
| 相关矩阵热力图 | 多变量关系概览 | 一目了然 |
| 散点矩阵 | 两两变量可视化 | 发现非线性模式 |

### 代码模板

```python
print("\n" + "=" * 50)
print("第4层：相关分析")
print("=" * 50)

# Pearson 相关矩阵
pearson_corr = df[numeric_cols].corr(method='pearson').round(3)
print("\nPearson 相关系数矩阵:")
print(pearson_corr)

# Spearman 秩相关矩阵
spearman_corr = df[numeric_cols].corr(method='spearman').round(3)
print("\nSpearman 秩相关系数矩阵:")
print(spearman_corr)

# 找出强相关对（|r| > 0.7）
print("\n强相关变量对 (|r| > 0.7):")
for i in range(len(numeric_cols)):
    for j in range(i+1, len(numeric_cols)):
        col_i, col_j = numeric_cols[i], numeric_cols[j]
        r = pearson_corr.loc[col_i, col_j]
        if abs(r) > 0.7:
            print(f"  {col_i} ↔ {col_j}: r={r:.3f}")

# 显著性检验
for i in range(len(numeric_cols)):
    for j in range(i+1, len(numeric_cols)):
        col_i, col_j = numeric_cols[i], numeric_cols[j]
        r, p = stats.pearsonr(df[col_i].dropna(), df[col_j].dropna())
        if p < 0.05:
            sig = "***" if p < 0.001 else ("**" if p < 0.01 else "*")
            print(f"  {col_i} ↔ {col_j}: r={r:.3f}, p={p:.4f} {sig}")
```

### 汇报要点
- 哪些变量对存在强相关（|r| > 0.7 → 可能存在共线性）
- Pearson 和 Spearman 差异大的变量对（→ 非线性单调关系）
- 显著相关的变量对及显著性水平

---

## 第 5 层：滞后分析

**目标**：发现时间序列的自相关结构、变量间的时滞关系。

### 方法

| 方法 | 用途 | 解读 |
|------|------|------|
| 自相关 ACF | 序列自身滞后相关性 | 识别周期、判断平稳性 |
| 偏自相关 PACF | 排除中间滞后影响后的相关 | 确定 AR 模型阶数 |
| 互相关 CCF | 两序列间的时滞关系 | 发现因果方向 |
| 滑动窗口统计 | 局部均值/方差变化 | 检测非平稳特征 |

### 代码模板

```python
from scipy.signal import correlate
from statsmodels.tsa.stattools import adfuller

print("\n" + "=" * 50)
print("第5层：滞后分析")
print("=" * 50)

# --- 自相关 ---
def compute_acf(series, nlags=40):
    """手动计算 ACF（不依赖 statsmodels 绘图）"""
    n = len(series)
    mean = np.mean(series)
    var = np.var(series)
    if var == 0:
        return []
    acf = []
    for lag in range(min(nlags + 1, n // 2)):
        c = np.mean((series[:n-lag] - mean) * (series[lag:] - mean))
        acf.append(c / var)
    return acf

# 对每个数值列计算 ACF
time_col = None
for col in df.columns:
    if df[col].dtype == 'object':
        try:
            pd.to_datetime(df[col])
            time_col = col
            break
        except:
            pass

# 假设数据按行有序（如果无时间列），取第一个数值列演示
target_col = numeric_cols[0] if len(numeric_cols) > 0 else None
if target_col:
    series = df[target_col].dropna().values
    acf_values = compute_acf(series, nlags=min(20, len(series)//4))
    print(f"\n{target_col} ACF (前20阶):")
    for lag, val in enumerate(acf_values[:21]):
        bar = "█" * int(abs(val) * 40) if abs(val) > 0.1 else ""
        sign = "+" if val >= 0 else "-"
        print(f"  lag={lag:2d}: {sign}{abs(val):.3f} {bar}")

# --- 互相关 ---
if len(numeric_cols) >= 2:
    col_a, col_b = numeric_cols[0], numeric_cols[1]
    s_a = (df[col_a].dropna().values - df[col_a].mean()) / df[col_a].std()
    s_b = (df[col_b].dropna().values - df[col_b].mean()) / df[col_b].std()
    min_len = min(len(s_a), len(s_b))
    s_a, s_b = s_a[:min_len], s_b[:min_len]

    max_lag = min(20, min_len // 4)
    print(f"\n互相关 {col_a} vs {col_b} (lag ±{max_lag}):")
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            ccf = np.corrcoef(s_a[-lag:], s_b[:lag])[0, 1]
        elif lag == 0:
            ccf = np.corrcoef(s_a, s_b)[0, 1]
        else:
            ccf = np.corrcoef(s_a[:-lag], s_b[lag:])[0, 1]
        bar = "█" * int(abs(ccf) * 30) if abs(ccf) > 0.15 else ""
        sign = "+" if ccf >= 0 else "-"
        print(f"  lag={lag:3d}: {sign}{abs(ccf):.3f} {bar}")

    best_lag = max(range(-max_lag, max_lag+1),
                   key=lambda l: abs(np.corrcoef(
                       s_a[-l:] if l < 0 else s_a[:-l] if l > 0 else s_a,
                       s_b[:l] if l < 0 else s_b[l:] if l > 0 else s_b
                   )[0, 1]))
    print(f"  → 最强相关滞后: lag={best_lag}")

# --- 滑动窗口统计 ---
if target_col and len(series) > 20:
    window = max(5, len(series) // 10)
    rolling_mean = pd.Series(series).rolling(window=window).mean()
    rolling_std = pd.Series(series).rolling(window=window).std()
    print(f"\n滑动窗口统计 ({target_col}, window={window}):")
    print(f"  均值范围: [{rolling_mean.min():.2f}, {rolling_mean.max():.2f}]")
    print(f"  标准差范围: [{rolling_std.min():.2f}, {rolling_std.max():.2f}]")
    print(f"  均值变化幅度: {rolling_mean.max() - rolling_mean.min():.2f}")
```

### 汇报要点
- ACF 衰减速度 → 判断序列平稳性（慢衰减 → 非平稳）
- 显著滞后阶数（ACF 首次落入置信区间）
- CCF 最强相关 lag → 指出"X 变化后大约 Y 个单位时间 Y 才会响应"
- 滑动均值是否有明显趋势方向

---

## 第 6 层：高级分析

**目标**：趋势显著性检验、异常检测、突变检测。

### 方法

| 方法 | 用途 | 适用条件 |
|------|------|----------|
| Mann-Kendall 趋势检验 | 单调趋势显著性 | 非参数，不要求正态 |
| Sen's Slope | 趋势斜率估计 | 配合 M-K 使用 |
| IQR 异常检测 | 基于分位数的异常值 | 不需要分布假设 |
| Z-score 异常检测 | 基于标准差的异常值 | 近似正态数据 |
| Pettitt 突变检验 | 单变点检测 | 非参数 |
| Isolation Forest | 多维异常检测 | 多变量场景 |

### 代码模板

```python
from scipy import stats
from sklearn.ensemble import IsolationForest

print("\n" + "=" * 50)
print("第6层：高级分析")
print("=" * 50)

# --- Mann-Kendall 趋势检验 ---
def mann_kendall(x):
    """手动 Mann-Kendall 检验"""
    n = len(x)
    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            s += np.sign(x[j] - x[i])
    # 方差
    ties = {}
    for v in x:
        ties[v] = ties.get(v, 0) + 1
    var_s = (n * (n - 1) * (2 * n + 5)) / 18
    for tp in ties.values():
        if tp > 1:
            var_s -= (tp * (tp - 1) * (2 * tp + 5)) / 18
    if var_s <= 0:
        var_s = 1e-10
    z = (s - np.sign(s)) / np.sqrt(var_s)
    p = 2 * (1 - stats.norm.cdf(abs(z)))
    # Sen's Slope
    slopes = []
    for i in range(n - 1):
        for j in range(i + 1, n):
            slopes.append((x[j] - x[i]) / (j - i))
    sen_slope = np.median(slopes)
    return s, z, p, sen_slope

for col in numeric_cols:
    data = df[col].dropna().values
    if len(data) < 10:
        continue
    s, z, p, slope = mann_kendall(data)
    trend = "上升" if slope > 0 else "下降"
    sig = "显著" if p < 0.05 else "不显著"
    print(f"\n{col} Mann-Kendall: Z={z:.2f}, p={p:.4f}, Sen's Slope={slope:.4f} → {sig}{trend}趋势")

# --- 异常检测 ---
for col in numeric_cols:
    data = df[col].dropna()
    if len(data) < 4:
        continue

    # IQR 方法
    Q1, Q3 = data.quantile(0.25), data.quantile(0.75)
    IQR = Q3 - Q1
    lower, upper = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR
    iqr_outliers = ((data < lower) | (data > upper)).sum()

    # Z-score 方法
    z_scores = np.abs((data - data.mean()) / data.std())
    z_outliers = (z_scores > 3).sum()

    print(f"\n{col} 异常检测: IQR法={iqr_outliers}个, Z-score法(>3σ)={z_outliers}个")

# --- 多变量异常检测 ---
if len(numeric_cols) >= 2:
    clean = df[numeric_cols].dropna()
    if len(clean) > 10:
        iso = IsolationForest(contamination=0.05, random_state=42)
        preds = iso.fit_predict(clean)
        iso_outliers = (preds == -1).sum()
        print(f"\nIsolation Forest 多维异常: {iso_outliers}个 ({iso_outliers/len(clean)*100:.1f}%)")
```

### 汇报要点
- 哪些列存在显著单调趋势（p < 0.05）
- 趋势方向（上升/下降）和速率（Sen's Slope）
- 异常值数量和比例，是否建议剔除或单独分析
- 多维异常检测是否发现聚类异常

---

## 分析汇报模板

全部 6 层完成后，按以下结构汇总：

```
## 数据分析报告

### 1. 数据概况
- 规模、缺失情况

### 2. 描述性统计
- 各指标均值/中位数/标准差/CV
- 偏态/峰度异常项

### 3. 分布特征
- 正态性检验结果
- 非正态列的处理建议

### 4. 相关关系
- 强相关变量对
- Pearson vs Spearman 差异解读

### 5. 滞后特征
- ACF 衰减模式
- 交叉相关最强 lag

### 6. 趋势与异常
- M-K 趋势检验结果
- 异常值清单及处理建议
```

---

## 注意事项

1. **大文件先预览** — 超过 1000 行先用 `df.head(100)` 确认结构
2. **缺失值谨慎处理** — 水文数据中缺失通常是设备故障，不要随意填充
3. **时间序列重采样** — 如果数据是时间序列，先 `set_index` + `resample` 统一频率
4. **单位一致性** — 水位(m)、流量(m³/s)、降雨(mm)，不同站点可能有不同单位
5. **不要画不必要图** — 只有用户要求或数字无法清晰表达时才画图
6. **保持简洁** — 每个分析层用一段话汇报，附关键数字，不要冗长
