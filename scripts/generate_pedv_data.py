#!/usr/bin/env python3
"""PEDV 灭活疫苗模拟生产数据生成器 v2.0

生成 analog_pedv schema 下 22 张表 × 50 批次完整生产数据。
覆盖全链条：原料仓储→上游生产→下游制备→冷链储存→运输监控。
含 16 个异常批次（含 Critical 级 + 仓储→生产追溯链）。

用法:
    python scripts/generate_pedv_data.py [--host localhost] [--port 5432] \
        [--db myappdb] [--user postgres] [--password xxx]
"""

import argparse
import json
import math
import os
import random
import sys
from datetime import date, timedelta, datetime

import psycopg2
from psycopg2 import sql

# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

SCHEMA = "analog_pedv"
SEED = 42
TOTAL_BATCHES = 50

# 异常批次定义 (16 个)
ANOMALY_BATCHES = {
    8: {
        "type": "sterility_false_positive",
        "severity": "minor",
        "description": "过程控制无菌检查假阳性（操作污染），复检通过，成品放行",
    },
    12: {
        "type": "mycoplasma_false_positive",
        "severity": "minor",
        "description": "过程控制支原体检查假阳性（DNA荧光染色），培养法确认为阴性",
    },
    16: {
        "type": "inactivation_failure",
        "severity": "critical",
        "description": "BEI灭活不彻底，细胞接种法检出活病毒，整批拒收。根因: 高生物负荷(180 CFU) + 低BEI浓度(1.2mM) + 高病毒量(8.3 log10)",
    },
    19: {
        "type": "low_potency",
        "severity": "major",
        "description": "成品potency_elisa=28 U（标准≥32），MOI偏低(0.03)导致病毒增殖不充分，有条件放行",
    },
    22: {
        "type": "env_excursion",
        "severity": "major",
        "description": "洁净区灌装间(grade A)粒子计数超标（0.5μm: 4200/m³，标准≤3520），灌装暂停2h，HVAC自控恢复后复检合格",
    },
    24: {
        "type": "equipment_failure",
        "severity": "major",
        "description": "管式离心机#2 运行中振动超标停机，导致澄清工序延迟6h。根因: 轴承磨损",
    },
    27: {
        "type": "ph_anomaly_2025",
        "severity": "minor",
        "description": "细胞培养Day3 pH降至6.52（正常6.8-7.4），CO₂流量计短暂故障，持续约8h后恢复",
    },
    28: {
        "type": "do_anomaly_2025",
        "severity": "major",
        "description": "病毒培养阶段DO持续偏低（最低22%），纯氧供气管路泄漏，细胞密度峰值仅3.8×10⁶/mL",
    },
    33: {
        "type": "operator_error",
        "severity": "minor",
        "description": "操作员未按SOP-MFG-0012规定在灭活前检测bioburden，事后补测发现bioburden=95 CFU（接近上限）",
    },
    35: {
        "type": "reagent_expired",
        "severity": "major",
        "description": "ELISA检测试剂盒过期2天仍在使用，导致potency读数异常偏高(52 U)，复检确认实际值为38 U。根因: 仓储未做效期预警",
    },
    38: {
        "type": "media_degraded",
        "severity": "major",
        "description": "生长培养基MED-G-001受潮结块（仓储湿度超标→包装密封性破坏），细胞生长缓慢，峰值密度仅4.2×10⁶/mL，成品效价32 U（低空飞过）。追溯: warehouse_monitoring 7月湿度85%超标→storage_excursions记录→培养基入厂检验漏检",
    },
    40: {
        "type": "adjuvant_failure",
        "severity": "critical",
        "description": "ISA 206佐剂乳化失败，油相/水相分离，半成品报废。根因: 仓储温度超标（冷库#3 曾升至18°C持续6h），佐剂物理性质改变",
    },
    42: {
        "type": "low_potency_repeat",
        "severity": "major",
        "description": "第二次效价不达标(potency=30 U)，与PEDV-2024-0019属同类偏差。CAPA有效性存疑：MOI下限已调整为0.05但本批仍用0.04",
    },
    45: {
        "type": "contamination_true",
        "severity": "critical",
        "description": "支原体真阳性（培养法+DNA染色双确认），整批报废。根因: 操作人员更衣不规范，从普通区带入污染",
    },
    47: {
        "type": "filling_error",
        "severity": "major",
        "description": "灌装机#1 装量偏差超限（目标2.0mL，实际1.82-2.35mL，RSO 8.5%），约300瓶需剔除。根因: 陶瓷泵密封圈磨损",
    },
    50: {
        "type": "cold_chain_break",
        "severity": "major",
        "description": "运输途中冷藏车制冷机组故障3h，温度升至22°C。产品到达后评估: MKT=12.3°C，做稳定性加测后降级放行",
    },
}

def parse_args():
    p = argparse.ArgumentParser(description="PEDV 疫苗模拟数据生成器 v2.0")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=5432)
    p.add_argument("--db", default="myappdb")
    p.add_argument("--user", default="postgres")
    p.add_argument("--password", default=None)
    return p.parse_args()

def connect(args):
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


# ═══════════════════════════════════════════════════════════
# DDL — 22 张表
# ═══════════════════════════════════════════════════════════

DDL_STATEMENTS = [
    # ── 参考数据表 ──
    """CREATE TABLE IF NOT EXISTS {schema}.cell_seeds (
        seed_id VARCHAR(20) PRIMARY KEY, seed_name VARCHAR(100),
        cell_line VARCHAR(30), bank_type VARCHAR(10),
        passage_level INT, date_established DATE,
        viability_pct DECIMAL(5,2), cell_density_per_ml INT,
        sterility_test VARCHAR(10), mycoplasma_test VARCHAR(10),
        extraneous_virus_test VARCHAR(10), tumorigenicity_test VARCHAR(10),
        is_in_use BOOLEAN
    )""",
    """CREATE TABLE IF NOT EXISTS {schema}.virus_seeds (
        seed_id VARCHAR(20) PRIMARY KEY, seed_name VARCHAR(100),
        strain VARCHAR(50), bank_type VARCHAR(10),
        passage_level INT, passage_history TEXT, date_established DATE,
        titer_tcid50_per_ml DECIMAL(10,2),
        sterility_test VARCHAR(10), mycoplasma_test VARCHAR(10),
        identity_test VARCHAR(10), bvdv_test VARCHAR(10),
        is_in_use BOOLEAN
    )""",
    """CREATE TABLE IF NOT EXISTS {schema}.culture_media (
        medium_id VARCHAR(20) PRIMARY KEY, medium_name VARCHAR(100),
        medium_type VARCHAR(30), supplier VARCHAR(100),
        lot_number VARCHAR(30), is_serum_free BOOLEAN,
        is_chemically_defined BOOLEAN, glucose_g_per_l DECIMAL(5,2),
        glutamine_mm DECIMAL(5,2), ph_target DECIMAL(3,1),
        osmolality_mosm INT, sterility_test VARCHAR(10),
        endotoxin_eu_per_ml DECIMAL(5,2),
        date_manufactured DATE, shelf_life_months INT
    )""",
    # ── 生产主表 ──
    """CREATE TABLE IF NOT EXISTS {schema}.production_batches (
        batch_id VARCHAR(20) PRIMARY KEY, product_name VARCHAR(100),
        cell_seed_id VARCHAR(20) REFERENCES {schema}.cell_seeds(seed_id),
        virus_seed_id VARCHAR(20) REFERENCES {schema}.virus_seeds(seed_id),
        growth_medium_id VARCHAR(20) REFERENCES {schema}.culture_media(medium_id),
        maintenance_medium_id VARCHAR(20) REFERENCES {schema}.culture_media(medium_id),
        bioreactor_scale_l INT, moi DECIMAL(4,2),
        start_date DATE, planned_harvest_date DATE, actual_harvest_date DATE,
        status VARCHAR(20), operator_team VARCHAR(30), notes TEXT
    )""",
    # ── 生产工序表 ──
    """CREATE TABLE IF NOT EXISTS {schema}.cell_culture_log (
        id SERIAL PRIMARY KEY, batch_id VARCHAR(20) REFERENCES {schema}.production_batches(batch_id),
        culture_day INT,
        cell_density_10e6_ml DECIMAL(8,3), viability_pct DECIMAL(5,2),
        ph DECIMAL(3,1), do_pct DECIMAL(5,1), temp_c DECIMAL(3,1),
        glucose_g_per_l DECIMAL(5,3), lactate_g_per_l DECIMAL(5,3),
        ammonia_mm DECIMAL(5,2), osmolality_mosm INT, agitation_rpm INT,
        notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS {schema}.virus_culture_log (
        id SERIAL PRIMARY KEY, batch_id VARCHAR(20) REFERENCES {schema}.production_batches(batch_id),
        dpi INT, cpe_pct DECIMAL(5,1),
        cell_density_10e6_ml DECIMAL(8,3), viability_pct DECIMAL(5,2),
        ph DECIMAL(3,1), do_pct DECIMAL(5,1),
        glucose_g_per_l DECIMAL(5,3), lactate_g_per_l DECIMAL(5,3),
        sample_titer_tcid50 DECIMAL(10,2), notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS {schema}.harvest_inactivation (
        record_id VARCHAR(20) PRIMARY KEY,
        batch_id VARCHAR(20) REFERENCES {schema}.production_batches(batch_id),
        harvest_date DATE, harvest_volume_l DECIMAL(8,2),
        pre_clarify_titer DECIMAL(10,2), clarification_method VARCHAR(30),
        post_clarify_volume_l DECIMAL(8,2), post_clarify_titer DECIMAL(10,2),
        bioburden_pre_cfu INT, bioburden_post_cfu INT,
        inactivant VARCHAR(30), inactivant_conc_mm DECIMAL(5,3),
        inactivation_temp_c DECIMAL(3,1), inactivation_duration_h DECIMAL(5,1),
        pre_inactivation_titer DECIMAL(10,2),
        residual_infectivity_test VARCHAR(10),
        inactivation_completion_date DATE,
        concentration_factor DECIMAL(5,2), post_conc_volume_l DECIMAL(8,2)
    )""",
    """CREATE TABLE IF NOT EXISTS {schema}.semi_product (
        semi_id VARCHAR(20) PRIMARY KEY,
        batch_id VARCHAR(20) REFERENCES {schema}.production_batches(batch_id),
        volume_l DECIMAL(8,2), antigen_content_elisa_u_ml DECIMAL(10,2),
        total_protein_mg_ml DECIMAL(6,3), purity_pct DECIMAL(5,2),
        endotoxin_eu_per_dose DECIMAL(5,2), sterility_test VARCHAR(10),
        ph DECIMAL(3,1), appearance VARCHAR(50),
        inactivation_verification VARCHAR(10),
        adjuvant_type VARCHAR(50), adjuvant_ratio DECIMAL(5,2)
    )""",
    # ── QC 表 ──
    """CREATE TABLE IF NOT EXISTS {schema}.in_process_tests (
        test_id SERIAL PRIMARY KEY,
        batch_id VARCHAR(20) REFERENCES {schema}.production_batches(batch_id),
        sample_point VARCHAR(30), test_type VARCHAR(40),
        test_date DATE, result_value VARCHAR(50),
        spec_min VARCHAR(50), spec_max VARCHAR(50),
        pass_fail VARCHAR(10), tested_by VARCHAR(30), notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS {schema}.final_product_qc (
        qc_report_id VARCHAR(20) PRIMARY KEY,
        batch_id VARCHAR(20) REFERENCES {schema}.production_batches(batch_id),
        test_date DATE, appearance VARCHAR(50), ph DECIMAL(3,1),
        sterility_test VARCHAR(10), endotoxin_eu_per_dose DECIMAL(5,2),
        potency_elisa DECIMAL(10,2), potency_tcid50 DECIMAL(10,2),
        safety_test_mice VARCHAR(10), safety_test_piglets VARCHAR(10),
        efficacy_challenge VARCHAR(10),
        residual_bei_mm DECIMAL(6,4), residual_bsa_ppm DECIMAL(6,2),
        adjuvant_content_mg_ml DECIMAL(6,2),
        aluminum_content_mg_ml DECIMAL(6,3),
        filling_volume_ml DECIMAL(5,2),
        release_decision VARCHAR(15), reviewer VARCHAR(30),
        expiry_date DATE
    )""",
    """CREATE TABLE IF NOT EXISTS {schema}.deviations (
        deviation_id VARCHAR(20) PRIMARY KEY,
        batch_id VARCHAR(20) REFERENCES {schema}.production_batches(batch_id),
        deviation_type VARCHAR(30), severity VARCHAR(10),
        description TEXT, investigation TEXT, root_cause TEXT,
        capa_actions TEXT, disposition VARCHAR(50),
        status VARCHAR(20), reported_date DATE, closed_date DATE,
        reported_by VARCHAR(30)
    )""",
    # ── 新增: 冷链储存 ──
    """CREATE TABLE IF NOT EXISTS {schema}.cold_storage_log (
        id SERIAL PRIMARY KEY,
        batch_id VARCHAR(20) REFERENCES {schema}.production_batches(batch_id),
        monitor_ts TIMESTAMPTZ, temp_c DECIMAL(4,1),
        humidity_pct DECIMAL(4,1), storage_location VARCHAR(30),
        alarm_flag BOOLEAN
    )""",
    # ── 新增: 运输监控 ──
    """CREATE TABLE IF NOT EXISTS {schema}.transport_monitoring (
        shipment_id VARCHAR(20) PRIMARY KEY,
        batch_id VARCHAR(20) REFERENCES {schema}.production_batches(batch_id),
        route_from VARCHAR(50), route_to VARCHAR(50),
        departure_time TIMESTAMPTZ, arrival_time TIMESTAMPTZ,
        vehicle_type VARCHAR(30),
        temp_min_c DECIMAL(4,1), temp_max_c DECIMAL(4,1),
        temp_excursion_count INT, temp_excursion_duration_min INT,
        mkt_c DECIMAL(5,2), shock_exceeded BOOLEAN,
        product_assessment VARCHAR(20)
    )""",
    # ── 新增: 物料库存 ──
    """CREATE TABLE IF NOT EXISTS {schema}.material_inventory (
        material_id VARCHAR(20) PRIMARY KEY,
        material_name VARCHAR(100), category VARCHAR(30),
        supplier VARCHAR(100), lot_number VARCHAR(30),
        current_stock INT, safety_stock INT, unit VARCHAR(20),
        date_received DATE, expiry_date DATE,
        storage_condition VARCHAR(50), status VARCHAR(20),
        unit_price_yuan DECIMAL(8,2)
    )""",
    # ── 新增: 批次物料消耗 BOM ──
    """CREATE TABLE IF NOT EXISTS {schema}.batch_material_usage (
        id SERIAL PRIMARY KEY,
        batch_id VARCHAR(20) REFERENCES {schema}.production_batches(batch_id),
        material_id VARCHAR(20) REFERENCES {schema}.material_inventory(material_id),
        planned_qty DECIMAL(8,2), actual_qty DECIMAL(8,2),
        unit VARCHAR(20), consumed_date DATE,
        operator VARCHAR(30), notes TEXT
    )""",
    # ── 新增: 仪器校准 ──
    """CREATE TABLE IF NOT EXISTS {schema}.equipment_calibration (
        equipment_id VARCHAR(20) PRIMARY KEY,
        equipment_name VARCHAR(100), category VARCHAR(30),
        location VARCHAR(50), serial_number VARCHAR(50),
        last_calibration_date DATE, next_calibration_date DATE,
        calibration_interval_months INT,
        calibration_result VARCHAR(10), calibrated_by VARCHAR(30)
    )""",
    # ── 新增: 人员培训 ──
    """CREATE TABLE IF NOT EXISTS {schema}.personnel_training (
        record_id SERIAL PRIMARY KEY,
        employee_name VARCHAR(30), department VARCHAR(30),
        role VARCHAR(30), training_topic VARCHAR(100),
        training_date DATE, expiry_date DATE,
        trainer VARCHAR(30), result VARCHAR(10)
    )""",
    # ── 新增: 洁净区环境 ──
    """CREATE TABLE IF NOT EXISTS {schema}.environmental_monitoring (
        record_id SERIAL PRIMARY KEY,
        monitor_date DATE, area VARCHAR(30), grade VARCHAR(5),
        location VARCHAR(50),
        particle_0_5um INT, particle_5_0um INT,
        viable_count_cfu INT, surface_count_cfu INT,
        temp_c DECIMAL(3,1), humidity_pct DECIMAL(4,1),
        pressure_diff_pa DECIMAL(5,1),
        pass_fail VARCHAR(10), monitored_by VARCHAR(30)
    )""",
    # ── 新增: 仓储温湿度 ──
    """CREATE TABLE IF NOT EXISTS {schema}.warehouse_monitoring (
        record_id SERIAL PRIMARY KEY,
        monitor_ts TIMESTAMPTZ, zone VARCHAR(30),
        temp_c DECIMAL(4,1), humidity_pct DECIMAL(4,1),
        temp_alarm BOOLEAN, humidity_alarm BOOLEAN,
        notes TEXT
    )""",
    # ── 新增: 仓储异常事件 ──
    """CREATE TABLE IF NOT EXISTS {schema}.storage_excursions (
        event_id VARCHAR(20) PRIMARY KEY,
        material_id VARCHAR(20) REFERENCES {schema}.material_inventory(material_id),
        event_date DATE, event_type VARCHAR(30),
        deviation_description TEXT,
        affected_qty INT, unit VARCHAR(20),
        temp_excursion_c DECIMAL(4,1), duration_hours DECIMAL(5,1),
        material_assessment VARCHAR(30),
        disposition VARCHAR(30), reported_by VARCHAR(30)
    )""",
    # ── 新增: 物料入厂检验 ──
    """CREATE TABLE IF NOT EXISTS {schema}.material_quality_inspection (
        inspection_id VARCHAR(20) PRIMARY KEY,
        material_id VARCHAR(20) REFERENCES {schema}.material_inventory(material_id),
        inspection_date DATE, test_item VARCHAR(50),
        result_value VARCHAR(50), spec_range VARCHAR(50),
        pass_fail VARCHAR(10), tested_by VARCHAR(30), notes TEXT
    )""",
    # ── 新增: AEFI 不良反应 ──
    """CREATE TABLE IF NOT EXISTS {schema}.aefi_reports (
        report_id VARCHAR(20) PRIMARY KEY,
        batch_id VARCHAR(20), vaccine_name VARCHAR(100),
        ae_term VARCHAR(100), ae_category VARCHAR(30),
        severity VARCHAR(10), onset_days INT,
        outcome VARCHAR(20), reported_date DATE,
        reporter VARCHAR(50)
    )""",
]

