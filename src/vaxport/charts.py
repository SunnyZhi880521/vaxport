"""图表生成 — matplotlib (Agg backend)

为报告生成 Agent 提供 5 种图表类型:
- trend: 趋势折线图
- control: 控制图 (X-bar)
- pareto: 帕累托图
- heatmap: 热力图
- comparison: 分组对比柱状图

输出: PNG 保存到 ~/.vaxport/charts/ 目录，返回文件路径供 Markdown 引用。
"""

import json
import math
import time
from pathlib import Path
from statistics import mean, stdev

import matplotlib
matplotlib.use("Agg")  # 非 GUI 后端
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import matplotlib.font_manager as fm


def _get_cjk_fonts() -> list[str]:
    """检测系统可用的中文字体"""
    cjk_candidates = []
    for f in fm.fontManager.ttflist:
        name_lower = f.name.lower()
        if any(kw in name_lower for kw in
               ["hei", "song", "ming", "kai", "fang", "yuan",
                "cjk", "pingfang", "stheit", "heiti"]):
            cjk_candidates.append(f.name)

    seen = set()
    result = []
    for name in cjk_candidates:
        if name not in seen:
            result.append(name)
            seen.add(name)

    preferred = ["PingFang SC", "Heiti SC", "STHeiti", "Arial Unicode MS"]
    for p in reversed(preferred):
        if p in seen:
            result.remove(p)
            result.insert(0, p)

    result.append("DejaVu Sans")
    return result


# 中文字体配置
try:
    matplotlib.rcParams["font.sans-serif"] = _get_cjk_fonts()
    matplotlib.rcParams["axes.unicode_minus"] = False
except Exception:
    pass


def _chart_dir() -> Path:
    """获取图表保存目录，自动创建"""
    p = Path.home() / ".vaxport" / "charts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _validate_chart_data(chart_type: str, data: dict) -> str | None:
    """校验图表核心数据是否为空。返回错误信息或 None（通过）。"""
    if chart_type == "trend":
        y = data.get("y") or data.get("values") or []
        if not y:
            return "trend 图缺少数据: 请提供 {\"x\": [...], \"y\": [...]} 格式，y 不能为空"
    elif chart_type == "control":
        values = data.get("values") or []
        if not values:
            return "control 图缺少数据: 请提供 {\"values\": [...]} 格式，values 不能为空"
    elif chart_type == "pareto":
        cats = data.get("categories") or []
        vals = data.get("values") or []
        if not cats or not vals:
            return "pareto 图缺少数据: 请提供 {\"categories\": [...], \"values\": [...]} 格式"
    elif chart_type == "heatmap":
        matrix = data.get("matrix") or data.get("data") or data.get("values") or []
        if not matrix or (isinstance(matrix, list) and len(matrix) == 0):
            return "heatmap 图缺少数据: 请提供 {\"matrix\": [[...], [...]], \"xlabels\": [...], \"ylabels\": [...]} 格式，matrix 必须是非空二维数组"
        if isinstance(matrix, list) and all(len(r) == 0 for r in matrix):
            return "heatmap 图缺少数据: matrix 的每行都是空数组，请填充实际数值（如 [[0.9, 0.5], [0.3, 0.8]]）"
    elif chart_type == "comparison":
        groups = data.get("groups") or data.get("categories") or data.get("data") or {}
        if not groups:
            return "comparison 图缺少数据: 请提供 {\"groups\": {\"组名\": [...]}} 格式，groups 不能为空字典"
        has_non_empty = False
        for k, v in groups.items():
            if isinstance(v, list) and len(v) > 0:
                has_non_empty = True
                break
        if not has_non_empty:
            return "comparison 图缺少数据: 所有组的 values 都是空数组，请填充实际数值"
    return None


