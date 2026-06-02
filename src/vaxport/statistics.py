"""统计计算工具 — 纯 Python stdlib，无重依赖

为 vaxport 提供疫苗生产过程所需的统计分析方法：
- 基础统计 (均值/标准差/CV)
- 过程能力指数 (Cpk)
- 线性趋势检测
- 离群值检测 (IQR)
- 相关性分析 (Pearson)
- 组间对比 (Welch t-test)
- 控制图限值 (3σ)
"""

import json
import math
from statistics import mean, median, stdev, StatisticsError


def run_statistics(operation: str, data: str, options: str = "{}") -> dict:
    """统计计算入口。

    Args:
        operation: 操作类型 (basic_stats/cpk/trend/outlier/correlation/compare_groups/control_limits)
        data: 输入数据，JSON 格式字符串
        options: 额外选项，JSON 格式字符串（如 usl/lsl 等）

    Returns:
        dict: 计算结果
    """
    try:
        opts = json.loads(options) if isinstance(options, str) else options
    except json.JSONDecodeError:
        return {"error": f"options JSON 解析失败: {options[:100]}"}

    handlers = {
        "basic_stats": _basic_stats,
        "cpk": _cpk,
        "trend": _trend,
        "outlier": _outlier,
        "correlation": _correlation,
        "compare_groups": _compare_groups,
        "control_limits": _control_limits,
    }

    handler = handlers.get(operation)
    if not handler:
        return {"error": f"未知操作: {operation}，支持: {', '.join(handlers)}"}

    try:
        return handler(data, opts)
    except Exception as e:
        return {"error": f"计算失败: {type(e).__name__}: {e}"}


def _parse_array(data) -> list:
    """解析输入数据为数值列表"""
    if isinstance(data, list):
        values = data
    elif isinstance(data, str):
        try:
            values = json.loads(data)
        except json.JSONDecodeError:
            return None
    else:
        return None

    if not isinstance(values, list) or len(values) == 0:
        return None
    try:
        return [float(v) for v in values]
    except (ValueError, TypeError):
        return None


# ── basic_stats ────────────────────────────────────────────

def _basic_stats(data, opts: dict) -> dict:
    values = _parse_array(data)
    if values is None:
        return {"error": "数据格式错误：需要数值数组，如 [1,2,3,4,5]"}

    n = len(values)
    try:
        m = mean(values)
        s = stdev(values) if n >= 2 else 0
    except StatisticsError:
        return {"error": "无法计算统计量（数据不足或全相同）"}

    cv = round((s / m * 100), 2) if m != 0 else None

    return {
        "n": n,
        "mean": round(m, 4),
        "median": round(median(values), 4),
        "std": round(s, 4),
        "cv_pct": cv,
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "range": round(max(values) - min(values), 4),
    }


# ── cpk ────────────────────────────────────────────────────

def _cpk(data, opts: dict) -> dict:
    values = _parse_array(data)
    if values is None:
        return {"error": "数据格式错误：需要数值数组"}

    usl = opts.get("usl")
    lsl = opts.get("lsl")

    if usl is None and lsl is None:
        return {"error": "需要至少指定 usl 或 lsl 中的一个"}

    n = len(values)
    try:
        m = mean(values)
        s = stdev(values) if n >= 2 else 0
    except StatisticsError:
        return {"error": "无法计算统计量"}

    if s == 0:
        return {"error": "标准差为 0，无法计算 Cpk（所有值相同）"}

    cp = None
    cpk = None
    cpu, cpl = None, None

    if usl is not None and lsl is not None:
        cp = round((usl - lsl) / (6 * s), 4)
        cpu = (usl - m) / (3 * s)
        cpl = (m - lsl) / (3 * s)
        cpk = round(min(cpu, cpl), 4)
    elif usl is not None:
        cpu = (usl - m) / (3 * s)
        cpk = round(cpu, 4)
    elif lsl is not None:
        cpl = (m - lsl) / (3 * s)
        cpk = round(cpl, 4)

    judgment = "合格" if cpk is not None and cpk >= 1.33 else ("勉强合格" if cpk is not None and cpk >= 1.0 else "不合格")

    result = {
        "n": n,
        "mean": round(m, 4),
        "std": round(s, 4),
        "cpk": cpk,
        "judgment": judgment,
    }
    if cp is not None:
        result["cp"] = cp
    if usl is not None:
        result["usl"] = usl
        result["cpu"] = round(cpu, 4) if cpu is not None else None
    if lsl is not None:
        result["lsl"] = lsl
        result["cpl"] = round(cpl, 4) if cpl is not None else None

    return result