def create_schema_and_tables(conn):
    cur = conn.cursor()
    cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(SCHEMA)))
    for stmt in DDL_STATEMENTS:
        cur.execute(sql.SQL(stmt).format(schema=sql.Identifier(SCHEMA)))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO vlm_reader").format(sql.Identifier(SCHEMA)))
    cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO vlm_reader").format(sql.Identifier(SCHEMA)))
    cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO vlm_reader").format(sql.Identifier(SCHEMA)))
    print(f"✓ Schema {SCHEMA} + 22 张表创建完成，vlm_reader 已授权")


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def gauss(mean, std, decimals=2):
    v = random.gauss(mean, std)
    return round(v, decimals)

def batch_date(i):
    """批次 i (1-50) 的生产日期：2024-01 到 2025-12，约每15天一批"""
    base = date(2024, 1, 10)
    offset = (i - 1) * 15
    return base + timedelta(days=offset)

def batch_id(i):
    """生成批次号：2024年26批 + 2025年24批"""
    if i <= 26:
        return f"PEDV-2024-{i:04d}"
    else:
        return f"PEDV-2025-{i - 26:04d}"


# ═══════════════════════════════════════════════════════════
# 参考数据
# ═══════════════════════════════════════════════════════════

def insert_cell_seeds(conn):
    rows = [
        ("MCB-VERO-001", "VERO 主细胞库", "VERO", "MCB", 5, date(2018, 3, 15),
         95.5, 5000000, "PASS", "PASS", "PASS", "PASS", True),
        ("MCB-VERO-002", "VERO 主细胞库 (备)", "VERO", "MCB", 5, date(2018, 3, 15),
         94.8, 4800000, "PASS", "PASS", "PASS", "PASS", False),
        ("WCB-VERO-2020-01", "VERO 工作细胞库 2020", "VERO", "WCB", 10, date(2020, 6, 1),
         93.2, 5200000, "PASS", "PASS", "PASS", "PASS", False),
        ("WCB-VERO-2021-01", "VERO 工作细胞库 2021", "VERO", "WCB", 12, date(2021, 8, 20),
         94.1, 5100000, "PASS", "PASS", "PASS", "PASS", False),
        ("WCB-VERO-2023-01", "VERO 工作细胞库 2023", "VERO", "WCB", 14, date(2023, 2, 10),
         94.5, 5300000, "PASS", "PASS", "PASS", "PASS", True),
        ("WCB-VERO-2024-01", "VERO 工作细胞库 2024", "VERO", "WCB", 15, date(2024, 5, 15),
         95.0, 5400000, "PASS", "PASS", "PASS", "PASS", True),
        ("WCB-VERO-2025-01", "VERO 工作细胞库 2025", "VERO", "WCB", 16, date(2025, 3, 1),
         94.7, 5250000, "PASS", "PASS", "PASS", "PASS", True),
    ]
    cur = conn.cursor()
    cur.executemany(
        f"INSERT INTO {SCHEMA}.cell_seeds VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        rows,
    )
    print(f"✓ cell_seeds: {len(rows)} 行")

def insert_virus_seeds(conn):
    rows = [
        ("MVSS-PEDV-CV777-001", "PEDV CV777 主种子批", "CV777", "MVSS", 5,
         "Vero细胞分离→PK-15传3代→Vero悬浮适应→克隆纯化", date(2019, 6, 1),
         7.20, "PASS", "PASS", "PASS (IFA)", "PASS", True),
        ("MVSS-PEDV-AJ1102-001", "PEDV AJ1102 主种子批", "AJ1102", "MVSS", 4,
         "田间分离→PK-15传5代→Vero悬浮适应", date(2020, 3, 10),
         6.80, "PASS", "PASS", "PASS (IFA)", "PASS", False),
        ("WVSS-PEDV-CV777-F5", "PEDV CV777 工作种子批 F5", "CV777", "WVSS", 5,
         "MVSS → Vero悬浮传5代", date(2021, 9, 15),
         7.35, "PASS", "PASS", "PASS (IFA)", "PASS", True),
        ("WVSS-PEDV-CV777-F6", "PEDV CV777 工作种子批 F6", "CV777", "WVSS", 6,
         "MVSS → Vero悬浮传6代", date(2022, 6, 20),
         7.50, "PASS", "PASS", "PASS (IFA)", "PASS", True),
        ("WVSS-PEDV-CV777-F7", "PEDV CV777 工作种子批 F7", "CV777", "WVSS", 7,
         "MVSS → Vero悬浮传7代", date(2023, 12, 5),
         7.42, "PASS", "PASS", "PASS (IFA)", "PASS", True),
        ("WVSS-PEDV-CV777-F8", "PEDV CV777 工作种子批 F8", "CV777", "WVSS", 8,
         "MVSS → Vero悬浮传8代", date(2024, 10, 10),
         7.28, "PASS", "PASS", "PASS (IFA)", "PASS", True),
        ("WVSS-PEDV-AJ1102-F4", "PEDV AJ1102 工作种子批 F4", "AJ1102", "WVSS", 4,
         "MVSS → Vero悬浮传4代", date(2022, 3, 1),
         6.95, "PASS", "PASS", "PASS (IFA)", "PASS", False),
    ]
    cur = conn.cursor()
    cur.executemany(
        f"INSERT INTO {SCHEMA}.virus_seeds VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        rows,
    )
    print(f"✓ virus_seeds: {len(rows)} 行")

def insert_culture_media(conn):
    rows = [
        ("MED-G-001", "VP-SFM 生长培养基", "cell_growth", "Gibco", "LOT-G20230101",
         True, False, 5.50, 4.0, 7.1, 320, "PASS", 0.05, date(2023, 1, 15), 36),
        ("MED-G-002", "EX-CELL Vero 生长培养基", "cell_growth", "SAFC", "LOT-G20230615",
         True, True, 5.80, 4.5, 7.0, 310, "PASS", 0.03, date(2023, 6, 15), 36),
        ("MED-G-003", "CD-Vero 化学限定生长培养基", "cell_growth", "Gibco", "LOT-G20240110",
         True, True, 5.20, 5.0, 7.1, 305, "PASS", 0.02, date(2024, 1, 10), 36),
        ("MED-M-001", "VP-SFM 维持培养基", "virus_maintenance", "Gibco", "LOT-M20230201",
         True, False, 3.50, 2.5, 7.2, 300, "PASS", 0.04, date(2023, 2, 1), 36),
        ("MED-M-002", "EX-CELL Vero 维持培养基", "virus_maintenance", "SAFC", "LOT-M20230701",
         True, True, 3.80, 3.0, 7.1, 295, "PASS", 0.02, date(2023, 7, 1), 36),
        ("MED-M-003", "CD-Vero 化学限定维持培养基", "virus_maintenance", "Gibco", "LOT-M20240201",
         True, True, 3.20, 3.5, 7.1, 290, "PASS", 0.02, date(2024, 2, 1), 36),
    ]
    cur = conn.cursor()
    cur.executemany(
        f"INSERT INTO {SCHEMA}.culture_media VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        rows,
    )
    print(f"✓ culture_media: {len(rows)} 行")


# ═══════════════════════════════════════════════════════════
# 生产数据生成
# ═══════════════════════════════════════════════════════════

