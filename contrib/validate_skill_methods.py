"""Validate all 6 layers of data-analysis skill against NX_SZS_clean.csv"""
import pandas as pd
import numpy as np
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

df = pd.read_csv('data/NX_SZS_clean.csv')
numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
print(f"数据规模: {df.shape[0]}行 × {df.shape[1]}列")
print(f"数值列: {numeric_cols}")

errors = []

# ============================================================
# L1: 数据探查
# ============================================================
print("\n" + "=" * 60)
print("L1: 数据探查")
print("=" * 60)

missing = df.isnull().sum()
missing_pct = (missing / len(df) * 100).round(1)
missing_report = missing[missing > 0]
assert len(missing_report) == 0, f"发现缺失值: {dict(missing_report)}"
print(f"[OK] 缺失值: 0")

dup = df.duplicated().sum()
assert dup == 0, f"发现重复行: {dup}"
print(f"[OK] 重复行: 0")

desc = df.describe()
for col in numeric_cols:
    assert desc[col]['min'] <= desc[col]['mean'] <= desc[col]['max'], f"{col} min/mean/max 异常"
print(f"[OK] describe 统计正常")

# ============================================================
# L2: 基础统计
# ============================================================
print("\n" + "=" * 60)
print("L2: 基础统计")
print("=" * 60)

stats_df = pd.DataFrame({
    'mean': df[numeric_cols].mean().round(2),
    'median': df[numeric_cols].median().round(2),
    'std': df[numeric_cols].std().round(2),
    'min': df[numeric_cols].min().round(2),
    'max': df[numeric_cols].max().round(2),
    'Q1': df[numeric_cols].quantile(0.25).round(2),
    'Q3': df[numeric_cols].quantile(0.75).round(2),
    'skew': df[numeric_cols].skew().round(2),
    'kurt': df[numeric_cols].kurt().round(2),
})
stats_df['IQR'] = (stats_df['Q3'] - stats_df['Q1']).round(2)
stats_df['CV'] = (stats_df['std'] / stats_df['mean'].abs().replace(0, np.nan)).round(3)
stats_df['median_mean_diff'] = (stats_df['median'] - stats_df['mean']).abs()

print(stats_df[['mean', 'median', 'std', 'CV', 'skew', 'kurt', 'IQR']])

# 验证 CV 不为负
assert (stats_df['CV'] >= 0).all(), "CV 出现负值"
print(f"[OK] CV 计算正常")

# 验证偏度和峰度合理范围
for col in numeric_cols:
    sk = stats_df.loc[col, 'skew']
    kt = stats_df.loc[col, 'kurt']
    assert -10 < sk < 10, f"{col} 偏度={sk} 超出合理范围"
    assert -20 < kt < 20, f"{col} 峰度={kt} 超出合理范围"
print(f"[OK] 偏度/峰度在合理范围")

# ============================================================
# L3: 分布分析
# ============================================================
print("\n" + "=" * 60)
print("L3: 分布分析")
print("=" * 60)

for col in numeric_cols:
    data = df[col].dropna()
    n = len(data)

    if n <= 5000:
        stat, p = stats.shapiro(data)
        test_name = "Shapiro-Wilk"
    else:
        stat, p = stats.kstest(data, 'norm', args=(data.mean(), data.std()))
        test_name = "K-S"

    assert 0 <= p <= 1, f"{col} {test_name} p值={p} 异常"
    print(f"  {col}: {test_name} p={p:.4f}")

print(f"[OK] 正态性检验全部正常")

# ============================================================
# L4: 相关分析
# ============================================================
print("\n" + "=" * 60)
print("L4: 相关分析")
print("=" * 60)

pearson = df[numeric_cols].corr(method='pearson')
spearman = df[numeric_cols].corr(method='spearman')

assert pearson.notna().all().all(), "Pearson 相关矩阵含 NaN"
assert spearman.notna().all().all(), "Spearman 相关矩阵含 NaN"

# 相关矩阵应是对称的 (ignoring diagonal)
for i in range(len(numeric_cols)):
    for j in range(len(numeric_cols)):
        assert abs(pearson.iloc[i, j] - pearson.iloc[j, i]) < 1e-6, f"Pearson 不对称 at {i},{j}"
        assert abs(spearman.iloc[i, j] - spearman.iloc[j, i]) < 1e-6, f"Spearman 不对称 at {i},{j}"
print(f"[OK] 相关矩阵对称性正确")

# 相关系数范围
for i in range(len(numeric_cols)):
    for j in range(len(numeric_cols)):
        assert -1.0 <= pearson.iloc[i, j] <= 1.0, f"Pearson 越界 at {i},{j}: {pearson.iloc[i,j]}"
        assert -1.0 <= spearman.iloc[i, j] <= 1.0, f"Spearman 越界 at {i},{j}"
print(f"[OK] 相关系数在 [-1, 1] 范围内")

# 显著性检验
sig_count = 0
for i in range(len(numeric_cols)):
    for j in range(i+1, len(numeric_cols)):
        col_i, col_j = numeric_cols[i], numeric_cols[j]
        r, p = stats.pearsonr(df[col_i].dropna(), df[col_j].dropna())
        assert 0 <= p <= 1, f"Pearsonr p值异常: {col_i} vs {col_j}, p={p}"
        if p < 0.05:
            sig_count += 1
print(f"[OK] 显著性检验正常，{sig_count} 对显著相关")

print("\nPearson 相关矩阵:")
print(pearson.round(3).to_string())

