"""报告生成 — Jinja2 模板渲染 Markdown 报告

为 vaxport 提供 5 种 GMP 合规报告:
- apqr: 年度产品质量回顾
- batch_record: 单批生产批记录摘要
- deviation_report: 偏差调查报告
- lot_release: 批签发申报资料
- monthly_quality: 月度质量报告
"""

import json
import os
from datetime import datetime
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "report_templates"

# 预加载模板（首次调用时编译）
_templates: dict = {}


def _get_template(name: str):
    """懒加载 Jinja2 模板"""
    if name not in _templates:
        try:
            from jinja2 import Environment, BaseLoader, TemplateNotFound
        except ImportError:
            return None

        template_file = TEMPLATE_DIR / f"{name}.md.j2"
        if not template_file.exists():
            return None

        env = Environment(loader=BaseLoader())
        with open(template_file) as f:
            _templates[name] = env.from_string(f.read())
    return _templates[name]


def generate_report(report_type: str, context: str, params: str = "{}") -> dict:
    """报告生成入口。

    Args:
        report_type: 报告类型 (apqr/batch_record/deviation_report/lot_release/monthly_quality)
        context: 报告数据上下文，JSON 格式字符串
        params: 额外参数，JSON 格式字符串

    Returns:
        dict: {"report": "Markdown 文本", "report_type": "...", "generated_at": "..."}
    """
    try:
        ctx = json.loads(context) if isinstance(context, str) else context
    except json.JSONDecodeError:
        return {"error": f"context JSON 解析失败: {context[:100]}"}

    try:
        prm = json.loads(params) if isinstance(params, str) else params
    except json.JSONDecodeError:
        return {"error": f"params JSON 解析失败: {params[:100]}"}

    valid_types = {"apqr", "batch_record", "deviation_report", "lot_release", "monthly_quality"}
    if report_type not in valid_types:
        return {"error": f"未知报告类型: {report_type}，支持: {', '.join(sorted(valid_types))}"}

    # 合并 context 和 params
    template_vars = {**ctx, **prm}
    template_vars["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    template = _get_template(report_type)
    if template is None:
        # 无 Jinja2 或模板不存在时，用内置 fallback
        fallback = _build_fallback(report_type, template_vars)
        return {
            "report": fallback,
            "report_type": report_type,
            "generated_at": template_vars["generated_at"],
            "note": "使用内置模板（安装 jinja2 并添加模板文件以获得更好的格式）",
        }

    try:
        rendered = template.render(**template_vars)
        return {
            "report": rendered,
            "report_type": report_type,
            "generated_at": template_vars["generated_at"],
        }
    except Exception as e:
        return {"error": f"模板渲染失败: {type(e).__name__}: {e}"}


# ── Fallback 模板（无 Jinja2 时使用）───────────────────────

def _build_fallback(report_type: str, ctx: dict) -> str:
    """内置纯 Python 模板（不依赖 Jinja2）"""
    builders = {
        "apqr": _apqr_fallback,
        "batch_record": _batch_record_fallback,
        "deviation_report": _deviation_fallback,
        "lot_release": _lot_release_fallback,
        "monthly_quality": _monthly_fallback,
    }
    return builders.get(report_type, lambda c: f"# {report_type}\n\n未知报告类型")(ctx)


def _apqr_fallback(ctx: dict) -> str:
    product = ctx.get("product_name", "未指定产品")
    period = ctx.get("report_period", "未指定报告期")
    batches = ctx.get("batches", [])
    total = len(batches)
    released = ctx.get("released_count", sum(1 for b in batches if b.get("release_decision") == "released"))
    pass_rate = round(released / total * 100, 1) if total > 0 else 0

    lines = [
        f"# 年度产品质量回顾报告 (APQR)",
        "",
        f"## 产品: {product}",
        f"## 报告期: {period}",
        f"## 生成时间: {ctx.get('generated_at', '')}",
        "",
        "---",
        "",
        "## 1. 生产概述",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总批次 | {total} |",
        f"| 放行批次 | {released} |",
        f"| 合格率 | {pass_rate}% |",
    ]

    # 批次列表
    if batches:
        lines.extend(["", "### 批次明细", ""])
        lines.append("| 批号 | 生产日期 | 规模 | 放行决定 |")
        lines.append("|------|---------|------|---------|")
        for b in batches[:30]:
            bid = b.get("batch_id", "-")
            date = b.get("start_date", "-")
            scale = b.get("bioreactor_scale_l", "-")
            decision = b.get("release_decision", b.get("status", "-"))
            lines.append(f"| {bid} | {date} | {scale}L | {decision} |")

    # 关键质量属性
    metrics = ctx.get("metrics", [])
    if metrics:
        lines.extend(["", "---", "", "## 2. 关键质量属性趋势", ""])
        lines.append("| 指标 | 均值 | 标准差 | Cpk | 判定 |")
        lines.append("|------|------|--------|-----|------|")
        for m in metrics:
            lines.append(f"| {m.get('name', '-')} | {m.get('mean', '-')} | {m.get('std', '-')} | {m.get('cpk', '-')} | {m.get('judgment', '-')} |")

    # 偏差/OOS 汇总
    deviations = ctx.get("deviations", [])
    oos_count = ctx.get("oos_count", 0)
    capa_open = ctx.get("capa_open_count", 0)
    lines.extend(["", "---", "", "## 3. 偏差与 OOS 汇总", ""])
    lines.append(f"| 类别 | 数量 |")
    lines.append(f"|------|------|")
    lines.append(f"| 偏差总数 | {len(deviations)} |")
    lines.append(f"| OOS 次数 | {oos_count} |")
    lines.append(f"| 未关闭 CAPA | {capa_open} |")

    if deviations:
        lines.extend(["", "### 偏差列表", ""])
        for d in deviations[:20]:
            lines.append(f"- **{d.get('id', '-')}**: {d.get('description', '')} [{d.get('status', '')}]")

    # 结论与建议
    lines.extend(["", "---", "", "## 4. 结论与建议", ""])
    conclusions = ctx.get("conclusions", [])
    if conclusions:
        for c in conclusions:
            lines.append(f"- {c}")
    else:
        lines.append("- 本报告期产品质量稳定，过程受控。")
        if oos_count > 0:
            lines.append(f"- 共有 {oos_count} 次 OOS，均已调查并关闭。")

    lines.extend(["", "---", "", f"*报告由 vaxport 自动生成，需经 QA 审核签字后生效。*"])
    return "\n".join(lines)


def _batch_record_fallback(ctx: dict) -> str:
    batch_id = ctx.get("batch_id", "未指定")
    product = ctx.get("product_name", "PEDV 灭活疫苗")

    lines = [
        f"# 批生产记录摘要",
        "",
        f"## 批次: {batch_id}",
        f"## 产品: {product}",
        f"## 生成时间: {ctx.get('generated_at', '')}",
        "",
        "---",
        "",
        "## 1. 批次基础信息",
    ]

    info_fields = [
        ("生产规模", f"{ctx.get('bioreactor_scale_l', '-')} L"),
        ("MOI", str(ctx.get("moi", "-"))),
        ("生产启动日期", str(ctx.get("start_date", "-"))),
        ("实际收获日期", str(ctx.get("actual_harvest_date", "-"))),
        ("状态", ctx.get("status", "-")),
        ("操作班组", ctx.get("operator_team", "-")),
        ("种细胞", ctx.get("cell_seed_name", "-")),
        ("种病毒", ctx.get("virus_seed_name", "-")),
        ("生长培养基", ctx.get("growth_medium_name", "-")),
        ("维持培养基", ctx.get("maintenance_medium_name", "-")),
    ]
    for label, value in info_fields:
        lines.append(f"- **{label}**: {value}")

    # 细胞培养摘要
    cell_log = ctx.get("cell_culture_summary", {})
    if cell_log:
        lines.extend(["", "## 2. 细胞培养", ""])
        lines.append(f"- 峰值密度: {cell_log.get('peak_density', '-')} ×10⁶/mL")
        lines.append(f"- 培养天数: {cell_log.get('culture_days', '-')}")
        lines.append(f"- 最低活力: {cell_log.get('min_viability', '-')}%")

    # 病毒培养摘要
    virus_log = ctx.get("virus_culture_summary", {})
    if virus_log:
        lines.extend(["", "## 3. 病毒培养", ""])
        lines.append(f"- 接种 DPI: {virus_log.get('max_dpi', '-')}")
        lines.append(f"- 峰值 CPE: {virus_log.get('peak_cpe', '-')}%")
        lines.append(f"- 收获时效价: {virus_log.get('harvest_titer', '-')} log10 TCID50/mL")

    # 灭活
    inactivation = ctx.get("inactivation", {})
    if inactivation:
        lines.extend(["", "## 4. 收获与灭活", ""])
        lines.append(f"- 收获体积: {inactivation.get('harvest_volume_l', '-')} L")
        lines.append(f"- 灭活剂: {inactivation.get('inactivant', '-')}")
        lines.append(f"- 灭活浓度: {inactivation.get('inactivant_conc_mm', '-')} mM")
        lines.append(f"- 灭活时长: {inactivation.get('inactivation_duration_h', '-')} h")
        lines.append(f"- 灭活验证: {inactivation.get('residual_infectivity_test', '-')}")

    # QC 结果
    qc = ctx.get("qc_result", {})
    if qc:
        lines.extend(["", "## 5. 成品检验", ""])
        qc_fields = [
            ("外观", qc.get("appearance", "-")),
            ("pH", qc.get("ph", "-")),
            ("无菌检查", qc.get("sterility_test", "-")),
            ("内毒素", f"{qc.get('endotoxin_eu_per_dose', '-')} EU/dose"),
            ("效价 (ELISA)", f"{qc.get('potency_elisa', '-')} U"),
            ("安全试验 (小鼠)", qc.get("safety_test_mice", "-")),
            ("安全试验 (仔猪)", qc.get("safety_test_piglets", "-")),
            ("攻毒保护", qc.get("efficacy_challenge", "-")),
            ("放行决定", qc.get("release_decision", "-")),
        ]
        for label, value in qc_fields:
            lines.append(f"- **{label}**: {value}")

    lines.extend(["", "---", "", f"*报告由 vaxport 自动生成。*"])
    return "\n".join(lines)


def _deviation_fallback(ctx: dict) -> str:
    dev_id = ctx.get("deviation_id", "未指定")
    lines = [
        f"# 偏差调查报告",
        "",
        f"## 偏差编号: {dev_id}",
        f"## 生成时间: {ctx.get('generated_at', '')}",
        "",
        "---",
        "",
        "## 1. 事件描述",
        "",
        ctx.get("description", "（待补充）"),
        "",
        "## 2. 受影响批次",
    ]

    batches = ctx.get("affected_batches", [])
    if batches:
        for b in batches:
            lines.append(f"- {b}")
    else:
        lines.append("- （待补充）")

    lines.extend(["", "## 3. 调查过程", "", ctx.get("investigation", "（待补充）")])
    lines.extend(["", "## 4. 根因分析", "", ctx.get("root_cause", "（待补充）")])
    lines.extend(["", "## 5. CAPA 措施", ""])

    capa = ctx.get("capa_actions", [])
    if capa:
        for i, c in enumerate(capa, 1):
            lines.append(f"{i}. {c}")
    else:
        lines.append("- （待补充）")

    lines.extend(["", "## 6. 产品处置", "", ctx.get("disposition", "（待补充）")])
    lines.extend(["", "---", "", f"*报告由 vaxport 辅助生成，需经 QA 审核签字。*"])
    return "\n".join(lines)


def _lot_release_fallback(ctx: dict) -> str:
    batch_id = ctx.get("batch_id", "未指定")
    product = ctx.get("product_name", "PEDV 灭活疫苗")

    lines = [
        f"# 批签发申报资料",
        "",
        f"## 产品: {product}",
        f"## 批号: {batch_id}",
        f"## 生成时间: {ctx.get('generated_at', '')}",
        "",
        "---",
        "",
        "## 1. 制造摘要",
        "",
        f"- 生产日期: {ctx.get('start_date', '-')} 至 {ctx.get('actual_harvest_date', '-')}",
        f"- 生产规模: {ctx.get('bioreactor_scale_l', '-')} L",
        f"- 半成品批号: {ctx.get('semi_id', '-')}",
        f"- 分装数量: {ctx.get('fill_count', '-')} 支",
    ]

    # 检验结果
    qc = ctx.get("qc_result", {})
    if qc:
        lines.extend(["", "## 2. 检验结果", ""])
        lines.append("| 检验项目 | 结果 | 标准 | 判定 |")
        lines.append("|---------|------|------|------|")
        checks = [
            ("外观", qc.get("appearance", "-"), "应为微乳白色液体", qc.get("appearance", "-")),
            ("pH", qc.get("ph", "-"), "6.5-7.5", "合格"),
            ("无菌检查", qc.get("sterility_test", "-"), "阴性", qc.get("sterility_test", "-")),
            ("内毒素", f"{qc.get('endotoxin_eu_per_dose', '-')} EU/dose", "≤10 EU/dose", "合格" if float(qc.get("endotoxin_eu_per_dose", 999)) <= 10 else "不合格"),
            ("效价 (ELISA)", f"{qc.get('potency_elisa', '-')} U", "≥32 U", "合格" if float(qc.get("potency_elisa", 0)) >= 32 else "不合格"),
            ("安全试验", qc.get("safety_test_mice", "-"), "小鼠存活", qc.get("safety_test_mice", "-")),
            ("攻毒保护", qc.get("efficacy_challenge", "-"), "保护", qc.get("efficacy_challenge", "-")),
        ]
        for name, result, spec, judgment in checks:
            lines.append(f"| {name} | {result} | {spec} | {judgment} |")

    lines.extend(["", "## 3. 资料清单", ""])
    items = ctx.get("documents", ["制造记录摘要", "检验报告书", "过程控制记录", "标签样张"])
    for i, doc in enumerate(items, 1):
        lines.append(f"{i}. {doc}")

    completeness = ctx.get("completeness", {})
    if completeness:
        lines.extend(["", "### 完整性检查", ""])
        for doc, status in completeness.items():
            icon = "✅" if status else "❌"
            lines.append(f"- {icon} {doc}")

    lines.extend(["", "---", "", f"*申报资料由 vaxport 辅助生成。*"])
    return "\n".join(lines)


def _monthly_fallback(ctx: dict) -> str:
    month = ctx.get("month", datetime.now().strftime("%Y-%m"))
    product = ctx.get("product_name", "PEDV 灭活疫苗")

    lines = [
        f"# 月度质量报告",
        "",
        f"## 产品: {product}",
        f"## 报告月份: {month}",
        f"## 生成时间: {ctx.get('generated_at', '')}",
        "",
        "---",
        "",
        "## 1. 生产批次统计",
    ]

    batches = ctx.get("batches", [])
    total = len(batches)
    released = ctx.get("released_count", sum(1 for b in batches if b.get("release_decision") == "released"))

    lines.extend([
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 本月生产批次 | {total} |",
        f"| 放行批次 | {released} |",
        f"| 检验中批次 | {ctx.get('testing_count', total - released)} |",
    ])

    # KPI
    lines.extend(["", "## 2. 质量 KPI", ""])
    kpi = ctx.get("kpi", {})
    lines.append("| KPI | 目标 | 实际 | 状态 |")
    lines.append("|-----|------|------|------|")
    kpi_items = [
        ("批次合格率", "≥95%", f"{kpi.get('pass_rate', '-')}%", "✅" if kpi.get("pass_rate", 100) >= 95 else "⚠️"),
        ("OOS 次数", "0", str(kpi.get("oos_count", 0)), "✅" if kpi.get("oos_count", 0) == 0 else "⚠️"),
        ("CAPA 按时关闭率", "≥90%", f"{kpi.get('capa_closure_rate', '-')}%", "✅" if kpi.get("capa_closure_rate", 100) >= 90 else "⚠️"),
        ("偏差关闭率", "≥90%", f"{kpi.get('deviation_closure_rate', '-')}%", "✅" if kpi.get("deviation_closure_rate", 100) >= 90 else "⚠️"),
    ]
    for name, target, actual, status in kpi_items:
        lines.append(f"| {name} | {target} | {actual} | {status} |")

    # 偏差
    deviations = ctx.get("deviations", [])
    lines.extend(["", "## 3. 偏差与 CAPA", ""])
    if deviations:
        for d in deviations:
            lines.append(f"- **{d.get('id', '-')}**: {d.get('description', '')} (状态: {d.get('status', '')})")
    else:
        lines.append("- 本月无偏差记录")

    lines.extend(["", "---", "", f"*报告由 vaxport 自动生成。*"])
    return "\n".join(lines)