# ── trend ──────────────────────────────────────────────────

def _trend(data, opts: dict) -> dict:
    """线性回归趋势检测。输入: [[t1,v1],[t2,v2],...] 或 {x:[...], y:[...]}"""
    if isinstance(data, dict) and "x" in data and "y" in data:
        x_vals = data["x"]
        y_vals = data["y"]
    elif isinstance(data, list) and len(data) > 0:
        if isinstance(data[0], list):
            x_vals = [p[0] for p in data]
            y_vals = [p[1] for p in data]
        else:
            x_vals = list(range(len(data)))
            y_vals = data
    elif isinstance(data, str):
        parsed = json.loads(data)
        return _trend(parsed, opts)
    else:
        return {"error": "数据格式错误：需要 [[t,v],...] 或 {x:[...], y:[...]}"}

    try:
        x_vals = [float(v) for v in x_vals]
        y_vals = [float(v) for v in y_vals]
    except (ValueError, TypeError):
        return {"error": "数据包含非数值元素"}

    n = len(x_vals)
    if n < 3:
        return {"error": "数据点不足（需要 ≥3）"}

    sum_x = sum(x_vals)
    sum_y = sum(y_vals)
    sum_xy = sum(x * y for x, y in zip(x_vals, y_vals))
    sum_x2 = sum(x * x for x in x_vals)
    sum_y2 = sum(y * y for y in y_vals)

    denominator = n * sum_x2 - sum_x * sum_x
    if denominator == 0:
        return {"error": "所有 x 值相同，无法计算趋势"}

    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n

    # R²
    y_mean = sum_y / n
    ss_total = sum((y - y_mean) ** 2 for y in y_vals)
    ss_residual = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(x_vals, y_vals))
    r_squared = round(1 - ss_residual / ss_total, 4) if ss_total != 0 else 1.0

    # 显著性近似：|r| > 0.7 → 强相关, > 0.5 → 中等, > 0.3 → 弱
    r = math.sqrt(abs(r_squared))
    if slope < 0:
        r = -r

    direction = "上升" if slope > 0 else ("下降" if slope < 0 else "稳定")
    significance = "显著" if abs(r) >= 0.7 else ("中等" if abs(r) >= 0.5 else "弱")

    return {
        "n": n,
        "slope": round(slope, 6),
        "intercept": round(intercept, 4),
        "r": round(r, 4),
        "r_squared": r_squared,
        "direction": direction,
        "significance": significance,
        "interpretation": f"趋势{direction}（{significance}，r={r:.3f}），斜率={slope:.4f}/单位",
    }


# ── outlier ────────────────────────────────────────────────

def _outlier(data, opts: dict) -> dict:
    values = _parse_array(data)
    if values is None:
        return {"error": "数据格式错误：需要数值数组"}

    n = len(values)
    if n < 4:
        return {"error": "数据点不足（需要 ≥4）"}

    sorted_vals = sorted(values)
    q1_idx = n // 4
    q3_idx = (3 * n) // 4
    q1 = sorted_vals[q1_idx]
    q3 = sorted_vals[q3_idx]
    iqr = q3 - q1

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    outliers = [v for v in values if v < lower or v > upper]

    return {
        "n": n,
        "q1": round(q1, 4),
        "q3": round(q3, 4),
        "iqr": round(iqr, 4),
        "lower_bound": round(lower, 4),
        "upper_bound": round(upper, 4),
        "outliers": [round(v, 4) for v in sorted(outliers)],
        "outlier_count": len(outliers),
        "method": "IQR (1.5×IQR)",
    }


# ── correlation ────────────────────────────────────────────

