#!/usr/bin/env python3
"""流感疫苗模拟数据生成器

为 myappdb 数据库生成 50 批流感疫苗生产数据（2025-2026年）。
包含好批/坏批、大批间差异、完整的生产和质量管理数据。

用法:
    python scripts/generate_flu_vaccine_data.py [--host localhost] [--port 5432] \\
        [--db myappdb] [--user postgres] [--password xxx]
"""

import argparse
import os
import random
import sys
from datetime import date, timedelta
from decimal import Decimal

import psycopg2

SEED = 42
PRODUCT = "FLU"
BATCH_COUNT = 50
YEARS = [2025, 2026]

random.seed(SEED)

# 流感疫苗毒株（四价）
STRAINS = ["A/H1N1", "A/H3N2", "B/Victoria", "B/Yamagata"]

# 生产方式
PRODUCTION_METHODS = ["egg_based", "cell_based"]

# 异常批次定义（约20%异常率）
ANOMALIES = {
    3:  {"type": "low_ha_titer", "severity": "major",
         "desc": "A/H1N1组分HA效价仅18μg/mL（标准≥20μg/mL），病毒培养时间不足"},
    7:  {"type": "sterility_positive", "severity": "critical",
         "desc": "成品无菌检查阳性（革兰氏阳性球菌），整批报废。根因:灌装间层流罩故障"},
    11: {"type": "endotoxin_high", "severity": "major",
         "desc": "成品内毒素12 EU/mL（标准≤5 EU/mL），增加超滤步骤后复检合格"},
    14: {"type": "egg_allergen", "severity": "minor",
         "desc": "卵清蛋白残留偏高（85ng/mL，标准≤100ng/mL），加强纯化"},
    18: {"type": "inactivation_incomplete", "severity": "critical",
         "desc": "甲醛灭活验证检出活病毒，延长灭活时间24h后复检通过"},
    22: {"type": "formulation_error", "severity": "major",
         "desc": "四价配制比例偏差（A/H3N2组分偏低15%），重新配制后达标"},
    26: {"type": "cold_chain_break", "severity": "major",
         "desc": "运输途中温度升至12°C持续4h（标准2-8°C），MKT=9.2°C，降级放行"},
    30: {"type": "particulate_found", "severity": "major",
         "desc": "灯检发现可见异物（白色絮状），确认为蛋白质聚集，整批重新过滤"},
    34: {"type": "low_potency", "severity": "major",
         "desc": "B/Yamagata组分SRD效价仅13μg（标准≥15μg），有条件放行"},
    38: {"type": "filling_deviation", "severity": "minor",
         "desc": "灌装量偏差（0.45-0.55mL，目标0.5mL），约50瓶剔除"},
    42: {"type": "stability_fail", "severity": "critical",
         "desc": "加速稳定性6月HA效价下降40%（标准≤25%），长期稳定性继续监测"},
    46: {"type": "contamination_true", "severity": "critical",
         "desc": "支原体污染，整批报废。根因:细胞培养血清批次问题"},
    49: {"type": "ph_deviation", "severity": "minor",
         "desc": "成品pH偏低（6.8，标准7.0-7.4），加缓冲液微调后达标"},
}

def norm(mean, std, decimals=2):
    v = random.gauss(mean, std)
    return round(v, decimals)

def norm_int(mean, std):
    return max(0, int(round(random.gauss(mean, std))))

def random_date(start, end):
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, max(0, delta)))

def batch_id(year, num):
    return f"FLU-{year}-{num:04d}"

def ensure(conn, stmt, **kwargs):
    try:
        with conn.cursor() as cur:
            cur.execute(stmt.format(**kwargs))
    except Exception as e:
        print(f"  [WARN] {e}")

def execute_batch(conn, stmt, rows):
    try:
        with conn.cursor() as cur:
            for row in rows:
                try:
                    cur.execute(stmt, row)
                except Exception as e:
                    print(f"  [WARN] row insert: {e}")
    except Exception as e:
        print(f"  [ERROR] batch insert: {e}")

