"""预测模型 — 效价/设备/库存趋势预测

纯 Python stdlib + 简单统计方法:
- 线性外推 (linear)
- 指数平滑 (exp_smooth)
- 移动平均 (moving_avg)
- 降解动力学 (degradation_kinetics, ICH Q1E)

零额外依赖，适合在 Agent 工具中内嵌使用。
"""

import json
import math
from statistics import mean, stdev, StatisticsError


def run_prediction(data: str, method: str, options: str = "{}") -> dict:
    """预测模型入口。

    Args:
        data: 时间序列数据 JSON
            - 数值数组: "[7.2, 7.5, 7.1, 7.8, 7.3]"
            - 时间-数值对: "[{\"t\":0,\"v\":7.2},...]"
            - 带时间标签: "{\"values\":[7.2,...],\"timestamps\":[\"2024-01\",...]}"
        method: 预测方法
            - "linear": 线性回归外推
            - "exp_smooth": 指数平滑
            - "moving_avg": 移动平均
            - "degradation": 降解动力学 (一级动力学, ICH Q1E)
        options: JSON 格式选项
            - {"horizon": 3, "alpha": 0.3, "window": 3, "confidence": 0.95}

    Returns:
        {"predictions": [...], "method": str, "metrics": {...}, "summary": str}
    """
    try:
        parsed = json.loads(data) if isinstance(data, str) else data
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"数据解析失败: {e}"}

    try:
        opts = json.loads(options) if isinstance(options, str) else options
    except (json.JSONDecodeError, TypeError):
        opts = {}

    # 提取数值序列
    if isinstance(parsed, list):
        if all(isinstance(x, (int, float)) for x in parsed):
            values = parsed
            timestamps = None
        elif all(isinstance(x, dict) and "v" in x for x in parsed):
            values = [x["v"] for x in parsed]
            timestamps = [x.get("t", i) for i, x in enumerate(parsed)]
        else:
            return {"error": "数据格式不支持，需为数值数组或[{\"v\":N,...}]格式"}
    elif isinstance(parsed, dict):
        values = parsed.get("values", [])
        timestamps = parsed.get("timestamps")
    else:
        return {"error": "数据格式不支持"}

    if len(values) < 3:
        return {"error": "需要至少3个数据点进行预测"}

    horizon = opts.get("horizon", 3)
    confidence = opts.get("confidence", 0.95)

    if method == "linear":
        return _predict_linear(values, timestamps, horizon, confidence)
    elif method == "exp_smooth":
        alpha = opts.get("alpha", 0.3)
        return _predict_exp_smooth(values, timestamps, horizon, alpha, confidence)
    elif method == "moving_avg":
        window = opts.get("window", 3)
        return _predict_moving_avg(values, timestamps, horizon, window)
    elif method == "degradation":
        return _predict_degradation(values, timestamps, horizon, confidence)
    else:
        return {"error": f"未知方法: {method}，支持: linear/exp_smooth/moving_avg/degradation"}


def _predict_linear(values, timestamps, horizon, confidence):
    """线性回归外推"""
    n = len(values)
    x = list(range(n))

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

    # 预测
    predictions = []
    for i in range(1, horizon + 1):
        pred_x = n - 1 + i
        pred_y = slope * pred_x + intercept

        # 预测区间 (简化)
        mse = ss_res / (n - 2) if n > 2 else 0
        se = math.sqrt(mse * (1 + 1/n + (pred_x - x_mean)**2 / ss_xx)) if ss_xx > 0 else 0
        z = 1.96  # 95% CI
        ci_lower = pred_y - z * se
        ci_upper = pred_y + z * se

        predictions.append({
            "step": i,
            "value": round(pred_y, 4),
            "ci_lower": round(ci_lower, 4),
            "ci_upper": round(ci_upper, 4),
        })

    # 趋势判定
    pct_change = (predictions[-1]["value"] - values[0]) / abs(values[0]) * 100 if values[0] != 0 else 0
    direction = "上升" if slope > 0 else "下降" if slope < 0 else "平稳"

    return {
        "method": "linear",
        "model": {
            "slope": round(slope, 6),
            "intercept": round(intercept, 4),
            "r_squared": round(r_squared, 4),
            "n": n,
        },
        "predictions": predictions,
        "trend": {
            "direction": direction,
            "total_pct_change": round(pct_change, 2),
            "significant": r_squared > 0.3,
        },
        "summary": f"线性回归预测: {direction}趋势(R²={r_squared:.3f}), "
                   f"{horizon}步预测值从{predictions[0]['value']}到{predictions[-1]['value']}。"
                   + (f" ⚠️ R²<0.3，预测可靠性有限。" if r_squared < 0.3 else ""),
    }