def generate_chart(data: str, chart_type: str, options: str = "{}") -> dict:
    """图表生成入口。

    Args:
        data: 图表数据 JSON
            trend: {"x": [...], "y": [...], "xlabel": "批次", "ylabel": "效价"}
            control: {"values": [...], "xlabel": "批次", "ylabel": "测定值"}
            pareto: {"categories": [...], "values": [...], "ylabel": "频次"}
            heatmap: {"matrix": [[...]], "xlabels": [...], "ylabels": [...]}
            comparison: {"groups": {"组1": [...], "组2": [...]}, "ylabel": "效价", "bar_labels": ["指标A","指标B"]}
        chart_type: "trend" / "control" / "pareto" / "heatmap" / "comparison"
        options: {"title": "图表标题", "width": 10, "height": 6, "dpi": 100}

    Returns:
        {"chart_type": str, "file_path": str, "format": "png"}
    """
    try:
        parsed = json.loads(data) if isinstance(data, str) else data
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"数据解析失败: {e}"}

    try:
        opts = json.loads(options) if isinstance(options, str) else options
    except (json.JSONDecodeError, TypeError):
        opts = {}

    title = opts.get("title", "")
    width = opts.get("width", 10)
    height = opts.get("height", 6)
    dpi = opts.get("dpi", 100)

    # 预校验：核心数据为空时直接返回错误，不生成空白图
    empty_err = _validate_chart_data(chart_type, parsed)
    if empty_err:
        return {"error": empty_err}

    fig, ax = plt.subplots(figsize=(width, height))

    try:
        if chart_type == "trend":
            _draw_trend(ax, parsed, opts)
        elif chart_type == "control":
            _draw_control(ax, parsed, opts)
        elif chart_type == "pareto":
            _draw_pareto(ax, parsed, opts)
        elif chart_type == "heatmap":
            _draw_heatmap(ax, parsed, opts)
        elif chart_type == "comparison":
            _draw_comparison(ax, parsed, opts)
        else:
            plt.close(fig)
            return {"error": f"未知图表类型: {chart_type}，支持: trend/control/pareto/heatmap/comparison"}
    except Exception as e:
        plt.close(fig)
        return {"error": f"图表渲染失败: {e}"}

    if title:
        ax.set_title(title, fontsize=14, fontweight="bold")

    fig.tight_layout()

    # 保存 PNG 到磁盘
    ts = int(time.time() * 1000)
    fname = f"chart_{chart_type}_{ts}.png"
    filepath = _chart_dir() / fname
    fig.savefig(str(filepath), format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)

    return {
        "chart_type": chart_type,
        "file_path": str(filepath),
        "format": "png",
        "title": title,
    }


def _draw_trend(ax, data: dict, opts: dict):
    """趋势折线图"""
    x = data.get("x", list(range(len(data.get("y", [])))))
    y = data.get("y", [])
    xlabel = opts.get("xlabel", data.get("xlabel", "批次"))
    ylabel = opts.get("ylabel", data.get("ylabel", "数值"))

    ax.plot(x, y, marker="o", linewidth=2, markersize=6, color="#2c7fb8")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)

    # 趋势线
    if len(y) > 2:
        n = len(y)
        x_num = list(range(n))
        x_mean = mean(x_num)
        y_mean = mean(y)
        ss_xy = sum((i - x_mean) * (y[i] - y_mean) for i in range(n))
        ss_xx = sum((i - x_mean) ** 2 for i in range(n))
        slope = ss_xy / ss_xx if ss_xx > 0 else 0
        intercept = y_mean - slope * x_mean
        trend_y = [slope * i + intercept for i in range(n)]
        ax.plot(x, trend_y, linestyle="--", linewidth=1.5,
                color="#e6550d", alpha=0.7, label=f"趋势线 (斜率={slope:.4f})")
        ax.legend(fontsize=9)


def _draw_control(ax, data: dict, opts: dict):
    """控制图 (X-bar)"""
    values = data.get("values", [])
    xlabel = opts.get("xlabel", data.get("xlabel", "批次"))
    ylabel = opts.get("ylabel", data.get("ylabel", "测定值"))

    n = len(values)
    x = list(range(1, n + 1))
    mu = mean(values) if values else 0
    sigma = stdev(values) if n > 1 else 0

    ucl = mu + 3 * sigma if sigma > 0 else mu
    lcl = mu - 3 * sigma if sigma > 0 else mu
    ucl_2s = mu + 2 * sigma if sigma > 0 else mu
    lcl_2s = mu - 2 * sigma if sigma > 0 else mu

    ax.plot(x, values, marker="o", linewidth=2, markersize=6, color="#2c7fb8")
    ax.axhline(y=mu, color="#31a354", linestyle="-", linewidth=1.5, label=f"CL={mu:.3f}")
    ax.axhline(y=ucl, color="#e6550d", linestyle="--", linewidth=1.2, label=f"UCL(+3σ)={ucl:.3f}")
    ax.axhline(y=lcl, color="#e6550d", linestyle="--", linewidth=1.2, label=f"LCL(-3σ)={lcl:.3f}")
    ax.axhline(y=ucl_2s, color="#fdae6b", linestyle=":", linewidth=1, alpha=0.5)
    ax.axhline(y=lcl_2s, color="#fdae6b", linestyle=":", linewidth=1, alpha=0.5)

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8, loc="upper right")

    # 标记违规点
    for i, v in enumerate(values):
        if v > ucl or v < lcl:
            ax.annotate(f"Rule 1", (x[i], v),
                        textcoords="offset points", xytext=(0, 12),
                        fontsize=8, color="#e6550d", ha="center")