def _correlation(data, opts: dict) -> dict:
    """Pearson 相关系数。输入: {x: [...], y: [...]} 或 [[x1,y1],...]"""
    if isinstance(data, dict) and "x" in data and "y" in data:
        x_vals = data["x"]
        y_vals = data["y"]
    elif isinstance(data, list):
        if len(data) > 0 and isinstance(data[0], list):
            x_vals = [p[0] for p in data]
            y_vals = [p[1] for p in data]
        else:
            return {"error": "需要配对数据: {x:[...], y:[...]} 或 [[x1,y1],...]"}
    elif isinstance(data, str):
        parsed = json.loads(data)
        return _correlation(parsed, opts)
    else:
        return {"error": "数据格式错误"}

    try:
        x_vals = [float(v) for v in x_vals]
        y_vals = [float(v) for v in y_vals]
    except (ValueError, TypeError):
        return {"error": "数据包含非数值元素"}

    n = len(x_vals)
    if n != len(y_vals):
        return {"error": f"x 和 y 长度不一致 ({len(x_vals)} vs {len(y_vals)})"}
    if n < 3:
        return {"error": "数据点不足（需要 ≥3）"}

    mx = mean(x_vals)
    my = mean(y_vals)
    sx = stdev(x_vals)
    sy = stdev(y_vals)

    if sx == 0 or sy == 0:
        return {"error": "标准差为 0，无法计算相关系数"}

    cov = sum((x - mx) * (y - my) for x, y in zip(x_vals, y_vals)) / (n - 1)
    r = cov / (sx * sy)
    r = max(-1.0, min(1.0, r))

    abs_r = abs(r)
    strength = "强" if abs_r >= 0.7 else ("中等" if abs_r >= 0.5 else ("弱" if abs_r >= 0.3 else "极弱"))

    return {
        "n": n,
        "r": round(r, 4),
        "r_squared": round(r ** 2, 4),
        "strength": strength,
        "direction": "正相关" if r > 0 else ("负相关" if r < 0 else "无相关"),
        "interpretation": f"{strength}{'正' if r > 0 else '负'}相关 (r={r:.3f}, R²={r**2:.3f})",
    }


# ── compare_groups ─────────────────────────────────────────

def _compare_groups(data, opts: dict) -> dict:
    """两组对比（Welch's t-test）。输入: {group_a: [...], group_b: [...], labels: [...]}"""
    if isinstance(data, str):
        data = json.loads(data)

    if not isinstance(data, dict):
        return {"error": "数据格式错误：需要 {group_a: [...], group_b: [...]}"}

    a = data.get("group_a", [])
    b = data.get("group_b", [])
    labels = opts.get("labels", ["A组", "B组"])

    try:
        a_vals = [float(v) for v in a]
        b_vals = [float(v) for v in b]
    except (ValueError, TypeError):
        return {"error": "数据包含非数值元素"}

    na, nb = len(a_vals), len(b_vals)
    if na < 2 or nb < 2:
        return {"error": "每组至少需要 2 个数据点"}

    ma = mean(a_vals)
    mb = mean(b_vals)
    sa = stdev(a_vals)
    sb = stdev(b_vals)

    diff = ma - mb

    # Welch's t-test
    se = math.sqrt(sa**2 / na + sb**2 / nb)
    if se == 0:
        return {"error": "两组方差均为 0，无法检验"}

    t_stat = diff / se

    # Welch-Satterthwaite 自由度
    num = (sa**2 / na + sb**2 / nb) ** 2
    den = (sa**2 / na)**2 / (na - 1) + (sb**2 / nb)**2 / (nb - 1)
    df = num / den

    # p 值近似（Abramowitz and Stegun 近似）
    p_value = _t_pvalue(abs(t_stat), df)

    # Cohen's d
    pooled_sd = math.sqrt(((na - 1) * sa**2 + (nb - 1) * sb**2) / (na + nb - 2))
    cohens_d = abs(diff) / pooled_sd if pooled_sd != 0 else 0

    effect_size = "大" if cohens_d >= 0.8 else ("中" if cohens_d >= 0.5 else "小")
    significant = p_value < 0.05

    return {
        "group_a": {"label": labels[0] if len(labels) > 0 else "A组", "n": na, "mean": round(ma, 4), "std": round(sa, 4)},
        "group_b": {"label": labels[1] if len(labels) > 1 else "B组", "n": nb, "mean": round(mb, 4), "std": round(sb, 4)},
        "diff": round(diff, 4),
        "diff_pct": round(diff / ma * 100, 2) if ma != 0 else None,
        "t_statistic": round(t_stat, 4),
        "df": round(df, 2),
        "p_value": round(p_value, 4),
        "significant": significant,
        "cohens_d": round(cohens_d, 4),
        "effect_size": effect_size,
        "interpretation": (
            f"{'差异显著' if significant else '差异不显著'} "
            f"(t={t_stat:.2f}, p={p_value:.4f}, d={cohens_d:.2f} [{effect_size}效应]), "
            f"{labels[0] if len(labels) > 0 else 'A组'}均值={ma:.2f}, "
            f"{labels[1] if len(labels) > 1 else 'B组'}均值={mb:.2f}"
        ),
    }