def generate_all(conn):
    cur = conn.cursor()

    for i in range(1, TOTAL_BATCHES + 1):
        bid = batch_id(i)
        d = batch_date(i)
        anomaly = ANOMALY_BATCHES.get(i)

        # ── 班组分配 — 3 个班组 ──
        if i <= 15:
            team = "A"
        elif i <= 35:
            team = "B"
        else:
            team = "C"

        # ── 规模分配 — 50L/200L/500L ──
        if i <= 8:
            scale = 50
        elif i <= 20:
            scale = 200
        elif i <= 24:
            scale = 50
        elif i <= 32:
            scale = 500  # 8批 500L
        else:
            scale = random.choices([200, 200, 500], weights=[4, 2, 4])[0]

        # ── MOI ──
        moi = round(random.uniform(0.04, 0.10), 2)
        if i >= 40:
            moi = round(moi + random.uniform(-0.03, 0.02), 2)  # 后期更分散

        # ── 培养基 ──
        growth_med = random.choice(["MED-G-001", "MED-G-002", "MED-G-003"])
        if growth_med == "MED-G-001":
            maint_med = "MED-M-001"
        elif growth_med == "MED-G-002":
            maint_med = "MED-M-002"
        else:
            maint_med = "MED-M-003"

        # ── 细胞种子 ──
        if i <= 12:
            cell_seed = "WCB-VERO-2023-01"
        elif i <= 30:
            cell_seed = "WCB-VERO-2024-01"
        else:
            cell_seed = "WCB-VERO-2025-01"

        # ── 病毒种子 ──
        vs_choices = ["WVSS-PEDV-CV777-F5", "WVSS-PEDV-CV777-F6", "WVSS-PEDV-CV777-F7", "WVSS-PEDV-CV777-F8"]
        vs_weights = [1, 2, 4, 3]
        virus_seed = random.choices(vs_choices, weights=vs_weights)[0]

        # ── 异常批次参数调整 ──
        if i == 16:  # 灭活失败
            scale, moi = 200, 0.08
        if i == 19:  # 低效价
            scale, moi = 200, 0.03
        if i == 38:  # 培养基变质
            growth_med = "MED-G-001"  # 受潮的批次
        if i == 42:  # 重复低效价
            moi = 0.04  # 违反新 SOP 的 MOI 下限 0.05

        harvest_d = d + timedelta(days=random.randint(12, 15))
        status = "completed"

        # ── 插入 production_batches ──
        notes = anomaly["description"] if anomaly else None
        cur.execute(
            f"INSERT INTO {SCHEMA}.production_batches VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (bid, "猪流行性腹泻灭活疫苗（悬浮培养）",
             cell_seed, virus_seed, growth_med, maint_med,
             scale, moi, d, d + timedelta(days=14), harvest_d,
             status, team, notes),
        )

        # ── 各工序 ──
        gen_cell_culture(cur, bid, i, scale)
        harvest_titer = gen_virus_culture(cur, bid, i, scale)
        gen_harvest(cur, bid, i, scale, harvest_titer, d, harvest_d)
        purity, antigen = gen_semi_product(cur, bid, i)
        gen_in_process_tests(cur, bid, i, d, harvest_d)
        release = gen_final_qc(cur, bid, i, d)
        gen_deviations(cur, bid, i, d, anomaly, release)
        gen_cold_storage_log(cur, bid, i, d)
        gen_transport_monitoring(cur, bid, i, d)
        gen_batch_material_usage(cur, bid, i, d)

        print(f"  ✓ [{i:2d}/50] {bid} ({d} | {scale}L | {team}班 | {release})")

    conn.commit()
    print(f"\n✓ {TOTAL_BATCHES} 批次生产数据生成完成")


# ═══════════════════════════════════════════════════════════
# 各工序生成函数
# ═══════════════════════════════════════════════════════════

def gen_cell_culture(cur, bid, i, scale):
    days = random.randint(5, 7)
    density = gauss(1.5, 0.3, 3)
    viability = 95.0
    ph = 7.15
    do = 55.0

    for day in range(days + 1):
        if day == 0:
            cur.execute(
                f"INSERT INTO {SCHEMA}.cell_culture_log (batch_id,culture_day,cell_density_10e6_ml,viability_pct,ph,do_pct,temp_c,glucose_g_per_l,lactate_g_per_l,ammonia_mm,osmolality_mosm,agitation_rpm) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (bid, day, density, viability, ph, do, 37.0, 5.50, 0.10, 0.50, 320, 120),
            )
            continue

        growth_rate = random.uniform(1.2, 1.8) if day <= 3 else random.uniform(0.75, 1.1)
        density = round(density * growth_rate, 3)
        max_density = gauss(7.0, 1.0, 3)  # 更大离散
        density = min(density, max_density)

        # DO 控制
        if i == 28:  # DO异常批次
            do = gauss(30, 6, 1)
        elif scale == 500:
            do = gauss(52 - day * 3, 5, 1)  # 大规模 DO 控制略差
        else:
            do = gauss(55 - day * 2, 4, 1)

        viability = gauss(95 - day * 1.5, 2.0, 2)
        ph = gauss(7.1, 0.12, 1)

        # pH异常
        if i == 27 and day == 3:
            ph = 6.52
            note_text = "pH 异常下降至 6.52，CO₂流量计故障"
        else:
            note_text = None

        glucose = gauss(5.50 - day * 0.6, 0.4, 3)
        lactate = gauss(0.10 + day * 0.5, 0.2, 3)
        ammonia = gauss(0.50 + day * 0.3, 0.15, 2)
        osmo = int(gauss(320 + day * 5, 10))

        cur.execute(
            f"INSERT INTO {SCHEMA}.cell_culture_log (batch_id,culture_day,cell_density_10e6_ml,viability_pct,ph,do_pct,temp_c,glucose_g_per_l,lactate_g_per_l,ammonia_mm,osmolality_mosm,agitation_rpm,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (bid, day, density, viability, ph, do, gauss(37.0, 0.3, 1),
             glucose, lactate, ammonia, osmo, int(gauss(120, 5)), note_text),
        )


def gen_virus_culture(cur, bid, i, scale):
    dpi_max = random.randint(4, 5)
    cpe = 0.0
    viability = 90.0
    density = gauss(5.5, 0.8, 3)
    peak_titer = 0.0

    for dpi in range(dpi_max + 1):
        if dpi == 0:
            cur.execute(
                f"INSERT INTO {SCHEMA}.virus_culture_log (batch_id,dpi,cpe_pct,cell_density_10e6_ml,viability_pct,ph,do_pct,glucose_g_per_l,lactate_g_per_l,sample_titer_tcid50) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (bid, dpi, 0.0, density, viability, 7.15, 55.0, 5.20, 0.80, None),
            )
            continue

        cpe = gauss(dpi * 22, 7, 1)
        cpe = min(cpe, 92.0)
        viability = gauss(90 - dpi * 12, 5, 2)
        density = round(density * random.uniform(0.78, 0.95), 3)

        if dpi <= 3:
            titer = 6.0 + dpi * 0.5 + random.uniform(-0.4, 0.4)
        else:
            titer = 7.5 - (dpi - 3) * 0.2 + random.uniform(-0.4, 0.4)

        if i == 16:
            titer += random.uniform(0.5, 0.8)
        if i == 28:
            titer -= random.uniform(0.5, 0.8)
        if i == 19:
            titer -= random.uniform(0.4, 0.7)
        if i == 42:
            titer -= random.uniform(0.3, 0.5)
        if i == 38:  # 培养基变质→效价偏低
            titer -= random.uniform(0.3, 0.5)

        titer = round(titer, 2)
        if titer > peak_titer:
            peak_titer = titer

        ph = gauss(7.1 - dpi * 0.1, 0.2, 1)
        do_val = gauss(50 - dpi * 4, 5, 1)
        if i == 28 and dpi >= 2:
            do_val = gauss(25, 5, 1)

        cur.execute(
            f"INSERT INTO {SCHEMA}.virus_culture_log (batch_id,dpi,cpe_pct,cell_density_10e6_ml,viability_pct,ph,do_pct,glucose_g_per_l,lactate_g_per_l,sample_titer_tcid50) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (bid, dpi, cpe, density, viability, ph, do_val,
             gauss(5.2 - dpi * 1.0, 0.4, 3),
             gauss(0.8 + dpi * 0.6, 0.3, 3),
             titer),
        )
    return round(peak_titer + random.uniform(-0.15, 0.25), 2)


def gen_harvest(cur, bid, i, scale, harvest_titer, batch_d, harvest_d):
    record_id = f"HI-{bid}"
    harvest_vol = round(scale * random.uniform(0.86, 0.95), 2)
    pre_clarify = round(harvest_titer + random.uniform(-0.15, 0.1), 2)
    post_clarify = round(pre_clarify - random.uniform(0.05, 0.18), 2)
    post_vol = round(harvest_vol * random.uniform(0.88, 0.95), 2)

    if i == 16:
        bioburden_pre = random.randint(150, 200)
    elif i == 33:
        bioburden_pre = random.randint(80, 110)  # SOP偏离，偏高
    else:
        bioburden_pre = random.randint(2, 90)  # 更大范围
    bioburden_post = max(0, int(bioburden_pre * random.uniform(0.03, 0.25)))

    if i == 16:
        bei_conc = 1.2
    else:
        bei_conc = round(random.uniform(1.4, 2.8), 3)

    inactivation_dur = random.uniform(28, 44)
    inactiv_complete = harvest_d + timedelta(days=2)
    conc_factor = round(random.uniform(7, 13), 2)
    post_conc_vol = round(post_vol / conc_factor, 2)

    residual_test = "FAIL" if i == 16 else "PASS"

    cur.execute(
        f"INSERT INTO {SCHEMA}.harvest_inactivation VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (record_id, bid, harvest_d, harvest_vol, pre_clarify,
         "depth_filtration", post_vol, post_clarify,
         bioburden_pre, bioburden_post,
         "BEI", bei_conc, 37.0, inactivation_dur,
         round(pre_clarify - 0.05, 2),
         residual_test, inactiv_complete, conc_factor, post_conc_vol),
    )


def gen_semi_product(cur, bid, i):
    semi_id = f"SP-{bid}"

    if i == 19:
        antigen = gauss(28, 4, 2)
        purity = gauss(91, 2, 2)
    elif i == 28:
        antigen = gauss(33, 4, 2)
        purity = gauss(89, 2, 2)
    elif i == 42:
        antigen = gauss(30, 3, 2)
        purity = gauss(90, 2, 2)
    elif i == 16:
        antigen = gauss(42, 4, 2)
        purity = gauss(92, 2, 2)
    elif i == 38:
        antigen = gauss(33, 4, 2)
        purity = gauss(88, 2, 2)
    elif i == 40:
        antigen = gauss(45, 3, 2)  # 佐剂问题不影响抗原本身
        purity = gauss(85, 3, 2)   # 但乳化失败影响纯度判定
    else:
        antigen = gauss(43, 5, 2)  # 更大离散
        purity = gauss(94, 3, 2)

    purity = min(purity, 98.5)
    sterility = "PASS"
    inact_verify = "FAIL" if i == 16 else "PASS"
    endotoxin = gauss(2.5, 1.2, 2) if i != 16 else gauss(9.0, 2.5, 2)
    vol = round(random.uniform(3.0, 6.0), 2)

    # 佐剂乳化失败
    if i == 40:
        appearance = "油相/水相分离，不合格"
    else:
        appearance = "微乳白色液体，无异物"

    cur.execute(
        f"INSERT INTO {SCHEMA}.semi_product VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (semi_id, bid, vol, antigen,
         gauss(0.85, 0.12, 3), purity, endotoxin, sterility,
         gauss(7.1, 0.15, 1), appearance,
         inact_verify, "ISA 206 油佐剂", gauss(50, 3, 2)),
    )
    return purity, antigen