def _predict_exp_smooth(values, timestamps, horizon, alpha, confidence):
    """指数平滑预测 (Holt's linear method 简化版)"""
    n = len(values)

    # 初始化
    level = values[0]
    trend = values[1] - values[0] if n > 1 else 0
    beta = 0.1  # trend smoothing

    for t in range(1, n):
        old_level = level
        level = alpha * values[t] + (1 - alpha) * (level + trend)
        trend = beta * (level - old_level) + (1 - beta) * trend

    # 预测
    predictions = []
    for i in range(1, horizon + 1):
        pred = level + i * trend
        predictions.append({
            "step": i,
            "value": round(pred, 4),
        })

    return {
        "method": "exp_smooth",
        "model": {
            "alpha": alpha,
            "final_level": round(level, 4),
            "final_trend": round(trend, 6),
            "n": n,
        },
        "predictions": predictions,
        "summary": f"指数平滑预测(α={alpha}): {horizon}步预测，"
                   f"当前水平={level:.4f}，趋势斜率={trend:.6f}。",
    }


def _predict_moving_avg(values, timestamps, horizon, window):
    """移动平均预测 (最后 window 点均值作为预测)"""
    n = len(values)
    recent = values[-window:]
    avg = mean(recent)

    predictions = []
    for i in range(1, horizon + 1):
        predictions.append({
            "step": i,
            "value": round(avg, 4),
        })

    return {
        "method": "moving_avg",
        "model": {
            "window": window,
            "recent_mean": round(avg, 4),
            "recent_std": round(stdev(recent), 4) if len(recent) > 1 else 0,
            "n": n,
        },
        "predictions": predictions,
        "summary": f"移动平均预测(window={window}): 基于近{window}点均值={avg:.4f}，"
                   f"预测未来{horizon}步为恒定值。适合短期平稳序列。",
    }


def _predict_degradation(values, timestamps, horizon, confidence):
    """降解动力学预测 (一级动力学, ICH Q1E 稳定性数据外推)

    适用于: 疫苗效价衰减、活性成分降解
    模型: ln(C) = ln(C₀) - k·t
    """
    n = len(values)

    # 检查值是否为正（取对数需要）
    if any(v <= 0 for v in values):
        return {"error": "降解动力学要求所有数值 > 0"}

    # 对数变换
    log_values = [math.log(v) for v in values]

    # 线性回归 on log scale
    x = list(range(n))
    x_mean = (n - 1) / 2
    y_mean = mean(log_values)
    ss_xy = sum((i - x_mean) * (log_values[i] - y_mean) for i in range(n))
    ss_xx = sum((i - x_mean) ** 2 for i in range(n))

    slope = ss_xy / ss_xx if ss_xx > 0 else 0
    intercept = y_mean - slope * x_mean

    k = -slope  # 降解速率常数
    C0 = math.exp(intercept)  # 初始浓度/效价

    # R²
    residuals = [log_values[i] - (slope * i + intercept) for i in range(n)]
    ss_res = sum(r ** 2 for r in residuals)
    ss_tot = sum((v - y_mean) ** 2 for v in log_values)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    # 半衰期 t₁/₂ = ln(2)/k
    half_life = math.log(2) / k if k > 0 else float('inf')

    # 预测
    predictions = []
    current_value = values[-1]
    for i in range(1, horizon + 1):
        pred_log = intercept + slope * (n - 1 + i)
        pred_y = math.exp(pred_log)
        pct_remaining = pred_y / C0 * 100

        predictions.append({
            "step": i,
            "value": round(pred_y, 4),
            "pct_remaining": round(pct_remaining, 2),
        })

    # 判定有效期 (当效价降至初始的 X% 时)
    shelf_life_thresholds = [90, 80, 70]  # 常用阈值
    shelf_life_estimates = {}
    for threshold in shelf_life_thresholds:
        if k > 0:
            t_threshold = math.log(100 / threshold) / k
            shelf_life_estimates[f"{threshold}%"] = round(t_threshold, 1)

    summary_parts = [
        f"一级降解动力学: k={k:.6f}/单位, C₀={C0:.4f}, R²={r_squared:.3f}",
    ]
    if k > 0 and half_life < float('inf'):
        summary_parts.append(f"半衰期(t₁/₂)={half_life:.1f}单位")
    if shelf_life_estimates:
        se_parts = [f"{k}={v}单位" for k, v in shelf_life_estimates.items()]
        summary_parts.append(f"估计有效期: {'; '.join(se_parts)}")
    if r_squared < 0.8:
        summary_parts.append("⚠️ R²<0.8，降解模型拟合一般，外推需谨慎")
    if n < 6:
        summary_parts.append("⚠️ 数据点较少(n<6)，ICH Q1E建议至少6个时间点进行有效期外推")

    return {
        "method": "degradation",
        "model": {
            "k": round(k, 6),
            "C0": round(C0, 4),
            "half_life": round(half_life, 1) if k > 0 else None,
            "r_squared": round(r_squared, 4),
            "n": n,
        },
        "shelf_life_estimates": shelf_life_estimates,
        "predictions": predictions,
        "summary": "；".join(summary_parts) + "。",
    }