def generate_ddl(conn):
    """创建流感疫苗相关表"""
    cur = conn.cursor()
    prod = "analog_production"

    # 确保 schema 存在
    cur.execute("CREATE SCHEMA IF NOT EXISTS analog_production")
    cur.execute("CREATE SCHEMA IF NOT EXISTS analog_quality")
    cur.execute("CREATE SCHEMA IF NOT EXISTS analog_warehouse")
    cur.execute("CREATE SCHEMA IF NOT EXISTS analog_coldchain")
    cur.execute("CREATE SCHEMA IF NOT EXISTS analog_equipment")
    cur.execute("CREATE SCHEMA IF NOT EXISTS analog_hr")
    cur.execute("CREATE SCHEMA IF NOT EXISTS analog_pv")

    # 授权
    for schema in ["analog_production", "analog_quality", "analog_warehouse",
                   "analog_coldchain", "analog_equipment", "analog_hr", "analog_pv"]:
        try:
            cur.execute(f"GRANT USAGE ON SCHEMA {schema} TO vlm_reader")
        except:
            pass

    # 清理旧的流感疫苗数据
    print("  清理旧数据...")
    try:
        cur.execute(f"DELETE FROM {prod}.flu_production_batches")
    except:
        pass
    try:
        cur.execute(f"DELETE FROM {prod}.flu_virus_culture_log")
    except:
        pass
    try:
        cur.execute(f"DELETE FROM {prod}.flu_harvest_inactivation")
    except:
        pass
    try:
        cur.execute(f"DELETE FROM {prod}.flu_formulation")
    except:
        pass
    try:
        cur.execute(f"DELETE FROM {prod}.flu_semi_product")
    except:
        pass
    try:
        cur.execute(f"DELETE FROM analog_quality.deviations WHERE product_type = 'FLU'")
    except:
        pass
    try:
        cur.execute(f"DELETE FROM analog_quality.final_product_qc WHERE product_type = 'FLU'")
    except:
        pass
    try:
        cur.execute(f"DELETE FROM analog_coldchain.cold_storage_log WHERE product_type = 'FLU'")
    except:
        pass
    try:
        cur.execute(f"DELETE FROM analog_pv.aefi_reports WHERE product_type = 'FLU'")
    except:
        pass

    # 流感疫苗生产批次表
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {prod}.flu_production_batches (
            batch_id VARCHAR(20) PRIMARY KEY,
            product_type VARCHAR(10) DEFAULT 'FLU',
            valency VARCHAR(20) DEFAULT 'quadrivalent',
            strains TEXT[],
            production_method VARCHAR(20),
            egg_supplier VARCHAR(50),
            cell_line VARCHAR(30),
            start_date DATE,
            harvest_date DATE,
            formulation_date DATE,
            fill_finish_date DATE,
            status VARCHAR(20),
            operator_team VARCHAR(10),
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # 病毒培养记录
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {prod}.flu_virus_culture_log (
            id SERIAL PRIMARY KEY,
            batch_id VARCHAR(20),
            strain VARCHAR(20),
            culture_day INT,
            ha_titer_log2 DECIMAL(5,2),
            tcid50_log10 DECIMAL(6,2),
            cell_density_10e6_ml DECIMAL(8,3),
            viability_pct DECIMAL(5,2),
            ph DECIMAL(3,1),
            do_pct DECIMAL(5,1),
            temp_c DECIMAL(3,1),
            notes TEXT
        )
    """)

    # 收获与灭活
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {prod}.flu_harvest_inactivation (
            record_id VARCHAR(30) PRIMARY KEY,
            batch_id VARCHAR(20),
            strain VARCHAR(20),
            harvest_date DATE,
            harvest_volume_l DECIMAL(8,2),
            pre_inactivation_ha_titer DECIMAL(6,2),
            inactivant VARCHAR(20) DEFAULT 'formaldehyde',
            inactivant_conc_pct DECIMAL(4,2),
            inactivation_temp_c DECIMAL(3,1),
            inactivation_duration_h DECIMAL(5,1),
            residual_infectivity_test VARCHAR(10),
            inactivation_completion_date DATE,
            post_inactivation_volume_l DECIMAL(8,2)
        )
    """)

    # 纯化与配制
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {prod}.flu_formulation (
            record_id VARCHAR(30) PRIMARY KEY,
            batch_id VARCHAR(20),
            strain VARCHAR(20),
            purified_volume_l DECIMAL(8,2),
            ha_content_ug_ml DECIMAL(6,2),
            total_protein_mg_ml DECIMAL(6,3),
            purity_pct DECIMAL(5,2),
            dna_residual_ng_ml DECIMAL(6,2),
            ovalbumin_ng_ml DECIMAL(6,2),
            endotoxin_eu_ml DECIMAL(5,2),
            target_ha_ug_per_dose DECIMAL(5,2),
            actual_ha_ug_per_dose DECIMAL(5,2),
            buffer_type VARCHAR(30),
            ph DECIMAL(3,1)
        )
    """)

    # 半成品
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {prod}.flu_semi_product (
            semi_id VARCHAR(30) PRIMARY KEY,
            batch_id VARCHAR(20),
            volume_l DECIMAL(8,2),
            strains TEXT[],
            ha_content_h1n1_ug_ml DECIMAL(6,2),
            ha_content_h3n2_ug_ml DECIMAL(6,2),
            ha_content_bvic_ug_ml DECIMAL(6,2),
            ha_content_byam_ug_ml DECIMAL(6,2),
            total_protein_mg_ml DECIMAL(6,3),
            ph DECIMAL(3,1),
            appearance VARCHAR(50),
            sterility_test VARCHAR(10),
            endotoxin_eu_per_dose DECIMAL(5,2)
        )
    """)

    # 授权 vlm_reader
    for table in ['flu_production_batches', 'flu_virus_culture_log', 'flu_harvest_inactivation', 'flu_formulation', 'flu_semi_product']:
        try:
            cur.execute(f"GRANT SELECT ON {prod}.{table} TO vlm_reader")
        except:
            pass

    print("  DDL 创建完成")