def gen_in_process_tests(cur, bid, i, batch_d, harvest_d):
    testers = ["张某", "李某", "王某", "赵某", "陈某"]
    sample_points = [
        ("pre_inoc", batch_d, ["sterility", "mycoplasma", "ph", "glucose"]),
        ("mid_culture", batch_d + timedelta(days=3), ["sterility", "bioburden", "ph", "osmolality"]),
        ("pre_harvest", harvest_d - timedelta(days=1), ["bioburden", "endotoxin", "titer", "ph", "protein"]),
        ("post_clarify", harvest_d, ["bioburden", "endotoxin", "titer"]),
        ("post_inactivation", harvest_d + timedelta(days=2), ["sterility", "endotoxin", "titer"]),
    ]

    for sp, d, test_types in sample_points:
        for tt in test_types:
            if tt == "sterility":
                if i == 8 and sp == "mid_culture":
                    result_val, pass_fail = "Positive (1/3)", "FAIL"
                    note_text = "1/3 瓶检出阳性，疑似操作污染（复检阴性）"
                else:
                    result_val, pass_fail = "Negative (0/3)", "PASS"
                    note_text = None
            elif tt == "mycoplasma":
                if i == 12 and sp == "mid_culture":
                    result_val, pass_fail = "Positive (DNA stain)", "FAIL"
                    note_text = "DNA荧光染色阳性，培养法确认为阴性（假阳性）"
                elif i == 45 and sp == "mid_culture":
                    result_val, pass_fail = "Positive (DNA stain + Culture)", "FAIL"
                    note_text = "DNA染色+培养法双阳性，确认为支原体污染"
                else:
                    result_val, pass_fail = "Negative", "PASS"
                    note_text = None
            elif tt == "bioburden":
                if i == 16 and sp == "pre_harvest":
                    val, pass_fail = 180, "FAIL"
                    note_text = "微生物限度超标（标准≤100 CFU）"
                elif i == 33 and sp == "pre_harvest":
                    val, pass_fail = random.randint(85, 110), "PASS" if val <= 100 else "FAIL"
                    note_text = "未按SOP检测，事后补测" if val > 100 else "事后补测，接近上限"
                elif i == 28 and sp == "pre_harvest":
                    val, pass_fail = random.randint(50, 95), "PASS"
                    note_text = None
                else:
                    val = random.randint(0, 95)
                    result_val, pass_fail = str(val), "PASS"
                    note_text = None
                    cur.execute(
                        f"INSERT INTO {SCHEMA}.in_process_tests (batch_id,sample_point,test_type,test_date,result_value,spec_min,spec_max,pass_fail,tested_by,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (bid, sp, tt, d, str(val), "0", "100", pass_fail,
                         random.choice(testers), note_text),
                    )
                    continue
                result_val, pass_fail = str(val), "FAIL" if val > 100 else "PASS"
            elif tt == "endotoxin":
                val = gauss(2.5, 1.5, 2)
                if i == 16:
                    val = gauss(9.0, 2.5, 2)
                result_val, pass_fail = f"{val} EU/mL", "PASS" if val <= 10 else "FAIL"
                note_text = None
            elif tt == "titer":
                val = gauss(7.3, 0.5, 2)
                if i == 19 or i == 42:
                    val = gauss(6.3, 0.3, 2)
                result_val, pass_fail = f"{val} log10 TCID50/mL", "PASS"
                note_text = None
            elif tt == "ph":
                if i == 27 and sp == "mid_culture":
                    val, pass_fail = 6.52, "FAIL"
                    note_text = "pH 低于下限 6.8"
                else:
                    val, pass_fail = gauss(7.1, 0.2, 1), "PASS"
                    note_text = None
                result_val = str(val)
            elif tt == "glucose":
                result_val, pass_fail = f"{gauss(5.0, 0.6, 2)} g/L", "PASS"
                note_text = None
            elif tt == "osmolality":
                result_val, pass_fail = f"{int(gauss(320, 12))} mOsm", "PASS"
                note_text = None
            elif tt == "protein":
                result_val, pass_fail = f"{gauss(0.9, 0.15, 3)} mg/mL", "PASS"
                note_text = None
            else:
                result_val, pass_fail = "N/A", "PASS"
                note_text = None

            cur.execute(
                f"INSERT INTO {SCHEMA}.in_process_tests (batch_id,sample_point,test_type,test_date,result_value,spec_min,spec_max,pass_fail,tested_by,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (bid, sp, tt, d, result_val,
                 _spec_min(tt), _spec_max(tt), pass_fail,
                 random.choice(testers), note_text),
            )


def _spec_min(test_type):
    specs = {"sterility": "Negative", "mycoplasma": "Negative", "bioburden": "0",
             "endotoxin": "0", "titer": "6.5", "ph": "6.8", "glucose": "1.0",
             "osmolality": "280", "protein": "0.5"}
    return specs.get(test_type, None)

def _spec_max(test_type):
    specs = {"sterility": "Negative", "mycoplasma": "Negative", "bioburden": "100",
             "endotoxin": "10", "titer": None, "ph": "7.4", "glucose": "8.0",
             "osmolality": "360", "protein": "2.0"}
    return specs.get(test_type, None)


def gen_final_qc(cur, bid, i, batch_d):
    qc_id = f"QC-{bid}"
    test_d = batch_d + timedelta(days=random.randint(28, 42))

    # 效价 — 更大离散度
    if i == 16:
        potency, efficacy_val, release = gauss(40, 3, 2), "FAIL", "rejected"
    elif i == 19:
        potency, efficacy_val, release = 28.0, "PASS", "conditional"
    elif i == 42:
        potency, efficacy_val, release = 30.0, "PASS", "conditional"
    elif i == 40:
        potency, efficacy_val, release = gauss(42, 3, 2), "N/A", "rejected"  # 佐剂失败
    elif i == 45:
        potency, efficacy_val, release = gauss(38, 3, 2), "FAIL", "rejected"
    elif i == 38:
        potency, efficacy_val, release = gauss(32, 2, 2), "PASS", "released"  # 边缘值
        potency = min(potency, 34)
    elif i == 28:
        potency, efficacy_val, release = gauss(34, 3, 2), "PASS", "released"
    else:
        potency = gauss(39, 5, 2)
        potency = max(potency, 31.0)  # 偶尔有边缘值
        efficacy_val, release = "PASS", "released"

    # 灌装异常
    if i == 47:
        filling_vol = gauss(2.05, 0.25, 2)  # RSO 大
    else:
        filling_vol = gauss(2.05, 0.04, 2)

    endotoxin = gauss(3.0, 1.8, 2)
    ph = gauss(7.1, 0.15, 1)

    # 效期：生产日期 + 18个月
    expiry = batch_d + timedelta(days=540)

    cur.execute(
        f"INSERT INTO {SCHEMA}.final_product_qc VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (qc_id, bid, test_d, "微乳白色液体", ph,
         "PASS" if i != 45 else "FAIL", endotoxin,
         potency, round(potency * 0.28, 2),
         "PASS", "PASS", efficacy_val,
         gauss(0.001, 0.0006, 4), gauss(2.5, 1.5, 2),
         gauss(0.85, 0.12, 2), gauss(0.42, 0.06, 3),
         filling_vol, release,
         random.choice(["周主任", "刘主任", "马主任"]),
         expiry),
    )
    return release