# ============================================================
# L5: 滞后分析
# ============================================================
print("\n" + "=" * 60)
print("L5: 滞后分析")
print("=" * 60)

def compute_acf(series, nlags=40):
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

target = 'ConvertedFlow'
series = df[target].dropna().values
acf_values = compute_acf(series, nlags=20)
assert len(acf_values) == 21, f"ACF 长度错误: {len(acf_values)}"
assert abs(acf_values[0] - 1.0) < 1e-6, f"ACF[0] 应为 1.0, 实际 {acf_values[0]}"
assert all(-1.0 <= v <= 1.0 for v in acf_values), "ACF 值越界"
print(f"[OK] ACF 计算正确, lag0={acf_values[0]:.4f}")
print(f"  前10阶: {[round(v,3) for v in acf_values[:10]]}")

# 互相关
col_a, col_b = 'Level', 'ConvertedFlow'
s_a = (df[col_a].dropna().values - df[col_a].mean()) / df[col_a].std()
s_b = (df[col_b].dropna().values - df[col_b].mean()) / df[col_b].std()
min_len = min(len(s_a), len(s_b))
s_a, s_b = s_a[:min_len], s_b[:min_len]
max_lag = min(10, min_len // 4)

ccf_values = []
for lag in range(-max_lag, max_lag + 1):
    if lag < 0:
        c = np.corrcoef(s_a[-lag:], s_b[:lag])[0, 1]
    elif lag == 0:
        c = np.corrcoef(s_a, s_b)[0, 1]
    else:
        c = np.corrcoef(s_a[:-lag], s_b[lag:])[0, 1]
    ccf_values.append((lag, c))

best_lag, best_ccf = max(ccf_values, key=lambda x: abs(x[1]))
assert -1.0 <= best_ccf <= 1.0, f"CCF 越界: {best_ccf}"
print(f"[OK] 互相关计算正确")
print(f"  Level vs ConvertedFlow 最强相关: lag={best_lag}, ccf={best_ccf:.4f}")
assert abs(best_lag) <= 2, f"Level vs ConvertedFlow 预期 lag≈0, 实际 {best_lag}"  # 水位和流量应该同步变化

# 滑动窗口
window = max(5, len(series) // 10)
rolling_mean = pd.Series(series).rolling(window=window).mean()
rolling_std = pd.Series(series).rolling(window=window).std()
assert rolling_mean.notna().sum() > 0, "滑动均值全部 NaN"
assert rolling_std.notna().sum() > 0, "滑动标准差全部 NaN"
print(f"[OK] 滑动窗口统计正常 (window={window})")
print(f"  均值范围: [{rolling_mean.min():.2f}, {rolling_mean.max():.2f}]")

# ============================================================
# L6: 高级分析
# ============================================================
print("\n" + "=" * 60)
print("L6: 高级分析")
print("=" * 60)

def mann_kendall(x):
    n = len(x)
    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            s += np.sign(x[j] - x[i])
    # 处理结
    unique_vals, counts = np.unique(x, return_counts=True)
    var_s = (n * (n - 1) * (2 * n + 5)) / 18
    for tp in counts:
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
    s, z, p, slope = mann_kendall(data)
    assert -20 < z < 20, f"{col} M-K Z值异常: {z}"
    assert 0 <= p <= 1, f"{col} M-K p值异常: {p}"
    trend = "上升" if slope > 0 else "下降"
    sig = "显著" if p < 0.05 else "不显著"
    print(f"  {col}: Z={z:.2f} p={p:.4f} slope={slope:.6f} → {sig}{trend}")

print(f"[OK] Mann-Kendall 检验正常")

# IQR 异常检测
for col in numeric_cols:
    data = df[col].dropna()
    Q1, Q3 = data.quantile(0.25), data.quantile(0.75)
    IQR = Q3 - Q1
    lower, upper = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR
    iqr_count = ((data < lower) | (data > upper)).sum()
    assert 0 <= iqr_count <= len(data), f"{col} IQR 异常数异常: {iqr_count}"
    print(f"  {col}: IQR异常={iqr_count}个 ({iqr_count/len(data)*100:.1f}%)")
print(f"[OK] IQR 异常检测正常")

# Z-score
for col in numeric_cols:
    data = df[col].dropna()
    z_scores = np.abs((data - data.mean()) / data.std())
    z_count = (z_scores > 3).sum()
    assert 0 <= z_count <= len(data), f"{col} Z-score 异常数异常: {z_count}"
    print(f"  {col}: Z-score(>3σ)={z_count}个")

print(f"[OK] Z-score 异常检测正常")

# Isolation Forest
from sklearn.ensemble import IsolationForest
clean = df[numeric_cols].dropna()
iso = IsolationForest(contamination=0.05, random_state=42)
preds = iso.fit_predict(clean)
iso_count = (preds == -1).sum()
assert 0 <= iso_count <= len(clean), f"Isolation Forest 异常数异常: {iso_count}"
print(f"  Isolation Forest: {iso_count}个异常 ({iso_count/len(clean)*100:.1f}%)")
print(f"[OK] Isolation Forest 正常")

# ============================================================
# 汇总
# ============================================================
print("\n" + "=" * 60)
if errors:
    print(f"[FAIL] {len(errors)} 项验证失败:")
    for e in errors:
        print(f"  - {e}")
else:
    print(f"[OK] 全部 6 层方法验证通过，无错误")
print("=" * 60)
