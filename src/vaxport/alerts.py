"""阈值监控与预警 — 实时查询数据库生成分级预警

为 vaxport 提供 6 种预警类型:
- expiry: 效期预警（物料/试剂/培养基/冻存管/细胞库）
- calibration: 仪器校准到期预警
- training: 人员资质/培训到期预警
- threshold: 工艺参数/OOS/环境超限预警
- trend: 趋势预警（连续 N 点在中心线同侧）
- summary: 汇总所有活跃预警

Phase 1: 基于 analog_pedv schema 的现有表实现 threshold/expiry/summary。
calibration/training 依赖不存在的数据表，返回"未就绪"提示。
"""

import json
from datetime import date, datetime, timedelta
from typing import Optional

from vaxport.db import Database


def check_alerts(db: Database, alert_type: str, filters: str = "{}") -> dict:
    """预警查询入口。

    Args:
        db: 数据库连接实例
        alert_type: 预警类型 (expiry/calibration/training/threshold/trend/summary)
        filters: JSON 格式过滤条件

    Returns:
        dict: 预警结果，含 alerts 列表和 severity 汇总
    """
    try:
        opts = json.loads(filters) if isinstance(filters, str) else filters
    except json.JSONDecodeError:
        return {"error": f"filters JSON 解析失败: {filters[:100]}"}

    if not db or not db.is_connected:
        return {"error": "数据库未连接", "alerts": [], "summary": {}}

    handlers = {
        "expiry": _check_expiry,
        "calibration": _check_calibration,
        "training": _check_training,
        "threshold": _check_threshold,
        "trend": _check_trend,
        "summary": _check_summary,
    }

    handler = handlers.get(alert_type)
    if not handler:
        return {"error": f"未知预警类型: {alert_type}，支持: {', '.join(handlers)}"}

    try:
        result = handler(db, opts)
        # 添加 severity 汇总
        alerts = result.get("alerts", [])
        summary = {"red": 0, "yellow": 0, "blue": 0}
        for a in alerts:
            sev = a.get("severity", "blue")
            summary[sev] = summary.get(sev, 0) + 1
        result["summary"] = summary
        result["generated_at"] = datetime.now().isoformat()
        return result
    except Exception as e:
        return {"error": f"预警查询失败: {type(e).__name__}: {e}", "alerts": [], "summary": {}}


def _severity_by_days(days_left: int) -> str:
    """按剩余天数分级"""
    if days_left <= 7:
        return "red"
    elif days_left <= 30:
        return "yellow"
    return "blue"


def _table_exists(db: Database, schema: str, table: str) -> bool:
    """检查表是否存在"""
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                (schema, table),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


# ── expiry ─────────────────────────────────────────────────