def gen_deviations(cur, bid, i, batch_d, anomaly, release):
    if anomaly is None:
        return

    dev_id = f"DEV-{bid}"
    atype = anomaly["type"]
    severity = anomaly["severity"]

    dev_data = {
        "sterility_false_positive": (
            "环境/操作", "次要",
            "过程控制无菌检查阳性（mid_culture，1/3瓶），疑似操作污染",
            "调查当日环境监测记录（粒子/浮游菌均正常）、操作人员更衣记录（符合SOP）、培养基灵敏度测试（合格）。同日 mycoplasma 和 bioburden 均正常。复检3瓶全部阴性。结论：操作过程引入的假阳性。",
            "取样操作不规范，手套接触了取样口外壁",
            "1. 加强无菌取样操作培训\n2. 修订 SOP-QC-0012 增加取样前手套消毒步骤",
            "产品正常放行", batch_d + timedelta(days=10), batch_d + timedelta(days=25), "李某",
        ),
        "mycoplasma_false_positive": (
            "检验", "次要",
            "过程控制支原体检查阳性（DNA荧光染色法），后经培养法确认阴性",
            "DNA 荧光染色法灵敏度高但特异性有限。同时送检的培养法（仲裁方法）结果为阴性。该批后续半成品及成品均无菌/支原体阴性。结论：DNA 染色法假阳性。",
            "DNA 荧光染色试剂盒批次差异导致非特异性结合",
            "1. 该试剂盒批次停用，退回供应商\n2. 支原体检查增加培养法作为确证方法\n3. 修订 SOP-QC-0028",
            "产品正常放行", batch_d + timedelta(days=12), batch_d + timedelta(days=28), "王某",
        ),
        "inactivation_failure": (
            "工艺", "重大",
            "BEI 灭活后细胞接种法检出活病毒（第3代出现CPE），灭活验证失败",
            "对灭活工艺参数全面回顾：(1)灭活前 bioburden=180 CFU（标准≤100），有机负荷偏高；(2)BEI浓度=1.2mM（公司标准1-3mM，处于下限）；(3)收获效价=8.3 log10（异常偏高）。综合判断：高生物负荷消耗了部分BEI，剩余BEI不足以完全灭活高滴度病毒。",
            "灭活前 bioburden 超标 + BEI 浓度偏低（1.2mM）+ 收获病毒量异常高（8.3 log10），三者叠加导致灭活不彻底",
            "1. BEI 浓度标准调整为 1.5-3.0mM（最低 1.5mM）\n2. 灭活前增加 bioburden 检测作为必检项（≥100 CFU 时调整 BEI 加量）\n3. 该批全部产品报废处理\n4. 修订 SOP-MFG-0089 灭活工艺章节\n5. 对 BEI 供应商进行审计",
            "整批报废", batch_d + timedelta(days=20), batch_d + timedelta(days=45), "赵某",
        ),
        "low_potency": (
            "工艺", "主要",
            f"成品 potency_elisa=28 U，低于放行标准（≥32 U），OOS",
            "回顾整个生产工艺：细胞培养阶段正常（峰值密度 5.8×10⁶/mL），病毒培养阶段效价增长缓慢（DPI=4 时仅 6.5 log10），半成品抗原含量偏低（28 U/mL）。可能原因：该批 MOI=0.03（偏低）导致病毒增殖不充分。",
            "MOI 偏低（0.03）+ 种病毒批次差异导致病毒增殖动力学延迟",
            "1. MOI 下限调整为 0.05\n2. 增加 DPI=2 时病毒效价监测\n3. 该批有条件放行（仅限紧急使用）\n4. 启动稳定性加测（25°C 6个月）",
            "有条件放行", batch_d + timedelta(days=30), batch_d + timedelta(days=50), "张某",
        ),
        "env_excursion": (
            "环境", "主要",
            "洁净区灌装间(grade A)粒子计数超标（0.5μm: 4200/m³，标准≤3520），灌装暂停2h",
            "检查HVAC系统发现初中效过滤器压差异常升高，更换过滤器后粒子计数恢复正常。期间灌装作业暂停2h，未灌装半成品已密封保存。灌装间重新清洁消毒后恢复生产。",
            "HVAC初中效过滤器堵塞→换气次数下降→粒子累积超标",
            "1. 更换初中效过滤器\n2. HVAC压差监控增加预警（压差>初始值1.5倍时自动提示更换）\n3. 修订SOP-ENV-0003增加过滤器更换频率",
            "产品正常放行", batch_d + timedelta(days=5), batch_d + timedelta(days=15), "陈某",
        ),
        "equipment_failure": (
            "设备", "主要",
            "管式离心机#2 运行中振动超标停机，导致澄清工序延迟6h",
            "检查发现离心机#2 轴承磨损严重，转鼓不平衡导致振动超标。切换至备用离心机#3完成澄清。维修#2：更换轴承+动平衡校正。",
            "轴承达到使用寿命（累计运行12000h，建议更换周期10000h）",
            "1. 建立设备关键部件预防性更换计划（按运行小时）\n2. 增加每月振动监测\n3. 备用离心机#3 纳入定期试运行计划",
            "产品正常放行", batch_d + timedelta(days=7), batch_d + timedelta(days=20), "王某",
        ),
        "ph_anomaly_2025": (
            "工艺", "次要",
            "细胞培养 Day3 pH 突降至 6.52（正常范围 6.8-7.4），持续约 8h 后自行恢复",
            "检查供气系统 CO₂ 流量记录：Day3 上午 CO₂ 流量计短暂故障导致 CO₂ 过量注入。维修后恢复正常。细胞活力从 93% 降至 87%，后续恢复至 90%。成品效价 37U（合格）。",
            "CO₂ 流量控制器短暂故障导致 pH 异常",
            "1. CO₂ 流量计增加定期校准频次（每季度→每月）\n2. 增加 pH 在线监测报警（pH<6.7 即时通知）",
            "产品正常放行", batch_d + timedelta(days=8), batch_d + timedelta(days=22), "李某",
        ),
        "do_anomaly_2025": (
            "工艺/设备", "主要",
            "病毒培养阶段 DO 持续偏低（最低 22%，正常 30-60%），细胞密度峰值仅 3.8×10⁶/mL，收获效价偏低（6.5 log10）",
            "检查发现生物反应器#3 的纯氧供应管路有轻微泄漏，导致氧传质效率下降。修复后验证 DO 控制恢复正常。但本批细胞生长已受影响。成品 potency_elisa=34U（低空飞过）。",
            "生物反应器供氧管路泄漏 → 氧传质不足 → 细胞生长受限 → 病毒产量下降",
            "1. 修复生物反应器#3 供氧管路\n2. 增加每周一次的供气管路压力测试\n3. 完善 DO 异常 SOP 应急流程（DO<30% 时切换备用供氧管路）",
            "产品放行（效价合格，但属边缘值）", batch_d + timedelta(days=15), batch_d + timedelta(days=35), "王某",
        ),
        "operator_error": (
            "人员/操作", "次要",
            "操作员未按 SOP-MFG-0012 规定在灭活前检测 bioburden",
            "QA 审核批记录时发现缺失灭活前 bioburden 检测结果。启动偏差调查：操作员承认疏忽，事后补测发现 bioburden=95 CFU（接近100 CFU上限）。灭活验证结果正常（PASS），产品风险评估为低风险。",
            "操作员培训不足 + 批记录审核节点缺失",
            "1. 该操作员重新培训 SOP-MFG-0012 并考核\n2. 批记录增加灭活前 bioburden 检测的强制确认栏\n3. QA 审核增加关键检测项完整性检查清单",
            "产品正常放行", batch_d + timedelta(days=18), batch_d + timedelta(days=30), "张某",
        ),
        "reagent_expired": (
            "检验/仓储", "主要",
            "ELISA检测试剂盒过期2天仍在使用，导致 potency 读数异常偏高(52 U)",
            "复检使用新试剂盒：potency=38 U（合格）。根因追溯：仓储未建立试剂效期预警机制，检验员使用前未核对效期标签。库存盘点发现另有3种试剂已过期未处理。",
            "仓储效期管理缺失 + 检验员未核对效期",
            "1. 立即盘点所有试剂效期，过期试剂报废\n2. 建立电子化效期预警系统（到期前30天/7天两级提醒）\n3. SOP-QC-0001 增加\"使用前核对效期\"步骤\n4. 过期试剂批次检验结果全部复检",
            "产品正常放行（复检确认合格）", batch_d + timedelta(days=32), batch_d + timedelta(days=45), "李某",
        ),
        "media_degraded": (
            "物料/仓储", "主要",
            "生长培养基 MED-G-001 受潮结块，细胞生长缓慢，峰值密度仅4.2×10⁶/mL",
            "调查发现：(1)仓储温湿度记录显示7月湿度多次超过85%；(2)该培养基批次 LOT-G20230101 包装密封性因高湿受损；(3)物料入厂检验仅做无菌/内毒素，未做物理性状检查。追踪后续批次：使用该培养基的3批中有2批效价偏低。",
            "仓储湿度超标（warehouse_monitoring 记录7月湿度>85%）→ 培养基包装密封性破坏 → 受潮变质 → 细胞生长不良 → 效价偏低",
            "1. 培养基仓库增加除湿机，湿度控制在<60%\n2. 物料入厂检验增加外观/物理性状检查\n3. 该批次培养基全部报废\n4. 追溯使用该培养基的3个批次进行稳定性加测\n5. 修订仓储SOP-WH-0005增加湿度超限应急处理流程",
            "产品放行（效价32U，边缘值，需加强稳定性监测）", batch_d + timedelta(days=25), batch_d + timedelta(days=40), "赵某",
        ),
        "adjuvant_failure": (
            "物料/工艺", "重大",
            "ISA 206 佐剂乳化失败，油相/水相分离，半成品报废",
            "调查发现冷库#3 温控系统于2025-08-15发生故障，库温升至18°C持续6h（warehouse_monitoring记录）。该冷库存放ISA 206佐剂批次 LOT-ADJ-20250115（storage_excursions记录）。佐剂经高温后物理性质改变，乳化时无法形成稳定油包水结构。入厂检验未涵盖温度耐受性测试。",
            "冷库#3 温控故障 → 库温18°C持续6h → ISA 206佐剂物理性质改变 → 乳化失败 → 半成品报废",
            "1. 冷库#3 增加备用制冷机组和温度超限短信报警\n2. ISA 206 佐剂储存温度要求从\"2-8°C\"改为\"2-8°C（温度波动≤±2°C）\"\n3. 该批次佐剂全部报废\n4. 佐剂入厂检验增加乳化模拟测试\n5. 对冷库温控系统进行全面检查",
            "整批半成品报废", batch_d + timedelta(days=22), batch_d + timedelta(days=48), "王某",
        ),
        "low_potency_repeat": (
            "工艺", "主要",
            "第二次效价不达标(potency=30 U)，与 PEDV-2024-0019 同属 MOI 偏低导致的低效价偏差",
            "对比前次低效价偏差(PEDV-2024-0019)的CAPA措施：MOI下限已调整为0.05。但本批实际MOI=0.04（操作员未按新SOP执行）。说明CAPA措施虽已制定但执行层面未落实。",
            "CAPA执行不到位：MOI下限已调整为0.05，但本批仍用0.04",
            "1. 对操作班组进行 MOI 新标准专项培训+考核\n2. 生物反应器控制系统增加 MOI 下限硬限制（<0.05 时无法启动接种程序）\n3. 启动CAPA有效性回顾审查\n4. 该批有条件放行",
            "有条件放行", batch_d + timedelta(days=35), batch_d + timedelta(days=55), "张某",
        ),
        "contamination_true": (
            "人员/环境", "重大",
            "支原体真阳性（DNA染色+培养法双确认），整批报废",
            "调查追溯：操作人员更衣记录显示当日1名操作员未按SOP要求更换洁净服鞋。环境监测记录显示同日更衣间粒子计数偏高。该操作员此前在普通区处理过未灭活样品。结论：人员从普通区带入支原体污染。",
            "操作人员更衣不规范，从普通区带入支原体污染",
            "1. 加强更衣SOP培训和考核（所有进入洁净区人员）\n2. 增加更衣间粒子/微生物监测频次\n3. 修订进入洁净区流程：增加紫外消毒过渡间停留时间\n4. 该批全部报废\n5. 生物反应器#5 进行甲醛熏蒸消毒",
            "整批报废", batch_d + timedelta(days=10), batch_d + timedelta(days=35), "赵某",
        ),
        "filling_error": (
            "设备", "主要",
            "灌装机#1 装量偏差超限（目标2.0mL，实际1.82-2.35mL，RSO 8.5%），约300瓶需剔除",
            "检查发现灌装机#1 陶瓷泵密封圈磨损，导致每次灌装量不一致。更换密封圈后复测：RSO=1.2%。剔除装量不合格的300瓶，其余批次正常放行。",
            "陶瓷泵密封圈磨损（累计灌装50万瓶，建议更换周期40万瓶）",
            "1. 更换灌装机#1 全部陶瓷泵密封圈\n2. 建立密封圈预防性更换计划（每40万瓶或每6个月）\n3. 灌装过程增加每30分钟装量检查\n4. 修订SOP-FIL-0002",
            "剔除不合格品后放行", batch_d + timedelta(days=28), batch_d + timedelta(days=40), "陈某",
        ),
        "cold_chain_break": (
            "物流/冷链", "主要",
            "运输途中冷藏车制冷机组故障3h，温度升至22°C",
            "车辆行驶至中途制冷机组皮带断裂，温度在2h内从4°C升至22°C。驾驶员联系维修后于3h后恢复制冷。产品到达后评估：运输全段MKT=12.3°C（标准MKT≤8°C）。启动稳定性加测：25°C加速7天 potency下降8%（正常<5%），需降级处理。",
            "冷藏车制冷机组皮带断裂→3h无制冷→温度升至22°C→产品热暴露",
            "1. 该批产品降级放行（效期从18个月缩短至12个月，标签加注\"冷链异常\"）\n2. 冷藏车增加双制冷机组冗余\n3. 运输过程温度报警升级为实时短信通知\n4. 修订运输SOP：>15°C超过2h的产品需做动物效力试验",
            "降级放行（缩短效期至12个月）", batch_d + timedelta(days=42), batch_d + timedelta(days=55), "李某",
        ),
    }

    data = dev_data.get(atype)
    if data is None:
        return
    dev_type, dev_severity, desc, investigation, root_cause, capa, disposition, reported, closed, reporter = data

    # 使用 anomaly 中定义的 severity
    cur.execute(
        f"INSERT INTO {SCHEMA}.deviations VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (dev_id, bid, dev_type, severity, desc, investigation, root_cause, capa, disposition, "已关闭", reported, closed, reporter),
    )


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — 冷链储存温度
# ═══════════════════════════════════════════════════════════

def gen_cold_storage_log(cur, bid, i, batch_d):
    """每批生成30天成品冷库储存温度记录 (2-8°C)"""
    locations = ["冷库-A区", "冷库-B区", "冷库-C区"]
    location = random.choice(locations)
    start_date = batch_d + timedelta(days=35)

    for day in range(30):
        ts = datetime.combine(
            start_date + timedelta(days=day),
            datetime.min.time().replace(hour=random.randint(0, 23)))

        if i == 50 and day >= 20:
            temp = gauss(6.5, 2.5, 1)
        elif random.random() < 0.03:
            temp = gauss(9.5, 2.0, 1)
        else:
            temp = gauss(4.5, 1.2, 1)

        humidity = gauss(45, 8, 1)
        alarm = temp < 1.5 or temp > 8.5

        cur.execute(
            f"INSERT INTO {SCHEMA}.cold_storage_log (batch_id,monitor_ts,temp_c,humidity_pct,storage_location,alarm_flag) VALUES (%s,%s,%s,%s,%s,%s)",
            (bid, ts, temp, humidity, location, alarm),
        )


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — 运输监控
# ═══════════════════════════════════════════════════════════

def gen_transport_monitoring(cur, bid, i, batch_d):
    """每批生成1条运输监控记录"""
    routes = [
        ("江苏南京工厂", "山东省畜牧兽医局"),
        ("江苏南京工厂", "河南省疾控中心"),
        ("江苏南京工厂", "广东省动物疫苗储备库"),
        ("江苏南京工厂", "四川省动物疫控中心"),
        ("江苏南京工厂", "河北省畜牧站"),
    ]
    route_from, route_to = random.choice(routes)
    ship_date = batch_d + timedelta(days=random.randint(38, 48))
    arr_date = ship_date + timedelta(hours=random.randint(8, 36))
    vehicle = random.choice(["冷藏车-苏A001", "冷藏车-苏A002", "冷藏车-苏B001"])

    if i == 50:
        temp_min, temp_max = 2.0, 22.0
        exc_count, exc_dur = 1, 180
        mkt = 12.3
        shock = False
        assessment = "降级放行"
    else:
        temp_min = gauss(2.5, 1.0, 1)
        temp_max = gauss(7.0, 1.5, 1)
        if temp_max > 8.5:
            exc_count = random.randint(1, 3)
            exc_dur = random.randint(10, 60)
            mkt = gauss(6.5, 1.5, 2)
        else:
            exc_count = 0
            exc_dur = 0
            mkt = gauss(4.5, 1.0, 2)
        shock = random.random() < 0.05
        assessment = "正常放行"

    shipment_id = f"SHIP-{bid}"
    cur.execute(
        f"INSERT INTO {SCHEMA}.transport_monitoring VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (shipment_id, bid, route_from, route_to, ship_date, arr_date,
         vehicle, temp_min, temp_max, exc_count, exc_dur, mkt, shock, assessment),
    )


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — 物料库存 (~40种)
# ═══════════════════════════════════════════════════════════