def _draw_pareto(ax, data: dict, opts: dict):
    """帕累托图"""
    categories = data.get("categories", [])
    values = data.get("values", [])
    ylabel = opts.get("ylabel", data.get("ylabel", "频次"))

    if not categories or not values:
        ax.text(0.5, 0.5, "无数据", ha="center", va="center", transform=ax.transAxes)
        return

    # 降序排列
    sorted_pairs = sorted(zip(values, categories), reverse=True)
    values_sorted = [p[0] for p in sorted_pairs]
    cats_sorted = [p[1] for p in sorted_pairs]

    total = sum(values_sorted)
    cum_pct = []
    cum = 0
    for v in values_sorted:
        cum += v
        cum_pct.append(cum / total * 100)

    x_pos = range(len(cats_sorted))
    ax.bar(x_pos, values_sorted, color="#2c7fb8", alpha=0.8, label=ylabel)

    ax2 = ax.twinx()
    ax2.plot(x_pos, cum_pct, "o-", linewidth=2, markersize=5, color="#e6550d")
    ax2.set_ylabel("累计百分比 (%)", color="#e6550d")
    ax2.set_ylim(0, 105)
    ax2.axhline(y=80, color="#e6550d", linestyle="--", alpha=0.5, linewidth=1)

    ax.set_xticks(list(x_pos))
    ax.set_xticklabels(cats_sorted, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.2, axis="y")


def _draw_heatmap(ax, data: dict, opts: dict):
    """热力图"""
    # 容错: 接受 matrix/data/values, xlabels/columns/cols, ylabels/rows
    matrix = data.get("matrix") or data.get("data") or data.get("values") or []
    xlabels = data.get("xlabels") or data.get("columns") or data.get("cols") or []
    ylabels = data.get("ylabels") or data.get("rows") or []

    if not matrix:
        ax.text(0.5, 0.5, "无数据", ha="center", va="center", transform=ax.transAxes)
        return

    # 确保 matrix 是二维列表
    if isinstance(matrix, list) and matrix and not isinstance(matrix[0], list):
        matrix = [matrix]

    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")

    # 标注数值
    max_val = max(max(r) for r in matrix) if matrix else 1
    for i in range(len(matrix)):
        for j in range(len(matrix[i])):
            val = matrix[i][j]
            text_color = "white" if isinstance(val, (int, float)) and val > (max_val * 0.7) else "black"
            ax.text(j, i, f"{val:.2f}" if isinstance(val, float) else str(val),
                    ha="center", va="center", fontsize=8, color=text_color)

    if xlabels:
        ax.set_xticks(range(min(len(xlabels), len(matrix[0]) if matrix else 0)))
        ax.set_xticklabels(xlabels[:len(matrix[0]) if matrix else 0], rotation=45, ha="right", fontsize=8)
    if ylabels:
        ax.set_yticks(range(min(len(ylabels), len(matrix))))
        ax.set_yticklabels(ylabels[:len(matrix)], fontsize=8)

    plt.colorbar(im, ax=ax, shrink=0.8)


def _draw_comparison(ax, data: dict, opts: dict):
    """分组对比柱状图"""
    # 容错: 接受 groups/categories/data
    groups = data.get("groups") or data.get("categories") or data.get("data") or {}
    bar_labels = data.get("bar_labels") or []  # 每个柱子代表的指标名称（用于图例）
    ylabel = opts.get("ylabel", data.get("ylabel", "数值"))

    if not groups:
        ax.text(0.5, 0.5, "无数据", ha="center", va="center", transform=ax.transAxes)
        return

    group_names = list(groups.keys())
    n_groups = len(group_names)
    max_bars = max(len(v) for v in groups.values())

    colors = ["#2c7fb8", "#e6550d", "#31a354", "#756bb1", "#fdae6b",
              "#d62728", "#8c564b", "#9467bd", "#7f7f7f", "#bcbd22"]
    bar_width = 0.8 / max(1, max_bars)

    if bar_labels and len(bar_labels) >= max_bars:
        # 有 bar_labels → 按指标着色，图例显示指标名（不同 group 的同指标同色）
        for gi, (gname, vals) in enumerate(groups.items()):
            for j, val in enumerate(vals):
                x_pos = gi + j * bar_width
                label = bar_labels[j] if gi == 0 and j < len(bar_labels) else None
                ax.bar(x_pos, val, bar_width * 0.85,
                       color=colors[j % len(colors)], alpha=0.85,
                       label=label)
        ax.legend(fontsize=8)
    else:
        # 无 bar_labels → 按 group 着色，图例显示 group 名（旧行为）
        for gi, (gname, vals) in enumerate(groups.items()):
            x_pos = [gi + j * bar_width for j in range(len(vals))]
            ax.bar(x_pos, vals, bar_width * 0.85,
                   color=colors[gi % len(colors)], alpha=0.85, label=gname)
        ax.legend(fontsize=8)

    ax.set_xticks([i + bar_width * (max_bars - 1) / 2
                   for i in range(n_groups)])
    ax.set_xticklabels(group_names, fontsize=9)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.2, axis="y")