def _check_expiry(db: Database, opts: dict) -> dict:
    """效期预警：检查各表中含日期/效期字段的数据"""
    alerts = []
    today = date.today()

    # 检查 analog_pedv schema 下的表
    schema = opts.get("schema", "analog_pedv")
    days_ahead = opts.get("days_ahead", 30)

    # 1. 种细胞库 — 检查建库日期 + 有效期（假设有效期 5 年）
    if _table_exists(db, schema, "cell_seeds"):
        try:
            with db.cursor() as cur:
                cur.execute(f"""
                    SELECT seed_id, seed_name, bank_type, date_established,
                           (date_established + INTERVAL '5 years')::date AS expiry_date
                    FROM {schema}.cell_seeds
                    WHERE (date_established + INTERVAL '5 years')::date <= %s
                    ORDER BY expiry_date
                """, (today + timedelta(days=days_ahead),))
                for row in cur.fetchall():
                    days_left = (row["expiry_date"] - today).days
                    alerts.append({
                        "type": "expiry",
                        "subtype": "cell_seed",
                        "severity": _severity_by_days(days_left),
                        "item": f"{row['seed_name']} ({row['seed_id']})",
                        "detail": f"建库日期 {row['date_established']}，有效期至 {row['expiry_date']}",
                        "due_date": str(row["expiry_date"]),
                        "days_left": days_left,
                    })
        except Exception:
            pass

    # 2. 种病毒库 — 同样假设 5 年有效期
    if _table_exists(db, schema, "virus_seeds"):
        try:
            with db.cursor() as cur:
                cur.execute(f"""
                    SELECT seed_id, seed_name, bank_type, date_established,
                           (date_established + INTERVAL '5 years')::date AS expiry_date
                    FROM {schema}.virus_seeds
                    WHERE (date_established + INTERVAL '5 years')::date <= %s
                    ORDER BY expiry_date
                """, (today + timedelta(days=days_ahead),))
                for row in cur.fetchall():
                    days_left = (row["expiry_date"] - today).days
                    alerts.append({
                        "type": "expiry",
                        "subtype": "virus_seed",
                        "severity": _severity_by_days(days_left),
                        "item": f"{row['seed_name']} ({row['seed_id']})",
                        "detail": f"建库日期 {row['date_established']}，有效期至 {row['expiry_date']}",
                        "due_date": str(row["expiry_date"]),
                        "days_left": days_left,
                    })
        except Exception:
            pass

    # 3. 培养基批次
    if _table_exists(db, schema, "culture_media"):
        try:
            with db.cursor() as cur:
                cur.execute(f"""
                    SELECT medium_id, medium_name, lot_number, supplier
                    FROM {schema}.culture_media
                    LIMIT 20
                """)
                for row in cur.fetchall():
                    # 培养基批次通过 lot_number 追溯，这里标记为信息级
                    alerts.append({
                        "type": "expiry",
                        "subtype": "culture_media",
                        "severity": "blue",
                        "item": f"{row['medium_name']} (Lot: {row['lot_number']})",
                        "detail": f"供应商: {row['supplier']}，请核对实际有效期",
                        "due_date": None,
                        "days_left": None,
                    })
        except Exception:
            pass

    if not alerts:
        alerts.append({
            "type": "expiry",
            "subtype": "info",
            "severity": "blue",
            "item": "未发现即将到期的项目",
            "detail": f"未来 {days_ahead} 天内无到期项",
            "due_date": None,
            "days_left": None,
        })

    return {"alerts": alerts}


# ── calibration ────────────────────────────────────────────

def _check_calibration(db: Database, opts: dict) -> dict:
    """仪器校准到期预警"""
    schema = opts.get("schema", "analog_pedv")

    # 检查是否存在校准相关表
    has_table = (
        _table_exists(db, schema, "equipment_calibration")
        or _table_exists(db, "public", "calibration")
    )

    if not has_table:
        return {
            "alerts": [{
                "type": "calibration",
                "subtype": "info",
                "severity": "blue",
                "item": "校准数据表未就绪",
                "detail": f"schema '{schema}' 中未找到 equipment_calibration 或 calibration 表。Phase 2-3 实施后将支持此预警。",
                "due_date": None,
                "days_left": None,
            }]
        }

    # 未来实现
    return {"alerts": [{"type": "calibration", "severity": "blue", "item": "校准到期查询功能待实现"}]}


# ── training ───────────────────────────────────────────────

def _check_training(db: Database, opts: dict) -> dict:
    """人员培训/资质到期预警"""
    schema = opts.get("schema", "analog_pedv")

    has_table = (
        _table_exists(db, schema, "training_records")
        or _table_exists(db, "public", "training")
    )

    if not has_table:
        return {
            "alerts": [{
                "type": "training",
                "subtype": "info",
                "severity": "blue",
                "item": "培训数据表未就绪",
                "detail": f"schema '{schema}' 中未找到 training_records 或 training 表。Phase 2-3 实施后将支持此预警。",
                "due_date": None,
                "days_left": None,
            }]
        }

    return {"alerts": [{"type": "training", "severity": "blue", "item": "培训到期查询功能待实现"}]}


# ── threshold ──────────────────────────────────────────────