def gen_material_inventory(conn):
    """生成 ~40 种物料库存记录（4大类：培养基/化学试剂/佐剂/包材）"""
    cur = conn.cursor()
    materials = [
        # ── 培养基 (8) ──
        ("MAT-MED-001", "VP-SFM 生长培养基 (干粉)", "培养基", "Gibco", "LOT-G20230101",
         5, 8, "瓶", date(2023, 1, 15), date(2026, 1, 15), "常温干燥", "受潮变质", 2850.00),
        ("MAT-MED-002", "EX-CELL Vero 生长培养基", "培养基", "SAFC", "LOT-G20230615",
         12, 8, "瓶", date(2023, 6, 15), date(2026, 6, 15), "常温干燥", "正常", 3200.00),
        ("MAT-MED-003", "CD-Vero 化学限定生长培养基", "培养基", "Gibco", "LOT-G20240110",
         8, 8, "瓶", date(2024, 1, 10), date(2027, 1, 10), "2-8°C", "正常", 4200.00),
        ("MAT-MED-004", "VP-SFM 维持培养基 (干粉)", "培养基", "Gibco", "LOT-M20230201",
         6, 8, "瓶", date(2023, 2, 1), date(2026, 2, 1), "常温干燥", "正常", 2650.00),
        ("MAT-MED-005", "EX-CELL Vero 维持培养基", "培养基", "SAFC", "LOT-M20230701",
         10, 8, "瓶", date(2023, 7, 1), date(2026, 7, 1), "常温干燥", "正常", 2980.00),
        ("MAT-MED-006", "CD-Vero 化学限定维持培养基", "培养基", "Gibco", "LOT-M20240201",
         7, 8, "瓶", date(2024, 2, 1), date(2027, 2, 1), "2-8°C", "正常", 3800.00),
        ("MAT-MED-007", "UltraCULTURE 血清替代物", "培养基", "Lonza", "LOT-US20240301",
         3, 5, "瓶", date(2024, 3, 1), date(2025, 9, 1), "-20°C", "临近效期", 5600.00),
        ("MAT-MED-008", "青链霉素双抗溶液 (100x)", "培养基", "Gibco", "LOT-AB20240101",
         15, 10, "瓶", date(2024, 1, 1), date(2026, 1, 1), "-20°C", "正常", 850.00),
        # ── 化学试剂 (10) ──
        ("MAT-REAG-001", "BEI (二乙烯亚胺) 原液", "化学试剂", "Sigma", "LOT-BEI20240115",
         2, 3, "L", date(2024, 1, 15), date(2025, 7, 15), "2-8°C 避光", "正常", 12000.00),
        ("MAT-REAG-002", "PBS 缓冲液 (10x)", "化学试剂", "Gibco", "LOT-PBS20240301",
         20, 15, "L", date(2024, 3, 1), date(2026, 3, 1), "常温", "正常", 380.00),
        ("MAT-REAG-003", "碳酸氢钠 (USP级)", "化学试剂", "Sigma", "LOT-SB20231001",
         8, 5, "kg", date(2023, 10, 1), date(2025, 10, 1), "常温干燥", "正常", 650.00),
        ("MAT-REAG-004", "D-葡萄糖 (USP级)", "化学试剂", "Sigma", "LOT-GLU20240401",
         12, 8, "kg", date(2024, 4, 1), date(2026, 4, 1), "常温干燥", "正常", 420.00),
        ("MAT-REAG-005", "L-谷氨酰胺 (USP级)", "化学试剂", "Sigma", "LOT-GLN20240101",
         4, 5, "kg", date(2024, 1, 1), date(2025, 7, 1), "2-8°C", "临近效期", 2800.00),
        ("MAT-REAG-006", "台盼蓝染液 (0.4%)", "化学试剂", "Bio-Rad", "LOT-TB20240601",
         6, 3, "瓶", date(2024, 6, 1), date(2026, 6, 1), "常温", "正常", 180.00),
        ("MAT-REAG-007", "PEDV ELISA 检测试剂盒", "化学试剂", "IDEXX", "LOT-ELISA20240115",
         8, 5, "盒", date(2024, 1, 15), date(2025, 1, 15), "2-8°C", "已过期", 8500.00),
        ("MAT-REAG-008", "内毒素检测试剂盒 (LAL法)", "化学试剂", "Lonza", "LOT-LAL20240301",
         10, 5, "盒", date(2024, 3, 1), date(2026, 3, 1), "2-8°C", "正常", 6200.00),
        ("MAT-REAG-009", "支原体检测试剂盒 (PCR法)", "化学试剂", "Thermo", "LOT-MYCO20240201",
         4, 3, "盒", date(2024, 2, 1), date(2025, 8, 1), "-20°C", "临近效期", 9500.00),
        ("MAT-REAG-010", "无菌检测培养基 (TSB/FTM)", "化学试剂", "BD", "LOT-STM20240501",
         25, 15, "瓶", date(2024, 5, 1), date(2026, 5, 1), "常温", "正常", 220.00),
        # ── 佐剂 (5) ──
        ("MAT-ADJ-001", "ISA 206 油佐剂", "佐剂", "SEPPIC", "LOT-ADJ-20250115",
         3, 5, "L", date(2025, 1, 15), date(2027, 1, 15), "2-8°C", "温度受损", 1800.00),
        ("MAT-ADJ-002", "氢氧化铝凝胶 (2%)", "佐剂", "Brenntag", "LOT-ALG20240315",
         8, 6, "L", date(2024, 3, 15), date(2026, 3, 15), "常温", "正常", 450.00),
        ("MAT-ADJ-003", "吐温-80 (注射级)", "佐剂", "Sigma", "LOT-TW8020240201",
         5, 4, "L", date(2024, 2, 1), date(2026, 2, 1), "常温", "正常", 680.00),
        ("MAT-ADJ-004", "Span-80 (注射级)", "佐剂", "Sigma", "LOT-SP8020240201",
         4, 4, "L", date(2024, 2, 1), date(2026, 2, 1), "常温", "正常", 720.00),
        ("MAT-ADJ-005", "轻质矿物油 (注射级)", "佐剂", "Sonneborn", "LOT-MO20240101",
         10, 8, "L", date(2024, 1, 1), date(2026, 1, 1), "常温", "正常", 320.00),
        # ── 包材 (12) ──
        ("MAT-PKG-001", "西林瓶 10mL (I型硼硅)", "包材", "山东药玻", "LOT-VL10-20240101",
         5000, 3000, "支", date(2024, 1, 1), date(2027, 1, 1), "常温", "正常", 0.85),
        ("MAT-PKG-002", "西林瓶 20mL (I型硼硅)", "包材", "山东药玻", "LOT-VL20-20240301",
         8000, 5000, "支", date(2024, 3, 1), date(2027, 3, 1), "常温", "正常", 1.20),
        ("MAT-PKG-003", "西林瓶 50mL (I型硼硅)", "包材", "山东药玻", "LOT-VL50-20240115",
         3000, 2000, "支", date(2024, 1, 15), date(2027, 1, 15), "常温", "正常", 2.50),
        ("MAT-PKG-004", "胶塞 20mm (丁基橡胶)", "包材", "江阴橡塑", "LOT-ST20-20240301",
         6000, 4000, "个", date(2024, 3, 1), date(2026, 9, 1), "常温干燥", "正常", 0.35),
        ("MAT-PKG-005", "胶塞 32mm (丁基橡胶)", "包材", "江阴橡塑", "LOT-ST32-20240201",
         4000, 3000, "个", date(2024, 2, 1), date(2026, 8, 1), "常温干燥", "正常", 0.55),
        ("MAT-PKG-006", "铝盖 20mm (Flip-off)", "包材", "江苏华兰", "LOT-AC20-20240401",
         8000, 5000, "个", date(2024, 4, 1), date(2027, 4, 1), "常温", "正常", 0.18),
        ("MAT-PKG-007", "铝盖 32mm (Flip-off)", "包材", "江苏华兰", "LOT-AC32-20240315",
         5000, 3500, "个", date(2024, 3, 15), date(2027, 3, 15), "常温", "正常", 0.28),
        ("MAT-PKG-008", "培养基配制瓶 500mL", "包材", "Corning", "LOT-BT500-20240101",
         50, 30, "个", date(2024, 1, 1), date(2028, 1, 1), "常温", "正常", 45.00),
        ("MAT-PKG-009", "培养基配制瓶 1L", "包材", "Corning", "LOT-BT1000-20240201",
         40, 25, "个", date(2024, 2, 1), date(2028, 2, 1), "常温", "正常", 68.00),
        ("MAT-PKG-010", "无菌滤器 0.22μm (PES)", "包材", "Sartorius", "LOT-FL022-20240301",
         100, 60, "个", date(2024, 3, 1), date(2026, 3, 1), "常温", "正常", 120.00),
        ("MAT-PKG-011", "生物反应器培养袋 50L", "包材", "Sartorius", "LOT-BAG50-20240115",
         20, 12, "个", date(2024, 1, 15), date(2026, 1, 15), "常温", "正常", 3800.00),
        ("MAT-PKG-012", "生物反应器培养袋 200L", "包材", "Sartorius", "LOT-BAG200-20240215",
         15, 10, "个", date(2024, 2, 15), date(2026, 2, 15), "常温", "低于安全库存", 8500.00),
        # ── 其他 (5) ──
        ("MAT-OTH-001", "75% 乙醇消毒液", "消毒剂", "山东利尔康", "LOT-ETOH20240501",
         30, 20, "L", date(2024, 5, 1), date(2025, 11, 1), "常温远离火源", "正常", 25.00),
        ("MAT-OTH-002", "过氧乙酸消毒液 (0.2%)", "消毒剂", "山东利尔康", "LOT-PAA20240401",
         15, 10, "L", date(2024, 4, 1), date(2025, 10, 1), "常温避光", "正常", 45.00),
        ("MAT-OTH-003", "一次性无菌手套 (无粉)", "耗材", "Ansell", "LOT-GLV20240301",
         2000, 1000, "双", date(2024, 3, 1), date(2027, 3, 1), "常温", "正常", 2.50),
        ("MAT-OTH-004", "一次性洁净服 (连体)", "耗材", "杜邦", "LOT-CLN20240201",
         500, 300, "套", date(2024, 2, 1), date(2027, 2, 1), "常温", "正常", 85.00),
        ("MAT-OTH-005", "甲醛溶液 (37% USP级)", "消毒剂", "Sigma", "LOT-FA20231201",
         5, 8, "L", date(2023, 12, 1), date(2025, 6, 1), "常温避光", "临近效期", 380.00),
    ]
    cur.executemany(
        f"INSERT INTO {SCHEMA}.material_inventory VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        materials,
    )
    conn.commit()
    print(f"✓ material_inventory: {len(materials)} 行")


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — 批次物料消耗 BOM
# ═══════════════════════════════════════════════════════════

def gen_batch_material_usage(cur, bid, i, batch_d):
    """每批生成 4-6 条物料消耗记录"""
    operators = ["张某", "李某", "王某", "赵某", "陈某", "刘某"]
    usage_date = batch_d + timedelta(days=random.randint(1, 5))

    # 基础物料消耗
    if i <= 16:
        g_med = "MAT-MED-001"
        m_med = "MAT-MED-004"
    elif i <= 32:
        g_med = "MAT-MED-002"
        m_med = "MAT-MED-005"
    else:
        g_med = "MAT-MED-003"
        m_med = "MAT-MED-006"

    items = [
        (g_med, round(random.uniform(0.8, 1.3), 2), "瓶"),
        (m_med, round(random.uniform(0.6, 1.0), 2), "瓶"),
        ("MAT-ADJ-001", round(random.uniform(0.3, 0.8), 2), "L"),
        ("MAT-PKG-001", round(random.uniform(450, 550), 0), "支"),
        ("MAT-PKG-004", round(random.uniform(450, 550), 0), "个"),
        ("MAT-PKG-006", round(random.uniform(450, 550), 0), "个"),
    ]

    # 异常批次调整
    if i == 38:
        items[0] = ("MAT-MED-001", 1.5, "瓶")
    if i == 40:
        items[2] = ("MAT-ADJ-001", 0.6, "L")
    if i == 35:
        items.append(("MAT-REAG-007", 1.0, "盒"))

    if random.random() < 0.25:
        items.append((
            random.choice(["MAT-REAG-002", "MAT-REAG-008", "MAT-REAG-010", "MAT-REAG-006"]),
            round(random.uniform(0.1, 0.5), 2),
            random.choice(["L", "盒", "瓶"]),
        ))

    for mat_id, planned_qty, unit in items:
        actual_qty = round(planned_qty * random.uniform(0.90, 1.05), 2)
        cur.execute(
            f"INSERT INTO {SCHEMA}.batch_material_usage (batch_id,material_id,planned_qty,actual_qty,unit,consumed_date,operator) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (bid, mat_id, planned_qty, actual_qty, unit, usage_date, random.choice(operators)),
        )


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — 仪器校准
# ═══════════════════════════════════════════════════════════

