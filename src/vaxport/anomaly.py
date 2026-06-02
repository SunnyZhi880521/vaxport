"""SPC 异常检测 — OOT/参数漂移/设备劣化

基于 statistics.py 的现有函数扩展，纯 Python stdlib。
"""

import json
import math
from statistics import mean, stdev, StatisticsError


def detect_anomaly(data: str, method: str, options: str = "{}") -> dict:
    """SPC 异常检测入口。

    Args:
        data: 数值数组 JSON，如 "[1.2, 1.5, 1.3, 2.8, 1.4]"
        method: 检测方法 — "oot" / "drift" / "degradation"
        options: JSON 格式选项

    Returns:
        dict: {"anomalies": [...], "method": str, "summary": str}
    """
    try:
        values = json.loads(data) if isinstance(data, str) else data
        if not isinstance(values, list) or len(values) < 3:
            return {"error": "数据必须是至少3个数值的数组", "method": method}
        values = [float(v) for v in values]
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        return {"error": f"数据解析失败: {e}", "method": method}

    try:
        opts = json.loads(options) if isinstance(options, str) else options
    except (json.JSONDecodeError, TypeError):
        opts = {}

    if method == "oot":
        return _detect_oot(values, opts)
    elif method == "drift":
        return _detect_drift(values, opts)
    elif method == "degradation":
        return _detect_degradation(values, opts)
    else:
        return {"error": f"未知检测方法: {method}，支持: oot/drift/degradation"}


def _detect_oot(values: list[float], opts: dict) -> dict:
    """OOT (Out-Of-Trend) 检测 — IQR + 3σ 双方法。

    Returns:
        IQR异常点 + 3σ异常点 + 综合判定
    """
    n = len(values)
    if n < 5:
        return {"error": "OOT检测需要至少5个数据点", "method": "oot"}

    try:
        mu = mean(values)
        sigma = stdev(values) if n > 1 else 0
    except StatisticsError:
        return {"error": "统计计算失败", "method": "oot"}

    # IQR 法
    sorted_vals = sorted(values)
    q1_idx = n // 4
    q3_idx = (3 * n) // 4
    q1 = sorted_vals[q1_idx]
    q3 = sorted_vals[min(q3_idx, n - 1)]
    iqr = q3 - q1
    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr

    iqr_outliers = []
    for i, v in enumerate(values):
        if v < lower_fence or v > upper_fence:
            iqr_outliers.append({
                "index": i, "value": v,
                "direction": "low" if v < lower_fence else "high",
                "distance_from_fence": round(abs(v - (lower_fence if v < lower_fence else upper_fence)), 4),
            })

    # 3σ 法
    sigma_outliers = []
    if sigma > 0:
        upper_3s = mu + 3 * sigma
        lower_3s = mu - 3 * sigma
        for i, v in enumerate(values):
            if v > upper_3s or v < lower_3s:
                sigma_outliers.append({
                    "index": i, "value": v,
                    "direction": "low" if v < lower_3s else "high",
                    "sigma_level": round(abs(v - mu) / sigma, 2),
                })

    # 综合判定
    all_outlier_indices = set()
    for o in iqr_outliers:
        all_outlier_indices.add(o["index"])
    for o in sigma_outliers:
        all_outlier_indices.add(o["index"])

    summary_parts = []
    if all_outlier_indices:
        summary_parts.append(
            f"检测到 {len(all_outlier_indices)} 个疑似OOT点 "
            f"(IQR法: {len(iqr_outliers)}, 3σ法: {len(sigma_outliers)})"
        )
        if iqr_outliers and sigma_outliers:
            # 双方法确认的点更可信
            iqr_idx = {o["index"] for o in iqr_outliers}
            sigma_idx = {o["index"] for o in sigma_outliers}
            confirmed = iqr_idx & sigma_idx
            if confirmed:
                summary_parts.append(
                    f"其中 {len(confirmed)} 个点被双方法确认，可信度高"
                )
    else:
        summary_parts.append("未检测到OOT异常点，数据在正常范围内波动")

    if len(values) < 25:
        summary_parts.append(f"⚠️ 当前数据量(n={n})较小，OOT检测结果仅供参考")

    return {
        "method": "oot",
        "n": n,
        "statistics": {
            "mean": round(mu, 4),
            "std": round(sigma, 4) if sigma else 0,
            "q1": round(q1, 4),
            "q3": round(q3, 4),
            "iqr": round(iqr, 4),
            "iqr_lower_fence": round(lower_fence, 4),
            "iqr_upper_fence": round(upper_fence, 4),
            "sigma_3_lower": round(mu - 3 * sigma, 4) if sigma else None,
            "sigma_3_upper": round(mu + 3 * sigma, 4) if sigma else None,
        },
        "iqr_outliers": iqr_outliers,
        "sigma_outliers": sigma_outliers,
        "total_outliers": len(all_outlier_indices),
        "summary": " ".join(summary_parts),
    }