def _check_threshold(db: Database, opts: dict) -> dict:
    """工艺参数/OOS/环境超限预警"""
    alerts = []
    schema = opts.get("schema", "analog_pedv")

    # 1. 过程控制检测 — FAIL 项
    if _table_exists(db, schema, "in_process_tests"):
        try:
            with db.cursor() as cur:
                cur.execute(f"""
                    SELECT test_id, batch_id, sample_point, test_type,
                           result_value, spec_min, spec_max, pass_fail,
                           test_date, notes
                    FROM {schema}.in_process_tests
                    WHERE pass_fail = 'FAIL'
                    ORDER BY test_date DESC
                    LIMIT 50
                """)
                for row in cur.fetchall():
                    alerts.append({
                        "type": "threshold",
                        "subtype": "in_process_test",
                        "severity": "red",
                        "item": f"{row['batch_id']} - {row['test_type']} @ {row['sample_point']}",
                        "detail": (
                            f"检测值: {row['result_value']} "
                            f"(标准: {row['spec_min'] or '-'} ~ {row['spec_max'] or '-'})"
                        ),
                        "due_date": str(row["test_date"]),
                        "days_left": None,
                        "pass_fail": row["pass_fail"],
                        "notes": row.get("notes", ""),
                    })
        except Exception:
            pass

    # 2. 成品 QC — 不合格项
    if _table_exists(db, schema, "final_product_qc"):
        try:
            with db.cursor() as cur:
                cur.execute(f"""
                    SELECT qc_report_id, batch_id, test_date,
                           sterility_test, potency_elisa, endotoxin_eu_per_dose,
                           efficacy_challenge, release_decision, reviewer
                    FROM {schema}.final_product_qc
                    WHERE release_decision != 'released'
                       OR sterility_test = 'FAIL'
                       OR efficacy_challenge = 'FAIL'
                    ORDER BY test_date DESC
                """)
                for row in cur.fetchall():
                    issues = []
                    if row["sterility_test"] == "FAIL":
                        issues.append("无菌检查不合格")
                    if row["efficacy_challenge"] == "FAIL":
                        issues.append("攻毒保护试验不合格")
                    if row["release_decision"] == "rejected":
                        issues.append("整批拒收")
                    elif row["release_decision"] == "conditional":
                        issues.append("有条件放行")
                    if row["potency_elisa"] and float(row["potency_elisa"]) < 32:
                        issues.append(f"效价偏低 ({row['potency_elisa']} U)")

                    alerts.append({
                        "type": "threshold",
                        "subtype": "final_product_qc",
                        "severity": "red" if row["release_decision"] == "rejected" else "yellow",
                        "item": f"{row['batch_id']} 成品QC",
                        "detail": "; ".join(issues) if issues else f"放行决定: {row['release_decision']}",
                        "due_date": str(row["test_date"]),
                        "days_left": None,
                        "release_decision": row["release_decision"],
                    })
        except Exception:
            pass

    # 3. 半成品 — 关键指标检查
    if _table_exists(db, schema, "semi_product"):
        try:
            with db.cursor() as cur:
                cur.execute(f"""
                    SELECT semi_id, batch_id, purity_pct, sterility_test,
                           inactivation_verification, endotoxin_eu_per_dose
                    FROM {schema}.semi_product
                    WHERE purity_pct < 90
                       OR sterility_test = 'FAIL'
                       OR inactivation_verification = 'FAIL'
                       OR endotoxin_eu_per_dose > 10
                    ORDER BY batch_id
                """)
                for row in cur.fetchall():
                    issues = []
                    if row["purity_pct"] and float(row["purity_pct"]) < 90:
                        issues.append(f"纯度偏低 ({row['purity_pct']}%)")
                    if row["sterility_test"] == "FAIL":
                        issues.append("无菌检查不合格")
                    if row["inactivation_verification"] == "FAIL":
                        issues.append("灭活验证失败")
                    if row["endotoxin_eu_per_dose"] and float(row["endotoxin_eu_per_dose"]) > 10:
                        issues.append(f"内毒素超标 ({row['endotoxin_eu_per_dose']} EU/dose)")

                    alerts.append({
                        "type": "threshold",
                        "subtype": "semi_product",
                        "severity": "red" if "FAIL" in str(issues) else "yellow",
                        "item": f"{row['batch_id']} 半成品 ({row['semi_id']})",
                        "detail": "; ".join(issues),
                        "due_date": None,
                        "days_left": None,
                    })
        except Exception:
            pass

    if not alerts:
        alerts.append({
            "type": "threshold",
            "subtype": "info",
            "severity": "blue",
            "item": "未发现超限项目",
            "detail": "所有检测结果均在规格范围内",
            "due_date": None,
            "days_left": None,
        })

    return {"alerts": alerts}


