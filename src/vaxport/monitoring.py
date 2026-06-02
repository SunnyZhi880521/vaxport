"""预警监控 — 聚合 + 严重度分级 + 仪表盘摘要

为 AlertMonitoringAgent 提供全类型预警聚合和风险分级。
基于 alerts.py 的 check_alerts() 扩展。
"""

import json
from datetime import date, datetime, timedelta
from typing import Optional

from vaxport.alerts import check_alerts
from vaxport.db import Database


# 严重度分级阈值
SEVERITY_RULES = {
    "Critical": {
        "expiry": "days_remaining <= 0",
        "calibration": "days_remaining <= 0",
        "training": "days_remaining <= 0",
        "threshold": "severity == 'OOS'",
        "trend": "consecutive_points >= 7 AND direction == 'same_side'",
    },
    "Major": {
        "expiry": "0 < days_remaining <= 30",
        "calibration": "0 < days_remaining <= 14",
        "training": "0 < days_remaining <= 30",
        "threshold": "severity == '超限'",
        "trend": "consecutive_points >= 5",
    },
    "Minor": {
        "expiry": "30 < days_remaining <= 90",
        "calibration": "14 < days_remaining <= 60",
        "training": "30 < days_remaining <= 90",
        "threshold": "severity == '预警'",
        "trend": "consecutive_points >= 3",
    },
}


def classify_alert_severity(alert: dict) -> str:
    """对单条预警分级。

    Args:
        alert: {"type": "expiry"|"calibration"|..., "days_remaining": N, ...}

    Returns:
        "Critical" / "Major" / "Minor" / "Info"
    """
    alert_type = alert.get("type", "")
    if alert_type not in SEVERITY_RULES["Critical"]:
        return "Info"

    days = alert.get("days_remaining")
    severity = alert.get("severity", "")
    consecutive = alert.get("consecutive_points", 0)

    # Critical 判定
    if alert_type in ("expiry", "calibration", "training") and days is not None:
        if days <= 0:
            return "Critical"
    if alert_type == "threshold" and severity == "OOS":
        return "Critical"
    if alert_type == "trend" and consecutive >= 7:
        return "Critical"

    # Major 判定
    if alert_type == "calibration" and days is not None and 0 < days <= 14:
        return "Major"
    if alert_type in ("expiry", "training") and days is not None and 0 < days <= 30:
        return "Major"
    if alert_type == "threshold" and severity == "超限":
        return "Major"
    if alert_type == "trend" and consecutive >= 5:
        return "Major"

    # Minor 判定
    if alert_type == "calibration" and days is not None and 14 < days <= 60:
        return "Minor"
    if alert_type in ("expiry", "training") and days is not None and 30 < days <= 90:
        return "Minor"
    if alert_type == "threshold" and severity == "预警":
        return "Minor"
    if alert_type == "trend" and consecutive >= 3:
        return "Minor"

    return "Info"


def get_alert_summary(db: Optional[Database] = None) -> dict:
    """全类型预警聚合摘要。

    Returns:
        仪表盘式摘要: {"total": N, "critical": N, "by_type": {...}, "alerts": [...]}
    """
    if not db:
        return {
            "status": "no_database",
            "note": "数据库未连接，无法获取实时预警。请连接数据库后重试。",
        }

    alert_types = ["expiry", "calibration", "training", "threshold", "trend"]
    all_alerts = []
    by_type = {}
    errors = []

    for at in alert_types:
        try:
            result = check_alerts(db, at)
            alerts = result.get("alerts", [])
            if not alerts and "note" not in result:
                alerts = []
            # 为每个 alert 标注类型和严重度
            for a in alerts:
                a["type"] = at
                a["severity_level"] = classify_alert_severity(a)
            all_alerts.extend(alerts)
            by_type[at] = {
                "count": len(alerts),
                "critical": sum(1 for a in alerts if a.get("severity_level") == "Critical"),
                "major": sum(1 for a in alerts if a.get("severity_level") == "Major"),
                "minor": sum(1 for a in alerts if a.get("severity_level") == "Minor"),
            }
        except Exception as e:
            errors.append(f"{at}: {e}")
            by_type[at] = {"count": 0, "error": str(e)}

    # 汇总统计
    critical_count = sum(1 for a in all_alerts if a.get("severity_level") == "Critical")
    major_count = sum(1 for a in all_alerts if a.get("severity_level") == "Major")
    minor_count = sum(1 for a in all_alerts if a.get("severity_level") == "Minor")
    info_count = len(all_alerts) - critical_count - major_count - minor_count

    # 严重度分布
    severity_distribution = {
        "Critical": {"count": critical_count, "action": "立即通知QA负责人和质量受权人"},
        "Major": {"count": major_count, "action": "24小时内处理并记录"},
        "Minor": {"count": minor_count, "action": "纳入日常监控，记录跟踪"},
        "Info": {"count": info_count, "action": "常规记录"},
    }

    # 生成摘要文本
    summary_parts = [f"预警总数: {len(all_alerts)}"]
    if critical_count > 0:
        summary_parts.append(f"🔴 Critical: {critical_count} (需立即处理)")
    if major_count > 0:
        summary_parts.append(f"🟠 Major: {major_count} (24h内处理)")
    if minor_count > 0:
        summary_parts.append(f"🟡 Minor: {minor_count} (记录跟踪)")

    top_critical = [a for a in all_alerts if a.get("severity_level") == "Critical"][:5]

    return {
        "generated_at": datetime.now().isoformat(),
        "total_alerts": len(all_alerts),
        "critical": critical_count,
        "major": major_count,
        "minor": minor_count,
        "summary": "；".join(summary_parts) + "。",
        "severity_distribution": severity_distribution,
        "by_type": by_type,
        "top_critical": top_critical,
        "errors": errors if errors else None,
    }


def get_alert_detail(db: Optional[Database], alert_type: str,
                     filters: str = "{}") -> dict:
    """获取单类型预警详情（含严重度分级）。

    Args:
        db: 数据库连接
        alert_type: expiry/calibration/training/threshold/trend
        filters: 过滤条件 JSON

    Returns:
        分级后的预警列表
    """
    if not db:
        return {"error": "数据库未连接"}

    result = check_alerts(db, alert_type, filters)
    alerts = result.get("alerts", [])
    if isinstance(alerts, list):
        for a in alerts:
            a["type"] = alert_type
            a["severity_level"] = classify_alert_severity(a)

        # 按严重度排序
        severity_order = {"Critical": 0, "Major": 1, "Minor": 2, "Info": 3}
        alerts.sort(key=lambda a: severity_order.get(a.get("severity_level", "Info"), 99))

    result["alerts"] = alerts
    result["severity_summary"] = {
        "Critical": sum(1 for a in alerts if a.get("severity_level") == "Critical"),
        "Major": sum(1 for a in alerts if a.get("severity_level") == "Major"),
        "Minor": sum(1 for a in alerts if a.get("severity_level") == "Minor"),
    }
    return result