def _t_pvalue(t: float, df: float) -> float:
    """Welch t-test p 值近似（基于 Beta 函数）"""
    if df <= 0:
        return 1.0
    x = df / (df + t * t)
    # 用不完全 Beta 函数的连分式近似
    p = _betainc_approx(df / 2, 0.5, x)
    return max(0.0, min(1.0, p))


def _betainc_approx(a: float, b: float, x: float) -> float:
    """不完全 Beta 函数近似（连分式法）"""
    if x < 0 or x > 1:
        return 0.0
    if x == 0 or x == 1:
        return x

    # 用正则化 Beta 函数的 Lentz 连分式
    # 对于 b=0.5 的特殊情况
    if x < (a + 1) / (a + b + 2):
        return _betainc_cont_fraction(a, b, x)
    else:
        return 1.0 - _betainc_cont_fraction(b, a, 1 - x)


def _betainc_cont_fraction(a: float, b: float, x: float) -> float:
    """不完全 Beta 函数连分式计算"""
    # 使用对数 Gamma 函数避免溢出
    import math
    log_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1 - x) - log_beta) / a

    # Lentz 连分式
    f = 1.0
    c = 1.0
    d = 1.0 - (a + b) * x / (a + 1)
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    f = d

    for m in range(1, 200):
        # 奇数步
        numer = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        d = 1.0 + numer * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + numer / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        f *= d * c

        # 偶数步
        numer = -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + numer * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + numer / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        f *= delta

        if abs(delta - 1.0) < 1e-10:
            break

    return front * f


# ── control_limits ─────────────────────────────────────────

def _control_limits(data, opts: dict) -> dict:
    values = _parse_array(data)
    if values is None:
        return {"error": "数据格式错误：需要数值数组"}

    n = len(values)
    if n < 2:
        return {"error": "数据点不足（需要 ≥2）"}

    try:
        m = mean(values)
        s = stdev(values)
    except StatisticsError:
        return {"error": "无法计算统计量"}

    sigma = opts.get("sigma", 3)
    ucl = m + sigma * s
    lcl = m - sigma * s

    # 检测超出控制限的点
    beyond_ucl = [round(v, 4) for v in values if v > ucl]
    beyond_lcl = [round(v, 4) for v in values if v < lcl]

    # Western Electric 规则检测
    rules_triggered = []
    # Rule 1: 任意点超出 3σ
    if beyond_ucl or beyond_lcl:
        rules_triggered.append("Rule 1: 超出控制限")
    # Rule 2: 连续 7 点在中心线同侧
    above = sum(1 for v in values if v > m)
    below = sum(1 for v in values if v < m)
    if above >= 7:
        rules_triggered.append(f"Rule 2: 连续 {above} 点在中心线上方")
    if below >= 7:
        rules_triggered.append(f"Rule 2: 连续 {below} 点在中心线下方")
    # Rule 3: 连续 7 点上升或下降
    if n >= 7:
        for start in range(n - 7):
            window = values[start:start + 7]
            if all(window[i] < window[i + 1] for i in range(6)):
                rules_triggered.append(f"Rule 3: 连续 7 点上升 (从第 {start + 1} 点)")
                break
            if all(window[i] > window[i + 1] for i in range(6)):
                rules_triggered.append(f"Rule 3: 连续 7 点下降 (从第 {start + 1} 点)")
                break
    # Rule 4: alternation pattern (14 points alternating up/down)
    if n >= 14:
        for start in range(n - 14):
            window = values[start:start + 14]
            alternations = sum(1 for i in range(1, 13) if (window[i] - window[i-1]) * (window[i+1] - window[i]) < 0)
            if alternations >= 12:
                rules_triggered.append(f"Rule 4: 连续 14 点交替上下 (从第 {start + 1} 点)")
                break

    in_control = len(rules_triggered) == 0

    return {
        "n": n,
        "mean": round(m, 4),
        "std": round(s, 4),
        "center_line": round(m, 4),
        "ucl": round(ucl, 4),
        "lcl": round(lcl, 4),
        "sigma": sigma,
        "beyond_ucl": beyond_ucl,
        "beyond_lcl": beyond_lcl,
        "in_control": in_control,
        "rules_triggered": rules_triggered if rules_triggered else ["无"],
    }