def gen_flu_batches():
    """生成50批流感疫苗基础数据"""
    batches = []
    batch_num = 1

    for year in YEARS:
        count = 25 if year == 2025 else 25
        for i in range(count):
            bid = batch_id(year, batch_num)
            method = random.choice(PRODUCTION_METHODS)
            status = "released"
            anomaly = None

            if batch_num in ANOMALIES:
                anomaly = ANOMALIES[batch_num]
                if anomaly["severity"] == "critical":
                    status = "rejected"
                elif anomaly["severity"] == "major":
                    status = random.choice(["conditional_release", "released"])
                else:
                    status = "released"

            batches.append({
                "batch_id": bid,
                "year": year,
                "num": batch_num,
                "method": method,
                "status": status,
                "anomaly": anomaly,
                "start_date": random_date(date(year, 1, 1), date(year, 11, 30)),
            })
            batch_num += 1

    return batches

def gen_production_data(conn, batches):
    """生成生产数据"""
    prod = "analog_production"

    # 插入批次主表
    batch_rows = []
    for b in batches:
        harvest_date = b["start_date"] + timedelta(days=norm_int(21, 3))
        formulation_date = harvest_date + timedelta(days=norm_int(14, 2))
        fill_finish_date = formulation_date + timedelta(days=norm_int(7, 1))

        egg_supplier = random.choice(["SPF Eggs Inc.", "Charles River", "Merck Animal Health"]) if b["method"] == "egg_based" else None
        cell_line = "MDCK" if b["method"] == "cell_based" else None

        batch_rows.append((
            b["batch_id"], PRODUCT, "quadrivalent", STRAINS, b["method"],
            egg_supplier, cell_line, b["start_date"], harvest_date,
            formulation_date, fill_finish_date, b["status"],
            random.choice(["A", "B", "C"]),
            b["anomaly"]["desc"] if b["anomaly"] else None
        ))

    execute_batch(conn, f"""
        INSERT INTO {prod}.flu_production_batches
        (batch_id, product_type, valency, strains, production_method,
         egg_supplier, cell_line, start_date, harvest_date, formulation_date,
         fill_finish_date, status, operator_team, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, batch_rows)
    print(f"  flu_production_batches: {len(batch_rows)} 行")

    # 病毒培养记录（每批4个毒株，每株7天培养）
    culture_rows = []
    for b in batches:
        for strain in STRAINS:
            base_ha = norm(8.5, 1.5) if strain.startswith("A/") else norm(7.8, 1.2)
            base_tcid = norm(7.5, 0.8)

            for day in range(1, 8):
                ha_titer = base_ha + norm(day * 0.3, 0.2)
                tcid50 = base_tcid + norm(day * 0.2, 0.15)
                cell_density = norm(2.5 + day * 0.8, 0.3)
                viability = norm(95 - day * 2, 3)

                culture_rows.append((
                    b["batch_id"], strain, day, ha_titer, tcid50,
                    cell_density, viability, norm(7.2, 0.2), norm(45, 8),
                    norm(35, 0.5), None
                ))

    execute_batch(conn, f"""
        INSERT INTO {prod}.flu_virus_culture_log
        (batch_id, strain, culture_day, ha_titer_log2, tcid50_log10,
         cell_density_10e6_ml, viability_pct, ph, do_pct, temp_c, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, culture_rows)
    print(f"  flu_virus_culture_log: {len(culture_rows)} 行")

    # 收获与灭活
    harvest_rows = []
    for b in batches:
        for strain in STRAINS:
            ha_titer = norm(25, 8)
            volume = norm(50, 10)
            duration = norm(72, 8)

            harvest_rows.append((
                f"HARV-{b['batch_id']}-{strain}", b["batch_id"], strain,
                b["start_date"] + timedelta(days=21), volume, ha_titer,
                "formaldehyde", norm(0.02, 0.003), norm(37, 0.5), duration,
                "pass", b["start_date"] + timedelta(days=24), norm(45, 8)
            ))

    execute_batch(conn, f"""
        INSERT INTO {prod}.flu_harvest_inactivation
        (record_id, batch_id, strain, harvest_date, harvest_volume_l,
         pre_inactivation_ha_titer, inactivant, inactivant_conc_pct,
         inactivation_temp_c, inactivation_duration_h, residual_infectivity_test,
         inactivation_completion_date, post_inactivation_volume_l)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, harvest_rows)
    print(f"  flu_harvest_inactivation: {len(harvest_rows)} 行")

    # 纯化与配制
    form_rows = []
    for b in batches:
        for strain in STRAINS:
            ha_content = norm(22, 5)
            ovalbumin = norm(75, 15) if b["method"] == "egg_based" else norm(5, 2)

            form_rows.append((
                f"FORM-{b['batch_id']}-{strain}", b["batch_id"], strain,
                norm(40, 8), ha_content, norm(0.8, 0.2), norm(95, 3),
                norm(8, 3), ovalbumin, norm(3.5, 1.5), norm(15, 1),
                norm(15, 1), "PBS", norm(7.2, 0.15)
            ))

    execute_batch(conn, f"""
        INSERT INTO {prod}.flu_formulation
        (record_id, batch_id, strain, purified_volume_l, ha_content_ug_ml,
         total_protein_mg_ml, purity_pct, dna_residual_ng_ml, ovalbumin_ng_ml,
         endotoxin_eu_ml, target_ha_ug_per_dose, actual_ha_ug_per_dose,
         buffer_type, ph)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, form_rows)
    print(f"  flu_formulation: {len(form_rows)} 行")

    # 半成品
    semi_rows = []
    for b in batches:
        semi_rows.append((
            f"SEMI-{b['batch_id']}", b["batch_id"], norm(180, 20), STRAINS,
            norm(15, 2), norm(15, 2), norm(15, 2), norm(15, 2),
            norm(0.6, 0.1), norm(7.2, 0.1), "clear liquid",
            random.choice(["pass", "pass", "pass", "fail"]),
            norm(3.5, 1.2)
        ))

    execute_batch(conn, f"""
        INSERT INTO {prod}.flu_semi_product
        (semi_id, batch_id, volume_l, strains, ha_content_h1n1_ug_ml,
         ha_content_h3n2_ug_ml, ha_content_bvic_ug_ml, ha_content_byam_ug_ml,
         total_protein_mg_ml, ph, appearance, sterility_test, endotoxin_eu_per_dose)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, semi_rows)
    print(f"  flu_semi_product: {len(semi_rows)} 行")

    conn.commit()

def gen_quality_data(conn, batches):
    """生成质量管理数据"""
    quality = "analog_quality"

    # 偏差记录
    dev_rows = []
    for b in batches:
        if b["anomaly"]:
            dev_id = f"DEV-{b['batch_id']}"
            reported_date = random_date(b["start_date"], b["start_date"] + timedelta(days=60))
            status = random.choice(["open", "closed", "closed"])
            resolved_date = reported_date + timedelta(days=norm_int(30, 10)) if status == "closed" else None
            capa_id = f"CAPA-{b['batch_id']}" if b["anomaly"]["severity"] == "critical" else None

            dev_rows.append((
                dev_id, PRODUCT, b["batch_id"], b["anomaly"]["type"],
                b["anomaly"]["severity"], b["anomaly"]["desc"],
                f"根因: {b['anomaly']['desc']}", "已采取纠正措施",
                capa_id, reported_date, resolved_date, status,
                random.choice(["QA_team", "Production_team", "QC_team"])
            ))

    if dev_rows:
        execute_batch(conn, f"""
            INSERT INTO {quality}.deviations
            (dev_id, product_type, batch_id, dev_type, severity, description,
             root_cause, corrective_action, capa_id, reported_date,
             resolved_date, status, reported_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, dev_rows)
        print(f"  deviations: {len(dev_rows)} 行")

    # 成品放行检测
    qc_rows = []
    for b in batches:
        qc_report_id = f"QC-{b['batch_id']}"
        test_date = random_date(b["start_date"], b["start_date"] + timedelta(days=90))

        # 计算平均 HA 含量作为 potency
        avg_ha = (norm(15, 2) + norm(15, 2) + norm(15, 2) + norm(15, 2)) / 4

        release = "released" if b["status"] == "released" else "rejected"
        if b["status"] == "conditional_release":
            release = "released"  # 简化为 released，避免字段长度问题

        qc_rows.append((
            qc_report_id, PRODUCT, b["batch_id"], test_date,
            "clear liquid", norm(7.2, 0.1),
            random.choice(["pass", "pass", "pass", "fail"]),
            norm(3.2, 0.8), avg_ha, "μg/dose",
            "pass", "pass", None, norm(0.5, 0.05), None, None,
            release, random.choice(["Dr_Zhang", "Dr_Li", "Dr_Wang"]), None
        ))

    execute_batch(conn, f"""
        INSERT INTO {quality}.final_product_qc
        (qc_report_id, product_type, batch_id, test_date, appearance, ph,
         sterility_test, endotoxin_eu_per_dose, potency, potency_unit,
         safety_test, efficacy_test, residual_moisture_pct,
         filling_volume_ml, adjuvant_content_mg_ml, reconstitution_time_s,
         release_decision, reviewer, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, qc_rows)
    print(f"  final_product_qc: {len(qc_rows)} 行")

    conn.commit()

def gen_warehouse_data(conn, batches):
    """生成仓储数据"""
    wh = "analog_warehouse"

    # 仓库环境监控（每天4个数据点，持续60天）
    monitor_rows = []
    start_date = date(2025, 1, 1)
    for day in range(60):
        for hour in [0, 6, 12, 18]:
            temp = norm(5, 1.2)
            humidity = norm(55, 8)
            alarm = temp > 8 or temp < 2

            monitor_rows.append((
                start_date + timedelta(days=day, hours=hour),
                temp, humidity, "cold_storage_A", alarm
            ))

    if monitor_rows:
        execute_batch(conn, f"""
            INSERT INTO {wh}.warehouse_monitoring
            (monitor_ts, temp_c, humidity_pct, zone, alarm_flag)
            VALUES (%s, %s, %s, %s, %s)
        """, monitor_rows)
        print(f"  warehouse_monitoring: {len(monitor_rows)} 行")

    conn.commit()

def gen_coldchain_data(conn, batches):
    """生成冷链监控数据"""
    cc = "analog_coldchain"

    # 冷库温度监控（每批30天，每天4个数据点）
    storage_rows = []
    for b in batches[:30]:  # 为前30批生成详细数据
        start = b["start_date"] + timedelta(days=90)
        for day in range(30):
            for hour in [0, 6, 12, 18]:
                temp = norm(5, 1.5)
                alarm = False

                # 模拟冷链中断
                if b.get("anomaly") and b["anomaly"]["type"] == "cold_chain_break":
                    if day == 15 and hour == 12:
                        temp = 12.5
                        alarm = True

                storage_rows.append((
                    PRODUCT, b["batch_id"], start + timedelta(days=day, hours=hour),
                    temp, norm(55, 8), "冷库#1-A区", alarm
                ))

    if storage_rows:
        execute_batch(conn, f"""
            INSERT INTO {cc}.cold_storage_log
            (product_type, batch_id, monitor_ts, temp_c, humidity_pct,
             storage_location, alarm_flag)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, storage_rows)
        print(f"  cold_storage_log: {len(storage_rows)} 行")

    conn.commit()

def gen_aefi_data(conn, batches):
    """生成AEFI（疑似预防接种异常反应）数据"""
    pv = "analog_pv"

    aefi_rows = []
    released_batches = [b for b in batches if b["status"] in ["released", "conditional_release"]]

    # 常见轻微反应（约5%接种者）
    for b in released_batches[:30]:
        count = norm_int(50, 20)
        for _ in range(count):
            symptom = random.choice(["injection_site_pain", "fever_low", "fatigue", "headache"])
            aefi_rows.append((
                PRODUCT, b["batch_id"],
                random_date(b["start_date"] + timedelta(days=100), b["start_date"] + timedelta(days=180)),
                random.choice([18, 25, 35, 45, 60]),
                symptom, "mild", norm_int(4, 2), norm_int(24, 12),
                "recovered", "probably_related", None
            ))

    # 严重反应（约0.1%）
    serious_cases = [
        ("FLU-2025-0007", "anaphylaxis", "severe", "probably_related"),
        ("FLU-2025-0018", "guillain_barre", "severe", "possibly_related"),
        ("FLU-2026-0034", "febrile_seizure", "moderate", "probably_related"),
    ]

    for bid, symptom, sev, causality in serious_cases:
        aefi_rows.append((
            PRODUCT, bid, random_date(date(2025, 6, 1), date(2026, 6, 1)),
            random.choice([30, 45, 55]), symptom, sev, norm_int(2, 1),
            norm_int(72, 24), "recovered_with_intervention", causality,
            f"严重不良事件: {symptom}"
        ))

    if aefi_rows:
        execute_batch(conn, f"""
            INSERT INTO {pv}.aefi_reports
            (product_type, batch_id, report_date, patient_age_months, symptom,
             severity, onset_hours_post_vaccination, duration_hours, outcome,
             causality_assessment, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, aefi_rows)
        print(f"  aefi_reports: {len(aefi_rows)} 行")

    conn.commit()

def verify_data(conn):
    """验证数据完整性"""
    cur = conn.cursor()
    print("\n" + "="*60)
    print("  数据验证")
    print("="*60)

    # 批次统计
    cur.execute("SELECT COUNT(*) FROM analog_production.flu_production_batches")
    total = cur.fetchone()[0]
    print(f"\n  总批次数: {total}")

    cur.execute("SELECT status, COUNT(*) FROM analog_production.flu_production_batches GROUP BY status")
    print("\n  [状态分布]")
    for status, cnt in cur.fetchall():
        print(f"    {status}: {cnt}")

    cur.execute("SELECT production_method, COUNT(*) FROM analog_production.flu_production_batches GROUP BY production_method")
    print("\n  [生产方式分布]")
    for method, cnt in cur.fetchall():
        print(f"    {method}: {cnt}")

    cur.execute("SELECT severity, COUNT(*) FROM analog_quality.deviations WHERE product_type = 'FLU' GROUP BY severity")
    print("\n  [偏差严重等级分布]")
    for sev, cnt in cur.fetchall():
        print(f"    {sev}: {cnt}")

    cur.execute("SELECT release_decision, COUNT(*) FROM analog_quality.final_product_qc WHERE product_type = 'FLU' GROUP BY release_decision")
    print("\n  [放行决策分布]")
    for dec, cnt in cur.fetchall():
        print(f"    {dec}: {cnt}")

    print("\n  验证完成.")

def parse_args():
    p = argparse.ArgumentParser(description="流感疫苗模拟数据生成器")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=5432)
    p.add_argument("--db", default="myappdb")
    p.add_argument("--user", default="postgres")
    p.add_argument("--password", default=None)
    return p.parse_args()

def connect_db(args):
    password = args.password or os.getenv("PGPASSWORD")
    if not password:
        print("错误: 需要密码。用 --password 或设置 PGPASSWORD 环境变量。")
        sys.exit(1)
    conn = psycopg2.connect(
        host=args.host, port=args.port, dbname=args.db,
        user=args.user, password=password,
    )
    conn.autocommit = True
    return conn

def main():
    args = parse_args()
    print("=" * 60)
    print("  流感疫苗模拟数据生成器")
    print(f"  50 批次 × 四价流感疫苗 (2025-2026)")
    print(f"  连接: {args.host}:{args.port}/{args.db}")
    print("=" * 60)

    conn = connect_db(args)

    print("\n[Phase 1] 创建表结构...")
    generate_ddl(conn)

    print("\n[Phase 2] 生成批次基础数据...")
    batches = gen_flu_batches()
    print(f"  生成 {len(batches)} 批")

    print("\n[Phase 3] 生成生产数据...")
    gen_production_data(conn, batches)

    print("\n[Phase 4] 生成质量管理数据...")
    gen_quality_data(conn, batches)

    print("\n[Phase 5] 生成仓储数据...")
    gen_warehouse_data(conn, batches)

    print("\n[Phase 6] 生成冷链监控数据...")
    gen_coldchain_data(conn, batches)

    print("\n[Phase 7] 生成 AEFI 数据...")
    gen_aefi_data(conn, batches)

    print("\n[Phase 8] 数据验证...")
    verify_data(conn)

    conn.close()
    print("\n完成!")

if __name__ == "__main__":
    main()