def gen_equipment_calibration(conn):
    """生成 20 台仪器校准记录"""
    cur = conn.cursor()
    equipments = [
        ("EQ-001", "生物反应器 #1", "生产设备", "上游车间", "SN-BR-2020-001",
         date(2024, 6, 1), date(2025, 6, 1), 12, "PASS", "计量所-王某"),
        ("EQ-002", "生物反应器 #2", "生产设备", "上游车间", "SN-BR-2020-002",
         date(2024, 8, 15), date(2025, 8, 15), 12, "PASS", "计量所-王某"),
        ("EQ-003", "生物反应器 #3", "生产设备", "上游车间", "SN-BR-2021-001",
         date(2024, 9, 1), date(2025, 9, 1), 12, "PASS", "计量所-王某"),
        ("EQ-004", "生物反应器 #5", "生产设备", "上游车间", "SN-BR-2022-001",
         date(2025, 1, 10), date(2026, 1, 10), 12, "PASS", "计量所-王某"),
        ("EQ-005", "管式离心机 #1", "生产设备", "下游车间", "SN-CF-2019-001",
         date(2024, 12, 1), date(2025, 12, 1), 12, "PASS", "计量所-赵某"),
        ("EQ-006", "管式离心机 #2", "生产设备", "下游车间", "SN-CF-2019-002",
         date(2024, 5, 15), date(2025, 5, 15), 12, "PASS", "计量所-赵某"),
        ("EQ-007", "管式离心机 #3", "生产设备", "下游车间", "SN-CF-2020-001",
         date(2024, 11, 1), date(2025, 11, 1), 12, "PASS", "计量所-赵某"),
        ("EQ-008", "灌装机 #1", "生产设备", "灌装车间", "SN-FIL-2020-001",
         date(2024, 10, 1), date(2025, 10, 1), 12, "PASS", "计量所-李某"),
        ("EQ-009", "灌装机 #2", "生产设备", "灌装车间", "SN-FIL-2021-001",
         date(2025, 2, 1), date(2026, 2, 1), 12, "PASS", "计量所-李某"),
        ("EQ-010", "冷冻干燥机", "生产设备", "下游车间", "SN-FD-2020-001",
         date(2024, 7, 15), date(2025, 7, 15), 12, "PASS", "计量所-赵某"),
        ("EQ-011", "高压灭菌锅 #1", "灭菌设备", "准备间", "SN-AC-2020-001",
         date(2024, 12, 15), date(2025, 12, 15), 12, "PASS", "特检院-张某"),
        ("EQ-012", "高压灭菌锅 #2", "灭菌设备", "准备间", "SN-AC-2021-001",
         date(2025, 1, 15), date(2026, 1, 15), 12, "PASS", "特检院-张某"),
        ("EQ-013", "CO₂ 培养箱 #1", "培养设备", "QC实验室", "SN-INC-2021-001",
         date(2024, 3, 1), date(2025, 3, 1), 12, "FAIL", "计量所-王某"),
        ("EQ-014", "CO₂ 培养箱 #2", "培养设备", "QC实验室", "SN-INC-2022-001",
         date(2025, 2, 15), date(2026, 2, 15), 12, "PASS", "计量所-王某"),
        ("EQ-015", "酶标仪", "分析仪器", "QC实验室", "SN-ELISA-2020-001",
         date(2024, 11, 1), date(2025, 11, 1), 12, "PASS", "计量所-陈某"),
        ("EQ-016", "pH 计 #1", "分析仪器", "QC实验室", "SN-pH-2021-001",
         date(2024, 6, 15), date(2025, 6, 15), 6, "PASS", "计量所-陈某"),
        ("EQ-017", "pH 计 #2 (车间)", "分析仪器", "上游车间", "SN-pH-2022-001",
         date(2024, 9, 15), date(2025, 3, 15), 6, "PASS", "计量所-陈某"),
        ("EQ-018", "渗透压仪", "分析仪器", "QC实验室", "SN-OSM-2021-001",
         date(2025, 1, 1), date(2025, 7, 1), 6, "PASS", "计量所-陈某"),
        ("EQ-019", "温度记录仪 (冷库#3)", "监控设备", "仓储", "SN-TMP-2022-001",
         date(2024, 8, 1), date(2025, 8, 1), 12, "PASS", "计量所-刘某"),
        ("EQ-020", "温度记录仪 (冷库#1)", "监控设备", "仓储", "SN-TMP-2021-001",
         date(2025, 3, 1), date(2026, 3, 1), 12, "PASS", "计量所-刘某"),
    ]
    cur.executemany(
        f"INSERT INTO {SCHEMA}.equipment_calibration VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        equipments,
    )
    conn.commit()
    print(f"✓ equipment_calibration: {len(equipments)} 行")


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — 人员培训
# ═══════════════════════════════════════════════════════════

def gen_personnel_training(conn):
    """生成 25 条人员培训记录"""
    cur = conn.cursor()
    records = [
        ("张某", "上游生产", "细胞培养操作员", "GMP 基础培训", date(2023, 3, 15), date(2025, 3, 15), "马主任", "PASS"),
        ("张某", "上游生产", "细胞培养操作员", "无菌操作技术", date(2024, 5, 10), date(2025, 5, 10), "周主任", "PASS"),
        ("李某", "QC", "QC检验员", "ELISA 检测操作", date(2023, 8, 20), date(2025, 8, 20), "陈某", "PASS"),
        ("李某", "QC", "QC检验员", "内毒素检测 (LAL法)", date(2024, 2, 15), date(2025, 2, 15), "陈某", "PASS"),
        ("王某", "上游生产", "生物反应器操作员", "生物反应器操作与维护", date(2023, 6, 10), date(2025, 6, 10), "马主任", "PASS"),
        ("王某", "上游生产", "生物反应器操作员", "GMP 基础培训", date(2024, 1, 20), date(2026, 1, 20), "马主任", "PASS"),
        ("赵某", "QA", "QA审核员", "偏差调查与CAPA", date(2024, 3, 5), date(2026, 3, 5), "周主任", "PASS"),
        ("赵某", "QA", "QA审核员", "批记录审核", date(2024, 7, 1), date(2025, 7, 1), "周主任", "PASS"),
        ("陈某", "QC", "QC主管", "数据完整性", date(2024, 4, 15), date(2026, 4, 15), "马主任", "PASS"),
        ("刘某", "下游生产", "灌装操作员", "灌装机操作SOP", date(2023, 11, 1), date(2025, 11, 1), "马主任", "PASS"),
        ("刘某", "下游生产", "灌装操作员", "洁净区行为规范", date(2024, 6, 15), date(2025, 6, 15), "周主任", "FAIL"),
        ("孙某", "下游生产", "离心/澄清操作员", "管式离心机操作", date(2024, 1, 10), date(2026, 1, 10), "马主任", "PASS"),
        ("周某", "仓储", "仓库管理员", "冷链管理规范", date(2023, 12, 1), date(2025, 12, 1), "马主任", "PASS"),
        ("周某", "仓储", "仓库管理员", "危险化学品储存", date(2024, 5, 20), date(2025, 5, 20), "周主任", "PASS"),
        ("吴某", "工程", "设备工程师", "HVAC系统维护", date(2024, 2, 28), date(2025, 2, 28), "马主任", "PASS"),
        ("吴某", "工程", "设备工程师", "纯化水系统操作", date(2024, 8, 1), date(2026, 8, 1), "马主任", "PASS"),
        ("郑某", "QC", "微生物检验员", "无菌检测操作", date(2024, 3, 15), date(2025, 3, 15), "陈某", "PASS"),
        ("郑某", "QC", "微生物检验员", "支原体检测 (PCR法)", date(2024, 9, 1), date(2025, 9, 1), "陈某", "PASS"),
        ("林某", "上游生产", "配液操作员", "培养基配制SOP", date(2023, 9, 15), date(2024, 9, 15), "张某", "PASS"),
        ("林某", "上游生产", "配液操作员", "GMP 基础培训", date(2024, 10, 1), date(2026, 10, 1), "马主任", "PASS"),
        ("何某", "仓储", "仓库管理员", "物料入库检验流程", date(2024, 4, 1), date(2026, 4, 1), "周主任", "PASS"),
        ("何某", "仓储", "仓库管理员", "温湿度监控系统操作", date(2023, 7, 1), date(2024, 7, 1), "马主任", "PASS"),
        ("冯某", "QA", "QA经理", "变更管理", date(2024, 6, 1), date(2026, 6, 1), "周主任", "PASS"),
        ("冯某", "QA", "QA经理", "风险管理", date(2024, 11, 1), date(2025, 11, 1), "周主任", "PASS"),
        ("马某", "上游生产", "C班组操作员", "无菌操作技术", date(2024, 8, 15), date(2025, 8, 15), "周主任", "PASS"),
    ]
    cur.executemany(
        f"INSERT INTO {SCHEMA}.personnel_training (employee_name,department,role,training_topic,training_date,expiry_date,trainer,result) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        records,
    )
    conn.commit()
    print(f"✓ personnel_training: {len(records)} 行")


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — 洁净区环境监测
# ═══════════════════════════════════════════════════════════

def gen_environmental_monitoring(conn):
    """生成 100 条洁净区环境监测记录"""
    cur = conn.cursor()
    areas = [
        ("细胞培养间", "A/B", "生物反应器旁"),
        ("细胞培养间", "A/B", "更衣间出口"),
        ("病毒培养间", "A/B", "取样口附近"),
        ("灭活间", "B", "灭活罐旁"),
        ("澄清间", "B", "离心机旁"),
        ("灌装间", "A", "灌装机#1旁"),
        ("灌装间", "A", "灌装机#2旁"),
        ("配液间", "C", "配制罐旁"),
        ("更衣间", "C/D", "一更"),
        ("更衣间", "C/D", "二更"),
    ]
    monitors = ["李某", "王某", "陈某"]
    records = []
    base_date = date(2024, 1, 5)

    for j in range(100):
        d = base_date + timedelta(days=j * 7)
        area, grade, location = random.choice(areas)

        if grade == "A":
            p05 = int(gauss(800, 500))
            p5 = int(gauss(5, 3))
        elif grade == "A/B":
            p05 = int(gauss(1500, 800))
            p5 = int(gauss(10, 6))
        elif grade == "B":
            p05 = int(gauss(2500, 1000))
            p5 = int(gauss(15, 8))
        else:
            p05 = int(gauss(5000, 2000))
            p5 = int(gauss(25, 12))

        viable = int(gauss(3, 4)) if grade in ("A", "A/B") else int(gauss(8, 8))
        surface = max(0, int(gauss(1, 2)))
        temp = gauss(22, 1.5, 1)
        humidity = gauss(45, 8, 1)
        pressure = gauss(15, 3, 1)

        if grade == "A" and (p05 > 3520 or p5 > 20):
            pf = "FAIL"
        elif grade == "A/B" and (p05 > 3520 or p5 > 29):
            pf = "FAIL"
        elif grade == "B" and (p05 > 3520):
            pf = "FAIL"
        else:
            pf = "PASS"

        # 批次22灌装间超标
        if d.year == 2024 and d.month == 11 and 10 <= d.day <= 20 and area == "灌装间" and grade == "A":
            p05, p5, pf = 4200, 18, "FAIL"

        records.append((d, area, grade, location, max(0, p05), max(0, p5),
                        max(0, viable), surface, temp, humidity, pressure, pf,
                        random.choice(monitors)))

    cur.executemany(
        f"INSERT INTO {SCHEMA}.environmental_monitoring (monitor_date,area,grade,location,particle_0_5um,particle_5_0um,viable_count_cfu,surface_count_cfu,temp_c,humidity_pct,pressure_diff_pa,pass_fail,monitored_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        records,
    )
    conn.commit()
    print(f"✓ environmental_monitoring: {len(records)} 行")


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — 仓储温湿度
# ═══════════════════════════════════════════════════════════

def gen_warehouse_monitoring(conn):
    """生成仓储温湿度监测记录（每日多区，含关键异常事件）"""
    cur = conn.cursor()
    zones = ["原料库-常温", "原料库-冷库#1", "原料库-冷库#2", "原料库-冷库#3", "成品库-冷库A", "成品库-冷库B"]
    records = []
    base_date = date(2024, 1, 1)

    # 每天随机选 1-2 个区记录，覆盖关键日期
    for day_offset in range(600):
        d = base_date + timedelta(days=day_offset)
        n_zones = random.randint(1, 2)
        chosen = random.sample(zones, n_zones)

        for zone in chosen:
            hour = random.randint(0, 23)
            ts = datetime(d.year, d.month, d.day, hour, 0, 0)

            if "冷库" in zone:
                temp = gauss(4.5, 1.0, 1)
                temp_alarm = temp < 1.5 or temp > 8.5
            else:
                temp = gauss(22, 3, 1)
                temp_alarm = temp < 15 or temp > 30

            humidity = gauss(50, 10, 1)
            humidity_alarm = humidity > 75
            notes_val = None

            # 2025年7月: 原料库-常温湿度持续偏高
            if d.year == 2025 and d.month == 7 and zone == "原料库-常温":
                humidity = gauss(83, 5, 1)
                humidity_alarm = True
                if humidity > 85:
                    notes_val = "湿度严重超标，已通知仓库管理员检查除湿设备"

            # 2025年8月15日: 冷库#3温度故障（全天，精确到小时）
            if d.year == 2025 and d.month == 8 and d.day == 15 and zone == "原料库-冷库#3":
                temp_alarm = True
                if hour < 8:
                    temp = gauss(5.5, 1.5, 1)
                    notes_val = "冷库#3温度开始异常升高"
                elif hour < 12:
                    temp = gauss(10, 2, 1)
                    notes_val = "冷库#3温度持续升高，接近报警阈值"
                elif hour < 18:
                    temp = gauss(16, 2, 1)
                    notes_val = "冷库#3温度异常升高！压缩机疑似故障，已紧急报修"
                else:
                    temp = gauss(10, 3, 1)
                    notes_val = "维修中，备用冷机临时降温，温度逐步回落"
            if d.year == 2025 and d.month == 8 and d.day == 16 and zone == "原料库-冷库#3":
                if hour < 4:
                    temp = gauss(6, 1.5, 1)
                    temp_alarm = hour < 2
                    notes_val = "温度逐步恢复正常" if hour >= 2 else "仍在回落中"
                else:
                    temp = gauss(4.0, 1.0, 1)
                    temp_alarm = Falsee
                    notes_val = "温度恢复正常，累计超温约6小时"

            records.append((ts, zone, temp, humidity, temp_alarm, humidity_alarm, notes_val))

    cur.executemany(
        f"INSERT INTO {SCHEMA}.warehouse_monitoring (monitor_ts,zone,temp_c,humidity_pct,temp_alarm,humidity_alarm,notes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        records,
    )
    conn.commit()
    print(f"✓ warehouse_monitoring: {len(records)} 行")


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — 仓储异常事件
# ═══════════════════════════════════════════════════════════

