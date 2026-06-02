"""AEFI 不良反应信号检测 — PRR/ROR/BCPNN

药物警戒中的定量信号检测方法，纯 Python stdlib 实现。

方法:
- PRR (Proportional Reporting Ratio): 比例报告比
- ROR (Reporting Odds Ratio): 报告比值比
- BCPNN (Bayesian Confidence Propagation Neural Network): 贝叶斯可信区间法
- Chi-square: 卡方检验 (Yates校正)
"""

import json
import math
from statistics import mean


def detect_signal(data: str, method: str = "all",
                  options: str = "{}") -> dict:
    """AEFI 信号检测入口。

    Args:
        data: 2×2 列联表数据 JSON
            {"a": N, "b": N, "c": N, "d": N}
            或完整数据 {"target_drug": "疫苗X", "target_ae": "发热",
                        "a": 50, "b": 200, "c": 30, "d": 5000}
            a = 目标药物+目标AE的报告数
            b = 目标药物+其他AE的报告数
            c = 其他药物+目标AE的报告数
            d = 其他药物+其他AE的报告数
        method: 检测方法 — "prr" / "ror" / "bcpnn" / "all"
        options: {"min_a": 3, "alpha_prior": 0.5} 等

    Returns:
        {"signals": [...], "summary": str}
    """
    try:
        d = json.loads(data) if isinstance(data, str) else data
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"数据解析失败: {e}"}

    try:
        opts = json.loads(options) if isinstance(options, str) else options
    except (json.JSONDecodeError, TypeError):
        opts = {}

    a = int(d.get("a", 0))
    b = int(d.get("b", 0))
    c = int(d.get("c", 0))
    d_val = int(d.get("d", 0))
    target_drug = d.get("target_drug", "")
    target_ae = d.get("target_ae", "")
    min_a = opts.get("min_a", 3)

    if a < min_a:
        return {
            "signals": [],
            "summary": f"报告数 a={a} < 最低阈值 {min_a}，不足以进行信号检测。",
            "method": method,
        }

    total = a + b + c + d_val
    if total == 0:
        return {"error": "总报告数不能为0"}

    results = []

    if method in ("prr", "all"):
        results.append(_calc_prr(a, b, c, d_val, target_drug, target_ae))

    if method in ("ror", "all"):
        results.append(_calc_ror(a, b, c, d_val, target_drug, target_ae))

    if method in ("bcpnn", "all"):
        alpha_prior = opts.get("alpha_prior", 0.5)
        results.append(_calc_bcpnn(a, b, c, d_val, total, alpha_prior,
                                   target_drug, target_ae))

    # 汇总
    signal_count = sum(1 for r in results if r.get("signal"))
    summary_parts = [
        f"{target_drug or '目标药物'} + {target_ae or '目标AE'}: "
        f"a={a}, b={b}, c={c}, d={d_val}, 总计={total}",
    ]

    if signal_count > 0:
        signals = [r["method"] for r in results if r.get("signal")]
        summary_parts.append(f"检测到信号({', '.join(signals)})")
    else:
        summary_parts.append("未检测到统计学信号")

    return {
        "signals": results,
        "summary": "；".join(summary_parts) + "。",
        "method": method,
        "contingency_table": {"a": a, "b": b, "c": c, "d": d_val, "N": total},
    }


def _calc_prr(a, b, c, d_val, drug, ae):
    """PRR (Proportional Reporting Ratio) 计算。

    PRR = [a/(a+b)] / [c/(c+d)]

    信号阈值: PRR ≥ 2, χ² ≥ 4, a ≥ 3 (EMA标准)
    """
    if a + b == 0 or c + d_val == 0:
        return {"method": "PRR", "error": "分母为0"}

    p_target = a / (a + b)
    p_other = c / (c + d_val)
    prr = p_target / p_other if p_other > 0 else float('inf')

    # PRR 95% CI
    se_log_prr = math.sqrt(1/a - 1/(a+b) + 1/c - 1/(c+d_val)) if a > 0 and c > 0 else 0
    ci_lower = math.exp(math.log(prr) - 1.96 * se_log_prr) if prr > 0 and se_log_prr > 0 else 0
    ci_upper = math.exp(math.log(prr) + 1.96 * se_log_prr) if prr > 0 and se_log_prr > 0 else float('inf')

    # Chi-square (Yates校正)
    expected = (a + b) * (a + c) / (a + b + c + d_val) if (a + b + c + d_val) > 0 else 0
    chi_sq = 0
    if expected > 0:
        o_e = abs(a - expected) - 0.5  # Yates continuity correction
        chi_sq = o_e ** 2 / expected

    # 信号判定
    signal = prr >= 2 and chi_sq >= 4

    return {
        "method": "PRR",
        "prr": round(prr, 2),
        "ci_95": [round(ci_lower, 4), round(ci_upper, 4)],
        "chi_square": round(chi_sq, 2),
        "thresholds": {"PRR": 2, "chi_sq": 4, "min_a": 3},
        "signal": signal,
        "interpretation": (
            f"PRR={prr:.2f}{'≥2' if prr >= 2 else '<2'}, "
            f"χ²={chi_sq:.2f}{'≥4' if chi_sq >= 4 else '<4'} → "
            f"{'🔴 信号' if signal else '无信号'}"
        ),
    }