# ── trend ──────────────────────────────────────────────────

def _check_trend(db: Database, opts: dict) -> dict:
    """趋势预警：检测参数持续逼近控制限值"""
    schema = opts.get("schema", "analog_pedv")

    alerts = []

    # 检查成品效价趋势（如果表存在）
    if _table_exists(db, schema, "final_product_qc") and _table_exists(db, schema, "production_batches"):
        try:
            with db.cursor() as cur:
                cur.execute(f"""
                    SELECT pb.batch_id, pb.start_date, fq.potency_elisa
                    FROM {schema}.production_batches pb
                    JOIN {schema}.final_product_qc fq ON pb.batch_id = fq.batch_id
                    WHERE fq.potency_elisa IS NOT NULL
                    ORDER BY pb.start_date
                """)
                rows = cur.fetchall()
                if len(rows) >= 7:
                    values = [float(r["potency_elisa"]) for r in rows]
                    # 检查最后 7 点是否都在均值下方
                    avg = sum(values) / len(values)
                    last_7 = values[-7:]
                    all_below = all(v < avg for v in last_7)
                    if all_below:
                        alerts.append({
                            "type": "trend",
                            "subtype": "potency_decline",
                            "severity": "yellow",
                            "item": "成品效价连续偏低",
                            "detail": f"最近 7 批效价均低于历史均值 {avg:.1f} U（{', '.join(f'{v:.0f}' for v in last_7)}）",
                            "due_date": None,
                            "days_left": None,
                        })

                    # 检查连续下降
                    if all(last_7[i] > last_7[i + 1] for i in range(6)):
                        alerts.append({
                            "type": "trend",
                            "subtype": "potency_downtrend",
                            "severity": "yellow",
                            "item": "成品效价连续下降",
                            "detail": f"最近 7 批效价持续下降（{', '.join(f'{v:.0f}' for v in last_7)}）",
                            "due_date": None,
                            "days_left": None,
                        })
        except Exception:
            pass

    if not alerts:
        alerts.append({
            "type": "trend",
            "subtype": "info",
            "severity": "blue",
            "item": "未发现趋势异常",
            "detail": "监控参数无显著趋势偏离",
            "due_date": None,
            "days_left": None,
        })

    return {"alerts": alerts}


# ── summary ────────────────────────────────────────────────

def _check_summary(db: Database, opts: dict) -> dict:
    """汇总所有预警"""
    all_alerts = []

    for check_fn, atype in [
        (_check_expiry, "expiry"),
        (_check_calibration, "calibration"),
        (_check_training, "training"),
        (_check_threshold, "threshold"),
        (_check_trend, "trend"),
    ]:
        try:
            result = check_fn(db, opts)
            for a in result.get("alerts", []):
                # 跳过 "未发现"/"未就绪" 的信息级条目
                if a.get("severity") == "blue" and a.get("subtype") in ("info",):
                    if atype != "threshold" or "未发现" not in a.get("item", ""):
                        continue
                all_alerts.append(a)
        except Exception:
            pass

    # 按严重程度排序：red → yellow → blue
    severity_order = {"red": 0, "yellow": 1, "blue": 2}
    all_alerts.sort(key=lambda a: severity_order.get(a.get("severity", "blue"), 3))

    return {"alerts": all_alerts}