def gen_storage_excursions(conn):
    """生成 5 条仓储异常事件（含仓储→生产追溯链关键事件）"""
    cur = conn.cursor()
    excursions = [
        ("EXC-2025-001", "MAT-MED-001", date(2025, 7, 20), "湿度超标",
         "原料库-常温 7月持续湿度>85%（峰值92%），VP-SFM生长培养基(LOT-G20230101)包装密封性因高湿破坏，粉末受潮结块。warehouse_monitoring 7月共12天湿度报警。根因为除湿机故障未及时维修。",
         3, "瓶", 0.0, 0.0, "受潮变质", "报废", "何某"),
        ("EXC-2025-002", "MAT-ADJ-001", date(2025, 8, 16), "温度超标",
         "冷库#3温控系统故障，库温从4°C升至18°C持续约6h（warehouse_monitoring确认）。库内ISA 206佐剂(LOT-ADJ-20250115)暴露于高温，物理性质改变。温度记录仪EQ-019记录完整。",
         5, "L", 14.0, 6.0, "温度受损", "报废", "周某"),
        ("EXC-2025-003", "MAT-REAG-007", date(2025, 2, 1), "效期过期未处理",
         "PEDV ELISA检测试剂盒(LOT-ELISA20240115)已于2025-01-15过期，但仍存放于QC冷库可用区。盘点发现时已过期17天。期间已被取用2盒（关联偏差 DEV-PEDV-2025-0010 试剂过期使用）。",
         8, "盒", 0.0, 0.0, "已过期", "报废", "何某"),
        ("EXC-2024-001", "MAT-MED-007", date(2024, 8, 10), "温度异常",
         "UltraCULTURE血清替代物(LOT-US20240301)储存冰箱(-20°C)短暂断电4h，温度升至-5°C。产品未完全融化，评估后降级使用（仅用于非关键实验）。",
         3, "瓶", 15.0, 4.0, "温度波动（降级使用）", "降级使用", "周某"),
        ("EXC-2024-002", "MAT-OTH-005", date(2024, 6, 5), "临近效期存放错误",
         "甲醛溶液(LOT-FA20231201)有效期至2025-06-01，已临近效期但未移至待报废区，仍存放于可用区。存在误用于生产环境消毒的风险。",
         5, "L", 0.0, 0.0, "临近效期（位置错误）", "移至待报废区", "何某"),
    ]
    cur.executemany(
        f"INSERT INTO {SCHEMA}.storage_excursions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        excursions,
    )
    conn.commit()
    print(f"✓ storage_excursions: {len(excursions)} 行")


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — 物料入厂检验
# ═══════════════════════════════════════════════════════════

def gen_material_quality_inspection(conn):
    """生成 60 条物料入厂检验记录"""
    cur = conn.cursor()
    testers = ["李某", "王某", "陈某", "郑某"]

    material_tests = {
        "MAT-MED-001": [("外观", "白色粉末，无异物", "白色粉末", "PASS"),
                       ("无菌检测", "阴性", "阴性", "PASS"),
                       ("内毒素", "<0.05 EU/mL", "<0.5 EU/mL", "PASS")],
        "MAT-MED-002": [("外观", "类白色粉末", "类白色粉末", "PASS"),
                       ("无菌检测", "阴性", "阴性", "PASS")],
        "MAT-MED-003": [("外观", "白色粉末", "白色粉末", "PASS"),
                       ("pH值", "7.1", "6.8-7.4", "PASS")],
        "MAT-MED-007": [("外观", "淡黄色液体", "淡黄色液体", "PASS"),
                       ("无菌检测", "阴性", "阴性", "PASS"),
                       ("内毒素", "0.2 EU/mL", "<1.0 EU/mL", "PASS")],
        "MAT-REAG-001": [("纯度", "99.5%", "≥98%", "PASS"),
                        ("外观", "无色透明液体", "无色透明液体", "PASS")],
        "MAT-REAG-007": [("灵敏度", "符合标准品", "符合标准品", "PASS"),
                        ("特异性", "符合标准品", "符合标准品", "PASS")],
        "MAT-REAG-009": [("灵敏度", "10 CFU/mL", "≤10 CFU/mL", "PASS"),
                        ("特异性", "仅检出支原体", "无交叉反应", "PASS")],
        "MAT-ADJ-001": [("外观", "淡黄色油状液体", "淡黄色油状液体", "PASS"),
                       ("粘度", "42 mPa·s", "35-50 mPa·s", "PASS"),
                       ("乳化模拟测试", "未执行", "应执行", "FAIL")],
        "MAT-ADJ-002": [("铝含量", "2.02%", "1.9-2.1%", "PASS"),
                       ("外观", "白色凝胶", "白色凝胶", "PASS")],
        "MAT-PKG-001": [("尺寸", "符合 ISO 8362", "ISO 8362", "PASS"),
                       ("外观", "无裂纹/气泡", "无裂纹/气泡", "PASS"),
                       ("内表面耐水性", "HC1级", "HC1级", "PASS")],
        "MAT-PKG-004": [("硬度", "42 Shore A", "40-50", "PASS"),
                       ("穿刺落屑", "1.2个/6针", "≤5个/6针", "PASS")],
        "MAT-PKG-010": [("完整性测试", "通过 (4.2 bar)", "≥3.5 bar", "PASS"),
                       ("流速", "18 mL/min", "≥15 mL/min", "PASS")],
        "MAT-PKG-011": [("完整性测试", "通过 (无泄漏)", "无泄漏", "PASS"),
                       ("无菌检测", "阴性", "阴性", "PASS")],
        "MAT-OTH-001": [("乙醇浓度", "75.2%", "75±2%", "PASS")],
        "MAT-OTH-003": [("外观", "无破损/污渍", "无破损", "PASS"),
                       ("针孔测试", "AQL 1.0 通过", "AQL 1.0", "PASS")],
        "MAT-OTH-004": [("外观", "无破损", "无破损", "PASS"),
                       ("颗粒物", "符合 Class 100", "Class 100", "PASS")],
    }

    inspections = []
    insp_id = 0
    for mat_id, tests in material_tests.items():
        for test_item, result, spec, pf in tests:
            insp_id += 1
            insp_date = date(2024, 1, 10) + timedelta(days=insp_id * 12)
            notes_val = None
            if mat_id == "MAT-ADJ-001" and test_item == "乳化模拟测试":
                notes_val = "检验标准缺失，该检验项目未被纳入入厂检验规程"
            inspections.append((
                f"INSP-{insp_id:04d}", mat_id, insp_date, test_item,
                result, spec, pf, random.choice(testers), notes_val,
            ))

    cur.executemany(
        f"INSERT INTO {SCHEMA}.material_quality_inspection VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        inspections,
    )
    conn.commit()
    print(f"✓ material_quality_inspection: {len(inspections)} 行")


# ═══════════════════════════════════════════════════════════
# 新增表数据生成 — AEFI 不良反应
# ═══════════════════════════════════════════════════════════

def gen_aefi_reports(conn):
    """生成 15 条 AEFI 不良反应报告（支持信号检测 PRR/ROR 2x2表）"""
    cur = conn.cursor()
    reports = [
        ("AEFI-2024-001", "PEDV-2024-0003", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "发热 (≥40.5°C)", "全身性", "中度", 1, "痊愈", date(2024, 3, 10), "山东某猪场-王某"),
        ("AEFI-2024-002", "PEDV-2024-0005", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "注射部位肿胀", "局部", "轻度", 2, "痊愈", date(2024, 4, 5), "河南某猪场-张某"),
        ("AEFI-2024-003", "PEDV-2024-0007", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "呕吐", "消化系统", "轻度", 1, "痊愈", date(2024, 5, 12), "广东某猪场-李某"),
        ("AEFI-2024-004", "PEDV-2024-0009", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "发热 (≥40.5°C)", "全身性", "中度", 1, "痊愈", date(2024, 6, 20), "河北某猪场-赵某"),
        ("AEFI-2024-005", "PEDV-2024-0010", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "食欲减退", "全身性", "轻度", 3, "痊愈", date(2024, 7, 8), "四川某猪场-孙某"),
        ("AEFI-2024-006", "PEDV-2024-0011", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "过敏反应（荨麻疹）", "免疫介导", "重度", 0, "痊愈", date(2024, 8, 15), "江苏某猪场-周某"),
        ("AEFI-2024-007", "PEDV-2024-0013", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "注射部位肿胀", "局部", "轻度", 2, "痊愈", date(2024, 9, 1), "河南某猪场-张某"),
        ("AEFI-2024-008", "PEDV-2024-0015", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "发热 (≥40.5°C)", "全身性", "中度", 1, "痊愈", date(2024, 10, 10), "山东某猪场-王某"),
        ("AEFI-2025-001", "PEDV-2025-0001", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "腹泻", "消化系统", "轻度", 2, "痊愈", date(2025, 1, 20), "广东某猪场-李某"),
        ("AEFI-2025-002", "PEDV-2025-0003", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "发热 (≥40.5°C)", "全身性", "中度", 1, "痊愈", date(2025, 2, 15), "河北某猪场-赵某"),
        ("AEFI-2025-003", "PEDV-2025-0004", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "注射部位脓肿", "局部", "中度", 5, "痊愈", date(2025, 3, 10), "四川某猪场-孙某"),
        ("AEFI-2025-004", "PEDV-2025-0006", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "食欲减退", "全身性", "轻度", 2, "痊愈", date(2025, 4, 5), "江苏某猪场-周某"),
        ("AEFI-2025-005", "PEDV-2025-0007", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "发热 (≥40.5°C)", "全身性", "中度", 1, "痊愈", date(2025, 5, 12), "山东某猪场-王某"),
        ("AEFI-2025-006", "PEDV-2025-0009", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "过敏反应（呼吸困难）", "免疫介导", "重度", 0, "痊愈", date(2025, 6, 20), "河南某猪场-张某"),
        ("AEFI-2025-007", "PEDV-2025-0011", "猪流行性腹泻灭活疫苗（悬浮培养）",
         "发热 (≥40.5°C)", "全身性", "中度", 2, "痊愈", date(2025, 7, 8), "广东某猪场-李某"),
    ]
    cur.executemany(
        f"INSERT INTO {SCHEMA}.aefi_reports VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        reports,
    )
    conn.commit()
    print(f"✓ aefi_reports: {len(reports)} 行")


# ═══════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════

def main():
    args = parse_args()
    conn = connect(args)

    print("=" * 60)
    print("PEDV 疫苗模拟数据生成器 v2.0 — 50批次 x 22表")
    print("=" * 60)

    # 1. 清理旧 schema
    cur = conn.cursor()
    cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(SCHEMA)))
    print(f"\n✓ 已删除旧 schema {SCHEMA}")

    # 2. 建表 + 授权
    create_schema_and_tables(conn)

    # 3. 参考数据
    print("\n── 参考数据 ──")
    insert_cell_seeds(conn)
    insert_virus_seeds(conn)
    insert_culture_media(conn)

    # 4. 新增: 一次性数据（非批次依赖）
    print("\n── 新增表数据 ──")
    gen_material_inventory(conn)
    gen_equipment_calibration(conn)
    gen_personnel_training(conn)
    gen_environmental_monitoring(conn)
    gen_warehouse_monitoring(conn)
    gen_storage_excursions(conn)
    gen_material_quality_inspection(conn)
    gen_aefi_reports(conn)

    # 5. 50批生产数据（含冷链/运输/物料消耗）
    print("\n── 生产批次 ──")
    generate_all(conn)

    # 6. 汇总
    print("\n" + "=" * 60)
    print("数据生成完成! 汇总:")
    print("=" * 60)

    tables = [
        "cell_seeds", "virus_seeds", "culture_media",
        "production_batches", "cell_culture_log", "virus_culture_log",
        "harvest_inactivation", "semi_product", "in_process_tests",
        "final_product_qc", "deviations",
        "cold_storage_log", "transport_monitoring",
        "material_inventory", "batch_material_usage",
        "equipment_calibration", "personnel_training",
        "environmental_monitoring", "warehouse_monitoring",
        "storage_excursions", "material_quality_inspection",
        "aefi_reports",
    ]

    total = 0
    for t in tables:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier(t)))
        cnt = cur.fetchone()[0]
        total += cnt
        print(f"  {t:35s} {cnt:>6d} 行")
    print(f"  {'─' * 45}")
    print(f"  {'总计':35s} {total:>6d} 行")

    # 异常批次统计
    cur.execute(sql.SQL(
        "SELECT severity, COUNT(*) FROM {}.deviations GROUP BY severity ORDER BY severity"
    ).format(sql.Identifier(SCHEMA)))
    print("\n  异常批次分布:")
    for sev, cnt in cur.fetchall():
        print(f"    {sev}: {cnt}")

    conn.close()
    print("\n✓ 全部完成!")


if __name__ == "__main__":
    main()