def _detect_drift(values: list[float], opts: dict) -> dict:
    """参数漂移检测 — 线性回归斜率 + CUSUM。

    Returns:
        斜率、显著性、CUSUM 累计和
    """
    n = len(values)
    if n < 5:
        return {"error": "漂移检测需要至少5个数据点", "method": "drift"}

    # 线性回归
    x_mean = (n - 1) / 2
    y_mean = mean(values)
    ss_xy = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
    ss_xx = sum((i - x_mean) ** 2 for i in range(n))

    slope = ss_xy / ss_xx if ss_xx > 0 else 0
    intercept = y_mean - slope * x_mean

    # R²
    residuals = [values[i] - (slope * i + intercept) for i in range(n)]
    ss_res = sum(r ** 2 for r in residuals)
    ss_tot = sum((v - y_mean) ** 2 for v in values)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    # 斜率显著性（简化 t 检验）
    se_slope = math.sqrt(ss_res / (n - 2) / ss_xx) if ss_xx > 0 and n > 2 else float('inf')
    t_stat = abs(slope) / se_slope if se_slope > 0 else 0

    # CUSUM
    cusum = [0]
    for v in values:
        cusum.append(cusum[-1] + (v - y_mean))
    cusum_max = max(abs(c) for c in cusum)

    # 漂移方向
    if slope > 0:
        direction = "上升"
    elif slope < 0:
        direction = "下降"
    else:
        direction = "平稳"

    # 总变化量估计
    total_change = slope * (n - 1)
    pct_change = round(total_change / y_mean * 100, 2) if y_mean != 0 else 0

    # 判定
    sig = "显著" if t_stat > 2 else "不显著"
    summary_parts = [
        f"检测到{direction}趋势",
        f"斜率={round(slope, 6)}/单位",
        f"R²={round(r_squared, 3)}",
        f"统计{sig}(t={round(t_stat, 2)})",
        f"总变化={round(total_change, 4)}({pct_change}%)",
    ]

    if abs(pct_change) > 10 and t_stat > 2:
        summary_parts.append("⚠️ 参数漂移幅度>10%且统计显著，建议调查原因")
    if r_squared < 0.3:
        summary_parts.append("R²<0.3，趋势强度较弱，需结合过程知识判断")

    return {
        "method": "drift",
        "n": n,
        "regression": {
            "slope": round(slope, 6),
            "intercept": round(intercept, 4),
            "r_squared": round(r_squared, 4),
            "t_statistic": round(t_stat, 2),
        },
        "cusum": {
            "max_deviation": round(cusum_max, 4),
            "final_cusum": round(cusum[-1], 4),
        },
        "change": {
            "direction": direction,
            "total_change": round(total_change, 4),
            "pct_change": pct_change,
        },
        "summary": "。".join(summary_parts) + "。",
    }


def _detect_degradation(values: list[float], opts: dict) -> dict:
    """设备劣化检测 — 移动平均趋势 + 波动率变化。

    Returns:
        移动平均斜率、波动率趋势、劣化指数
    """
    n = len(values)
    if n < 7:
        return {"error": "劣化检测需要至少7个数据点", "method": "degradation"}

    window = opts.get("window", min(5, n // 3))
    if window < 2:
        window = 3

    # 移动平均
    ma_values = []
    for i in range(n - window + 1):
        ma_values.append(mean(values[i:i + window]))

    # 移动平均趋势
    ma_n = len(ma_values)
    x_mean_ma = (ma_n - 1) / 2
    y_mean_ma = mean(ma_values)
    ss_xy_ma = sum((i - x_mean_ma) * (ma_values[i] - y_mean_ma) for i in range(ma_n))
    ss_xx_ma = sum((i - x_mean_ma) ** 2 for i in range(ma_n))
    ma_slope = ss_xy_ma / ss_xx_ma if ss_xx_ma > 0 else 0

    # 波动率分析（分段标准差）
    half = n // 2
    std_first = stdev(values[:half]) if len(values[:half]) > 1 else 0
    std_second = stdev(values[half:]) if len(values[half:]) > 1 else 0
    std_ratio = std_second / std_first if std_first > 0 else 1

    # 劣化指数 (0-100)
    degradation_score = 0
    factors = []

    # 趋势贡献
    if ma_slope < 0:
        degradation_score += min(40, abs(ma_slope) / abs(y_mean_ma) * 1000)
        factors.append(f"负趋势(+{min(40, abs(ma_slope) / abs(y_mean_ma) * 1000):.0f})")

    # 波动率贡献
    if std_ratio > 1.5:
        degradation_score += 30
        factors.append("波动率显著增加(+30)")
    elif std_ratio > 1.2:
        degradation_score += 15
        factors.append("波动率有所增加(+15)")

    # 近期最低点
    recent_third = values[-(n // 3):]
    overall_min = min(values)
    if min(recent_third) <= overall_min:
        degradation_score += 20
        factors.append("近期出现新低点(+20)")

    degradation_score = min(100, degradation_score)

    if degradation_score >= 60:
        level = "严重"
    elif degradation_score >= 30:
        level = "中等"
    else:
        level = "轻微或无"

    summary = (
        f"设备劣化评估: {level}劣化(指数={degradation_score}/100)。"
        f"移动平均趋势斜率={round(ma_slope, 6)}，"
        f"波动率比(后半/前半)={round(std_ratio, 2)}。"
    )
    if factors:
        summary += f" 影响因素: {'; '.join(factors)}。"
    if degradation_score >= 60:
        summary += " ⚠️ 建议安排预防性维护或校准。"

    return {
        "method": "degradation",
        "n": n,
        "moving_average": {
            "window": window,
            "ma_values": [round(v, 4) for v in ma_values],
            "ma_slope": round(ma_slope, 6),
        },
        "volatility": {
            "std_first_half": round(std_first, 4),
            "std_second_half": round(std_second, 4),
            "std_ratio": round(std_ratio, 2),
        },
        "degradation_index": degradation_score,
        "level": level,
        "factors": factors,
        "summary": summary,
    }