def _calc_ror(a, b, c, d_val, drug, ae):
    """ROR (Reporting Odds Ratio) 计算。

    ROR = (a/c) / (b/d) = ad / bc

    信号阈值: ROR 95% CI lower > 1, a ≥ 3
    """
    if b == 0 or c == 0:
        return {"method": "ROR", "error": "b或c为0，无法计算ROR"}

    ror = (a * d_val) / (b * c)

    # ROR 95% CI
    se_log_ror = math.sqrt(1/a + 1/b + 1/c + 1/d_val) if a > 0 and d_val > 0 else 0
    ci_lower = math.exp(math.log(ror) - 1.96 * se_log_ror) if ror > 0 and se_log_ror > 0 else 0
    ci_upper = math.exp(math.log(ror) + 1.96 * se_log_ror) if ror > 0 and se_log_ror > 0 else float('inf')

    signal = ci_lower > 1

    return {
        "method": "ROR",
        "ror": round(ror, 2),
        "ci_95": [round(ci_lower, 4), round(ci_upper, 4)],
        "thresholds": {"ci_lower": 1, "min_a": 3},
        "signal": signal,
        "interpretation": (
            f"ROR={ror:.2f}, 95%CI=[{ci_lower:.2f}, {ci_upper:.2f}] → "
            f"{'🔴 信号(CI>1)' if signal else '无信号(CI包含1)'}"
        ),
    }


def _calc_bcpnn(a, b, c, d_val, total, alpha_prior, drug, ae):
    """BCPNN (Bayesian Confidence Propagation Neural Network) 计算。

    IC (Information Component):
    IC = log₂[P(AE|Drug) / P(AE)]

    信号阈值: IC_025 > 0 (即 95% CI 下限 > 0)

    使用 Beta-Binomial 先验: Beta(α₁, α₂)
    先验参数通常设为: α₁ = α_prior, α₂ ≈ 1
    """
    if total == 0:
        return {"method": "BCPNN", "error": "总数为0"}

    # 先验超参数
    alpha1 = alpha_prior
    alpha2 = 1.0
    beta1 = alpha_prior
    beta2 = 1.0

    # 后验期望 (Gamma)
    gamma11 = (a + alpha1) / (total + alpha1 + alpha2)
    gamma1_ = (a + b + alpha1) / (total + alpha1 + alpha2)  # P(drug)
    gamma_1 = (a + c + beta1) / (total + beta1 + beta2)    # P(ae)

    # IC = log₂(P_obs / P_expected)
    p_obs = gamma11
    p_exp = gamma1_ * gamma_1
    ic = math.log2(p_obs / p_exp) if p_obs > 0 and p_exp > 0 else 0

    # IC 方差 (Delta method)
    var_ic = 0
    if a > 0 and total > 0:
        var_ic = (1 / math.log(2) ** 2) * (
            (total - a + alpha2 - alpha1) / ((a + alpha1) * (total + alpha1 + alpha2 + 1))
            + (total - (a + b) + alpha2) / ((a + b + alpha1) * (total + alpha1 + alpha2 + 1))
            + (total - (a + c) + beta2) / ((a + c + beta1) * (total + beta1 + beta2 + 1))
        )

    ic_std = math.sqrt(abs(var_ic))

    # 95% CI
    ic_025 = ic - 1.96 * ic_std
    ic_975 = ic + 1.96 * ic_std

    signal = ic_025 > 0

    return {
        "method": "BCPNN",
        "IC": round(ic, 4),
        "IC_025": round(ic_025, 4),
        "IC_975": round(ic_975, 4),
        "E_IC": round(ic, 4),
        "SD_IC": round(ic_std, 4),
        "thresholds": {"IC_025": 0},
        "signal": signal,
        "interpretation": (
            f"IC={ic:.4f}, IC_025={ic_025:.4f} → "
            f"{'🔴 信号(IC_025>0)' if signal else '无信号(IC_025≤0)'}"
        ),
    }


def detect_signals_batch(reports: str, method: str = "all") -> dict:
    """批量信号检测 — 对多个药物-AE组合同时检测。

    Args:
        reports: JSON数组，每项为2×2列联表
            [{"target_drug":"疫苗X","target_ae":"发热","a":50,...}, ...]
        method: "prr"/"ror"/"bcpnn"/"all"

    Returns:
        {"results": [...], "signals_found": N}
    """
    try:
        data = json.loads(reports) if isinstance(reports, str) else reports
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"数据解析失败: {e}"}

    if not isinstance(data, list):
        return {"error": "reports 需为数组格式"}

    results = []
    signals_found = 0

    for report in data:
        result = detect_signal(json.dumps(report, ensure_ascii=False), method)
        results.append(result)
        if any(s.get("signal") for s in result.get("signals", [])):
            signals_found += 1

    return {
        "total_drug_ae_pairs": len(data),
        "signals_found": signals_found,
        "signal_rate": round(signals_found / len(data) * 100, 1) if data else 0,
        "results": results,
    }