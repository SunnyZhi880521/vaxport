#!/usr/bin/env python3
"""疫苗生产模拟数据生成器 v3.0

生成 7 个功能域 Schema × ~40 张表 × 6 种疫苗产品的完整生产数据。
覆盖全链条：生产工序 → 质量管理 → 仓储物料 → 冷链监控 → 设备设施 → 人员培训 → AEFI。

用法:
    python scripts/generate_vaccine_data.py [--host localhost] [--port 5432] \\
        [--db myappdb] [--user postgres] [--password xxx]
"""

import argparse
import math
import os
import random
import sys
from datetime import date, timedelta, datetime

import psycopg2
from psycopg2 import sql

# ═══════════════════════════════════════════════════════════
# 配置常量
# ═══════════════════════════════════════════════════════════

SEED = 42
SCHEMAS = [
    "analog_production",
    "analog_quality",
    "analog_warehouse",
    "analog_coldchain",
    "analog_equipment",
    "analog_hr",
    "analog_pv",
]

PRODUCTS = ["PEDV", "PRRSV", "APP", "HPS", "SS", "HPSSS_COMBO", "ECOLI"]

BATCHES_PER_PRODUCT = {
    "PEDV": 50,
    "PRRSV": 50,
    "APP": 50,
    "HPS": 110,  # 中间体
    "SS": 90,   # 中间体
    "HPSSS_COMBO": 150,  # 3 组合 × 50
    "ECOLI": 50,
}

# HPS 血清型批次数分配
HPS_SEROTYPE_BATCHES = {
    4: 28, 5: 22, 7: 22, 12: 18, 13: 20,
}

# SS 血清型批次数分配
SS_SEROTYPE_BATCHES = {
    "2": 25, "7": 25, "9": 20, "1": 12, "ST7": 8,
}

# 连苗组合定义
COMBO_VARIANTS = {
    "A": {"hps": [4, 5, 13], "ss": ["2", "7", "9"]},
    "B": {"hps": [4, 7], "ss": ["2", "7", "ST7"]},
    "C": {"hps": [4, 5, 12, 13], "ss": ["2", "7", "9", "1"]},
}

OPERATOR_TEAMS = ["A", "B", "C"]
SCALES = [50, 200, 500]

random.seed(SEED)

# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def norm(mean, std, decimals=2):
    """正态分布随机数"""
    v = random.gauss(mean, std)
    return round(v, decimals)

def norm_int(mean, std):
    """正态分布随机整数"""
    return max(0, int(round(random.gauss(mean, std))))

def daterange(start, end):
    """日期范围迭代器"""
    for n in range((end - start).days + 1):
        yield start + timedelta(days=n)

def random_date(start, end):
    """随机日期"""
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, max(0, delta)))

def batch_id(product, year, num):
    """生成批次编号"""
    return f"{product}-{year}-{num:04d}"

def hps_batch_id(serotype, year, num):
    """HPS 中间体批次编号"""
    return f"HPS{serotype}-{year}-{num:04d}"

def ss_batch_id(serotype, year, num):
    """SS 中间体批次编号"""
    return f"SS{serotype}-{year}-{num:04d}"

def combo_batch_id(variant, year, num):
    """连苗成品批次编号"""
    return f"COMBO{variant}-{year}-{num:04d}"

def dev_id(product, year, num):
    return f"DEV-{product}-{year}-{num:04d}"

def capa_id(year, num):
    return f"CAPA-{year}-{num:04d}"

def ensure(conn, stmt, **kwargs):
    """执行 SQL，忽略错误"""
    try:
        with conn.cursor() as cur:
            cur.execute(stmt.format(**kwargs))
    except Exception as e:
        print(f"  [WARN] {e}")

def execute_batch(conn, stmt, rows):
    """批量插入"""
    try:
        with conn.cursor() as cur:
            for row in rows:
                try:
                    cur.execute(stmt, row)
                except Exception as e:
                    print(f"  [WARN] row insert: {e} | {row[:80] if row else ''}")
    except Exception as e:
        print(f"  [ERROR] batch insert: {e}")

# ═══════════════════════════════════════════════════════════
# 异常批次定义（跨产品）
# ═══════════════════════════════════════════════════════════

PEDV_ANOMALIES = {
    8:  {"type": "sterility_false_positive", "severity": "minor",
         "desc": "过程控制无菌检查假阳性（操作污染），复检通过，成品放行"},
    12: {"type": "mycoplasma_false_positive", "severity": "minor",
         "desc": "过程控制支原体检查假阳性（DNA荧光染色），培养法确认为阴性"},
    16: {"type": "inactivation_failure", "severity": "critical",
         "desc": "BEI灭活不彻底，细胞接种法检出活病毒，整批拒收。根因: 高生物负荷+低BEI浓度+高病毒量"},
    19: {"type": "low_potency", "severity": "major",
         "desc": "成品potency=28 U（标准≥32），MOI偏低(0.03)导致病毒增殖不充分，有条件放行"},
    22: {"type": "env_excursion", "severity": "major",
         "desc": "洁净区灌装间粒子计数超标，灌装暂停2h，HVAC自控恢复后复检合格"},
    24: {"type": "equipment_failure", "severity": "major",
         "desc": "管式离心机#2运行中振动超标停机，澄清工序延迟6h。根因: 轴承磨损"},
    27: {"type": "ph_anomaly", "severity": "minor",
         "desc": "细胞培养Day3 pH降至6.52，CO₂流量计短暂故障，持续约8h后恢复"},
    28: {"type": "do_anomaly", "severity": "major",
         "desc": "病毒培养阶段DO持续偏低（最低22%），纯氧供气管路泄漏，细胞密度峰值仅3.8×10⁶/mL"},
    33: {"type": "operator_error", "severity": "minor",
         "desc": "操作员未按SOP在灭活前检测bioburden，事后补测发现bioburden=95 CFU"},
    35: {"type": "reagent_expired", "severity": "major",
         "desc": "ELISA检测试剂盒过期2天仍使用，potency读数异常偏高(52 U)，复检确认实际值38 U"},
    38: {"type": "media_degraded", "severity": "major",
         "desc": "生长培养基受潮结块（仓储湿度超标→包装密封性破坏），细胞生长缓慢，峰值密度仅4.2×10⁶/mL"},
    40: {"type": "adjuvant_failure", "severity": "critical",
         "desc": "ISA 206佐剂乳化失败，油相/水相分离，半成品报废。根因: 冷库#3曾升至18°C持续6h"},
    42: {"type": "low_potency_repeat", "severity": "major",
         "desc": "第二次效价不达标(potency=30 U)，CAPA有效性存疑：MOI下限已调整为0.05但本批仍用0.04"},
    45: {"type": "contamination_true", "severity": "critical",
         "desc": "支原体真阳性（培养法+DNA染色双确认），整批报废。根因: 操作人员更衣不规范"},
    47: {"type": "filling_error", "severity": "major",
         "desc": "灌装机#1装量偏差超限（目标2.0mL，实际1.82-2.35mL），约300瓶剔除"},
    50: {"type": "cold_chain_break", "severity": "major",
         "desc": "运输途中冷藏车制冷机组故障3h，温度升至22°C。MKT=12.3°C，降级放行"},
}

PRRSV_ANOMALIES = {
    5:  {"type": "low_potency", "severity": "major",
         "desc": "冻干后效价偏低（10^4.2 TCID50/mL，标准≥10^5.0），有条件放行"},
    12: {"type": "lyo_failure", "severity": "critical",
         "desc": "冻干机真空度异常，饼块塌陷，最终水分5.8%（标准≤3%），整批报废"},
    18: {"type": "sterility_positive", "severity": "critical",
         "desc": "成品无菌检查阳性（好氧菌），调查中。根因怀疑冻干机真空泄漏导致污染"},
    23: {"type": "cell_growth_poor", "severity": "major",
         "desc": "Marc-145细胞生长缓慢，峰值密度仅2.8×10⁶/mL，收获效价偏低"},
    30: {"type": "vacuum_leak", "severity": "major",
         "desc": "冻干后真空密封检查失败，部分西林瓶漏气（约15%），剔除后其余放行"},
    37: {"type": "env_excursion", "severity": "minor",
         "desc": "冻干区环境粒子计数短期超标，调查确认HVAC回风口滤网破损，已更换"},
    42: {"type": "reconstitution_slow", "severity": "major",
         "desc": "冻干成品复溶时间95秒（标准≤60秒），根因: 冻干保护剂比例不当"},
    48: {"type": "operator_error", "severity": "minor",
         "desc": "病毒接种MOI计算错误（实际0.15 vs 目标0.10），导致CPE进展过快"},
}

APP_ANOMALIES = {
    7:  {"type": "refolding_low", "severity": "major",
         "desc": "ApxI复性回收率仅32%（标准≥50%），该组分效价不足，整批降级"},
    15: {"type": "apxi_expression_low", "severity": "major",
         "desc": "ApxI发酵表达量仅目标值的40%，根因: IPTG诱导时机偏差"},
    22: {"type": "emulsion_failure", "severity": "critical",
         "desc": "ISA 201乳化失败，油水分离，半成品报废。根因: 均质机转速不足"},
    28: {"type": "endotoxin_high", "severity": "major",
         "desc": "纯化后内毒素偏高(12 EU/mg)，增加一步Triton X-114萃取去除，复检合格"},
    33: {"type": "omp_degradation", "severity": "minor",
         "desc": "OMP组分SDS-PAGE检出部分降解条带，确认是冻融过程导致，加强操作规范"},
    39: {"type": "column_fouling", "severity": "major",
         "desc": "Ni-NTA层析柱污染，亲和纯化效率下降40%，需重新装柱再生"},
    46: {"type": "sterility_semi", "severity": "critical",
         "desc": "半成品无菌检查阳性（真菌），调查确认为配制间高效过滤器泄漏"},
}

HPS_ANOMALIES = {
    # key: (serotype, batch_num_in_serotype)
    (4, 3):   {"type": "fermentation_contamination", "severity": "critical",
               "desc": "HPS4发酵染菌（芽孢杆菌），镜检发现大量杂菌，整罐报废"},
    (5, 2):   {"type": "inactivation_incomplete", "severity": "critical",
               "desc": "甲醛灭活不彻底，灭活验证检出活菌。根因: 菌体浓度过高+甲醛浓度偏低"},
    (7, 4):   {"type": "low_antigen", "severity": "major",
               "desc": "抗原含量偏低（仅为标准的60%），浓缩倍数不足。有条件放行"},
    (12, 2):  {"type": "ph_deviation", "severity": "minor",
               "desc": "发酵过程pH控制偏差（最低pH 6.5），影响生长速率但不影响抗原质量"},
    (13, 3):  {"type": "do_failure", "severity": "major",
               "desc": "发酵罐供气系统故障，DO降至2%，菌体生长停滞6h后恢复"},
    (4, 7):   {"type": "endotoxin_high", "severity": "major",
               "desc": "灭活后内毒素超标（25 EU/mL），增加超滤换液步骤后达标"},
    (5, 5):   {"type": "equipment_failure", "severity": "major",
               "desc": "发酵罐搅拌桨机械密封泄漏，需停机维修，延迟收获48h"},
    (7, 6):   {"type": "operator_error", "severity": "minor",
               "desc": "甲醛加量计算错误（实际0.15% vs 目标0.20%），补加后灭活验证通过"},
    (12, 4):  {"type": "harvest_delay", "severity": "minor",
               "desc": "离心机排队等待，收获延迟12h，菌体活力下降但不影响抗原"},
    (13, 5):  {"type": "concentration_low", "severity": "major",
               "desc": "超滤浓缩过程中膜通量下降40%，浓缩倍数仅达目标70%"},
}

SS_ANOMALIES = {
    ("2", 3):  {"type": "fermentation_contamination", "severity": "critical",
                "desc": "SS2发酵罐染杂菌，革兰氏阳性球菌，整罐丢弃"},
    ("7", 4):  {"type": "inactivation_incomplete", "severity": "major",
                "desc": "灭活验证检出微量活菌，延长灭活时间12h后复检通过"},
    ("9", 3):  {"type": "low_antigen", "severity": "major",
                "desc": "抗原产量偏低，根因: 培养基铁离子浓度不足影响链球菌生长"},
    ("1", 2):  {"type": "ph_deviation", "severity": "minor",
                "desc": "发酵后期pH异常升高(8.2)，确认是缓冲体系失效"},
    ("ST7", 2): {"type": "low_cfu", "severity": "major",
                "desc": "ST7菌株生长缓慢，最终CFU仅为目标60%，需延长发酵时间24h"},
    ("2", 6):  {"type": "endotoxin_high", "severity": "major",
                "desc": "灭活后内毒素偏高(18 EU/mL)，增加洗涤步骤后达标"},
    ("7", 7):  {"type": "harvest_delay", "severity": "minor",
                "desc": "收获管路堵塞，清理耗时3h"},
    ("9", 5):  {"type": "operator_error", "severity": "minor",
                "desc": "灭活温度记录仪未校准，温度显示偏差+0.5°C"},
}

COMBO_ANOMALIES = {
    ("A", 8):   {"type": "ratio_error", "severity": "major",
                 "desc": "HPS:SS配制比例偏差（目标1:1，实际为1.23:1），需调整后重新混合"},
    ("A", 15):  {"type": "adjuvant_issue", "severity": "major",
                 "desc": "佐剂加入后出现絮状沉淀，确认是佐剂批次问题，更换佐剂批次后重新配制"},
    ("A", 22):  {"type": "sterility_false_positive", "severity": "minor",
                 "desc": "半成品无菌检查初检阳性，复检阴性，确认为取样操作污染"},
    ("B", 5):   {"type": "filling_error", "severity": "major",
                 "desc": "灌装线速度过快导致装量偏差（RSO 7.5%），约200瓶需剔除"},
    ("B", 12):  {"type": "label_error", "severity": "minor",
                 "desc": "标签打印错误（生产日期偏移1天），需重新贴标"},
    ("B", 18):  {"type": "potency_low", "severity": "major",
                 "desc": "成品效价偏低（ELISA 28 U），根因: HPS4中间体抗原含量不足。有条件放行"},
    ("B", 25):  {"type": "env_excursion", "severity": "minor",
                 "desc": "灌装间环境粒子监测短期超标，HVAC自控恢复，评估后产品不受影响"},
    ("C", 3):   {"type": "particulate_found", "severity": "major",
                 "desc": "灯检发现少量可见异物（玻屑），确认为灌装管路玻璃碎片，整批重新灯检"},
    ("C", 10):  {"type": "ph_deviation", "severity": "minor",
                 "desc": "配制后pH偏低(6.3)，加NaOH微调后达标"},
    ("C", 17):  {"type": "transport_damage", "severity": "major",
                 "desc": "成品运输途中部分外箱破损（~5%），确认内包装完好。加强包装防护"},
    ("C", 20):  {"type": "homogeneity_failure", "severity": "major",
                 "desc": "混合均匀性验证失败（抗原含量CV>15%），延长搅拌时间后复检通过"},
    ("C", 28):  {"type": "filter_integrity", "severity": "major",
                 "desc": "除菌过滤器完整性测试失败（起泡点偏低），更换滤器后重新过滤"},
    ("C", 33):  {"type": "stability_ounit", "severity": "minor",
                 "desc": "稳定性考察3月时间点效价下降超出预期趋势(OOT)，加强后续时间点监测"},
    ("C", 40):  {"type": "raw_material_issue", "severity": "major",
                 "desc": "包材供应商更换西林瓶批次后规格偏差（外径偏大0.3mm），灌装线需调整参数"},
    ("C", 45):  {"type": "operator_training_gap", "severity": "minor",
                 "desc": "新操作员未完成SOP培训即上岗操作配制罐，主管发现后叫停并重新培训"},
}

ECOLI_ANOMALIES = {
    6:  {"type": "lt_expression_low", "severity": "major",
         "desc": "F41菌株LT毒素表达量仅目标30%（ELISA OD偏低），该批次LT类毒素含量不足"},
    14: {"type": "fermentation_contamination", "severity": "critical",
         "desc": "K99发酵罐检出噬菌体污染，菌体裂解，OD600骤降，整罐报废"},
    21: {"type": "inactivation_incomplete", "severity": "critical",
         "desc": "甲醛灭活验证检出微量活菌，延长灭活时间+温度升高后复检通过"},
    28: {"type": "mixing_heterogeneous", "severity": "major",
         "desc": "5菌株混合不均匀，K88+K99组分抗原含量CV达22%，需调整搅拌参数重新混合"},
    35: {"type": "endotoxin_high", "severity": "major",
         "desc": "成品内毒素偏高(8.5 EU/dose)，根因: 灭活后洗涤不充分，增加洗涤步骤"},
    42: {"type": "fimbriae_degradation", "severity": "minor",
         "desc": "987P菌毛抗原SDS-PAGE检出部分降解，确认为蛋白酶残留，优化纯化流程"},
    48: {"type": "stability_accelerated_fail", "severity": "major",
         "desc": "加速稳定性(25°C)6月效价下降45%（标准≤30%），长期稳定性继续监测"},
}


# ═══════════════════════════════════════════════════════════
# DDL 语句生成
# ═══════════════════════════════════════════════════════════

def generate_ddl(conn):
    """创建所有 schema 和表"""
    cur = conn.cursor()

    # 清理旧 schema
    for schema in SCHEMAS:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

    # 清理旧 analog_pedv schema
    cur.execute("DROP SCHEMA IF EXISTS analog_pedv CASCADE")

    # 创建新 schema + 授权
    for schema in SCHEMAS:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"GRANT USAGE ON SCHEMA {schema} TO vlm_reader")

    # ── analog_production ──
    prod = "analog_production"

    # PEDV 表
    for stmt in [
        f"""CREATE TABLE {prod}.pedv_production_batches (
            batch_id VARCHAR(25) PRIMARY KEY, product_type VARCHAR(15) DEFAULT 'PEDV',
            cell_seed_id VARCHAR(20), virus_seed_id VARCHAR(20),
            growth_medium_id VARCHAR(20), maintenance_medium_id VARCHAR(20),
            bioreactor_scale_l INT, moi DECIMAL(4,2),
            start_date DATE, planned_harvest_date DATE, actual_harvest_date DATE,
            status VARCHAR(20), operator_team VARCHAR(10), notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        f"""CREATE TABLE {prod}.pedv_cell_culture_log (
            id SERIAL PRIMARY KEY, batch_id VARCHAR(20),
            culture_day INT, cell_density_10e6_ml DECIMAL(8,3),
            viability_pct DECIMAL(5,2), ph DECIMAL(3,1), do_pct DECIMAL(5,1),
            temp_c DECIMAL(3,1), glucose_g_per_l DECIMAL(5,3),
            lactate_g_per_l DECIMAL(5,3), ammonia_mm DECIMAL(5,2),
            osmolality_mosm INT, agitation_rpm INT, notes TEXT
        )""",
        f"""CREATE TABLE {prod}.pedv_virus_culture_log (
            id SERIAL PRIMARY KEY, batch_id VARCHAR(20),
            dpi INT, cpe_pct DECIMAL(5,1), cell_density_10e6_ml DECIMAL(8,3),
            viability_pct DECIMAL(5,2), ph DECIMAL(3,1), do_pct DECIMAL(5,1),
            glucose_g_per_l DECIMAL(5,3), lactate_g_per_l DECIMAL(5,3),
            sample_titer_tcid50 DECIMAL(10,2), notes TEXT
        )""",
        f"""CREATE TABLE {prod}.pedv_harvest_inactivation (
            record_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
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
        f"""CREATE TABLE {prod}.pedv_semi_product (
            semi_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            volume_l DECIMAL(8,2), antigen_content_elisa_u_ml DECIMAL(10,2),
            total_protein_mg_ml DECIMAL(6,3), purity_pct DECIMAL(5,2),
            endotoxin_eu_per_dose DECIMAL(5,2), sterility_test VARCHAR(10),
            ph DECIMAL(3,1), appearance VARCHAR(50),
            inactivation_verification VARCHAR(20),
            adjuvant_type VARCHAR(50), adjuvant_ratio DECIMAL(5,2)
        )""",
    ]:
        cur.execute(stmt)

    # PRRSV 表
    for stmt in [
        f"""CREATE TABLE {prod}.prrsv_production_batches (
            batch_id VARCHAR(25) PRIMARY KEY, product_type VARCHAR(15) DEFAULT 'PRRSV',
            virus_strain VARCHAR(30), cell_line VARCHAR(20) DEFAULT 'Marc-145',
            cell_seed_id VARCHAR(20), virus_seed_id VARCHAR(20),
            growth_medium_id VARCHAR(20), maintenance_medium_id VARCHAR(20),
            bioreactor_scale_l INT, moi DECIMAL(4,2),
            start_date DATE, harvest_date DATE, lyophilization_date DATE,
            status VARCHAR(20), operator_team VARCHAR(10), notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        f"""CREATE TABLE {prod}.prrsv_cell_culture_log (
            id SERIAL PRIMARY KEY, batch_id VARCHAR(20),
            culture_day INT, cell_density_10e6_ml DECIMAL(8,3),
            viability_pct DECIMAL(5,2), ph DECIMAL(3,1), do_pct DECIMAL(5,1),
            temp_c DECIMAL(3,1), glucose_g_per_l DECIMAL(5,3),
            lactate_g_per_l DECIMAL(5,3), ammonia_mm DECIMAL(5,2),
            osmolality_mosm INT, agitation_rpm INT, notes TEXT
        )""",
        f"""CREATE TABLE {prod}.prrsv_virus_culture_log (
            id SERIAL PRIMARY KEY, batch_id VARCHAR(20),
            dpi INT, cpe_pct DECIMAL(5,1), cell_density_10e6_ml DECIMAL(8,3),
            viability_pct DECIMAL(5,2), ph DECIMAL(3,1), do_pct DECIMAL(5,1),
            glucose_g_per_l DECIMAL(5,3), lactate_g_per_l DECIMAL(5,3),
            sample_titer_tcid50 DECIMAL(10,2), notes TEXT
        )""",
        f"""CREATE TABLE {prod}.prrsv_harvest_log (
            record_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            harvest_date DATE, harvest_volume_l DECIMAL(8,2),
            pre_clarify_titer_tcid50 DECIMAL(10,2), clarification_method VARCHAR(30),
            post_clarify_volume_l DECIMAL(8,2), post_clarify_titer_tcid50 DECIMAL(10,2),
            bioburden_cfu INT
        )""",
        f"""CREATE TABLE {prod}.prrsv_lyophilization_log (
            record_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            lyophilization_date DATE,
            freezing_temp_c DECIMAL(4,1), freezing_duration_h DECIMAL(4,1),
            primary_drying_temp_c DECIMAL(4,1), primary_drying_pressure_pa DECIMAL(6,2),
            primary_drying_duration_h DECIMAL(5,1),
            secondary_drying_temp_c DECIMAL(4,1), secondary_drying_duration_h DECIMAL(4,1),
            final_moisture_pct DECIMAL(4,2), cake_appearance VARCHAR(30),
            vacuum_seal_test VARCHAR(10), reconstitution_time_s INT
        )""",
        f"""CREATE TABLE {prod}.prrsv_semi_product (
            semi_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            volume_l DECIMAL(8,2), titer_tcid50_per_ml DECIMAL(10,2),
            stabilizer_type VARCHAR(50), stabilizer_ratio_pct DECIMAL(5,2),
            sterility_test VARCHAR(10), ph DECIMAL(3,1), appearance VARCHAR(50)
        )""",
    ]:
        cur.execute(stmt)

    # APP 表
    for stmt in [
        f"""CREATE TABLE {prod}.app_production_batches (
            batch_id VARCHAR(25) PRIMARY KEY, product_type VARCHAR(15) DEFAULT 'APP',
            antigen_components TEXT[], expression_system VARCHAR(30) DEFAULT 'E.coli BL21(DE3)',
            bioreactor_scale_l INT, start_date DATE, harvest_date DATE,
            status VARCHAR(20), operator_team VARCHAR(10), notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        f"""CREATE TABLE {prod}.app_fermentation_log (
            id SERIAL PRIMARY KEY, batch_id VARCHAR(20),
            component VARCHAR(10), fermentation_h DECIMAL(5,1),
            od600 DECIMAL(6,3), ph DECIMAL(3,1), do_pct DECIMAL(5,1),
            temp_c DECIMAL(3,1), glucose_g_per_l DECIMAL(5,2),
            iptg_induction_h DECIMAL(5,1), induction_duration_h DECIMAL(4,1),
            wet_cell_weight_g_per_l DECIMAL(6,2), target_protein_pct DECIMAL(5,2)
        )""",
        f"""CREATE TABLE {prod}.app_purification_log (
            record_id VARCHAR(30) PRIMARY KEY, batch_id VARCHAR(20),
            component VARCHAR(10), inclusion_body_wash_cycles INT,
            inclusion_body_purity_pct DECIMAL(5,2),
            solubilization_method VARCHAR(30),
            affinity_resin VARCHAR(30), affinity_elution_purity_pct DECIMAL(5,2),
            iex_resin VARCHAR(30), iex_elution_purity_pct DECIMAL(5,2),
            sec_purity_pct DECIMAL(5,2), refolding_method VARCHAR(40),
            refolding_recovery_pct DECIMAL(5,2),
            final_protein_conc_mg_ml DECIMAL(6,3)
        )""",
        f"""CREATE TABLE {prod}.app_semi_product (
            semi_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            volume_l DECIMAL(8,2), apxi_conc_ug_ml DECIMAL(6,2),
            apxii_conc_ug_ml DECIMAL(6,2), apxiii_conc_ug_ml DECIMAL(6,2),
            omp_conc_ug_ml DECIMAL(6,2),
            adjuvant_type VARCHAR(30), adjuvant_ratio_pct DECIMAL(5,2),
            ph DECIMAL(3,1), appearance VARCHAR(50),
            sterility_test VARCHAR(10), endotoxin_eu_per_ml DECIMAL(5,2)
        )""",
    ]:
        cur.execute(stmt)

    # HPS 表
    for stmt in [
        f"""CREATE TABLE {prod}.hps_production_batches (
            batch_id VARCHAR(25) PRIMARY KEY, product_type VARCHAR(15) DEFAULT 'HPS',
            serotype INT, strain_name VARCHAR(30), medium_id VARCHAR(20),
            bioreactor_scale_l INT, start_date DATE, harvest_date DATE,
            status VARCHAR(20), operator_team VARCHAR(10), notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        f"""CREATE TABLE {prod}.hps_fermentation_log (
            id SERIAL PRIMARY KEY, batch_id VARCHAR(20),
            fermentation_h DECIMAL(5,1), od600 DECIMAL(6,3),
            ph DECIMAL(3,1), do_pct DECIMAL(5,1),
            temp_c DECIMAL(3,1), glucose_g_per_l DECIMAL(5,2),
            cfu_per_ml DECIMAL(12,2), notes TEXT
        )""",
        f"""CREATE TABLE {prod}.hps_harvest_inactivation (
            record_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            harvest_date DATE, harvest_volume_l DECIMAL(8,2),
            pre_concentration_cfu DECIMAL(12,2),
            concentration_factor DECIMAL(5,2), post_conc_volume_l DECIMAL(8,2),
            inactivant VARCHAR(20) DEFAULT 'formaldehyde',
            inactivant_conc_pct DECIMAL(4,2),
            inactivation_temp_c DECIMAL(3,1), inactivation_duration_h DECIMAL(5,1),
            inactivation_verification VARCHAR(20),
            inactivation_completion_date DATE
        )""",
        f"""CREATE TABLE {prod}.hps_semi_product (
            semi_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            volume_l DECIMAL(8,2), antigen_content_ug_ml DECIMAL(6,2),
            sterility_test VARCHAR(10), endotoxin_eu_per_ml DECIMAL(5,2),
            ph DECIMAL(3,1), appearance VARCHAR(50)
        )""",
    ]:
        cur.execute(stmt)

    # SS 表
    for stmt in [
        f"""CREATE TABLE {prod}.ss_production_batches (
            batch_id VARCHAR(25) PRIMARY KEY, product_type VARCHAR(15) DEFAULT 'SS',
            serotype VARCHAR(5), strain_name VARCHAR(30), medium_id VARCHAR(20),
            bioreactor_scale_l INT, start_date DATE, harvest_date DATE,
            status VARCHAR(20), operator_team VARCHAR(10), notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        f"""CREATE TABLE {prod}.ss_fermentation_log (
            id SERIAL PRIMARY KEY, batch_id VARCHAR(20),
            fermentation_h DECIMAL(5,1), od600 DECIMAL(6,3),
            ph DECIMAL(3,1), do_pct DECIMAL(5,1),
            temp_c DECIMAL(3,1), glucose_g_per_l DECIMAL(5,2),
            cfu_per_ml DECIMAL(12,2), notes TEXT
        )""",
        f"""CREATE TABLE {prod}.ss_harvest_inactivation (
            record_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            harvest_date DATE, harvest_volume_l DECIMAL(8,2),
            pre_concentration_cfu DECIMAL(12,2),
            concentration_factor DECIMAL(5,2), post_conc_volume_l DECIMAL(8,2),
            inactivant VARCHAR(20) DEFAULT 'formaldehyde',
            inactivant_conc_pct DECIMAL(4,2),
            inactivation_temp_c DECIMAL(3,1), inactivation_duration_h DECIMAL(5,1),
            inactivation_verification VARCHAR(20),
            inactivation_completion_date DATE
        )""",
        f"""CREATE TABLE {prod}.ss_semi_product (
            semi_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            volume_l DECIMAL(8,2), antigen_content_ug_ml DECIMAL(6,2),
            sterility_test VARCHAR(10), endotoxin_eu_per_ml DECIMAL(5,2),
            ph DECIMAL(3,1), appearance VARCHAR(50)
        )""",
    ]:
        cur.execute(stmt)

    # HPS+SS 连苗表
    for stmt in [
        f"""CREATE TABLE {prod}.hpsss_combo_production_batches (
            batch_id VARCHAR(25) PRIMARY KEY, product_type VARCHAR(15) DEFAULT 'HPSSS_COMBO',
            combo_variant VARCHAR(1), hps_serotypes INT[], ss_serotypes VARCHAR[],
            hps_semi_ids VARCHAR[], ss_semi_ids VARCHAR[],
            adjuvant_type VARCHAR(30), adjuvant_ratio_pct DECIMAL(5,2),
            formulation_volume_l DECIMAL(8,2), filling_date DATE,
            status VARCHAR(20), operator_team VARCHAR(10), notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        f"""CREATE TABLE {prod}.hpsss_combo_semi_product (
            semi_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            total_volume_l DECIMAL(8,2), total_antigen_content_ug_ml DECIMAL(6,2),
            hps_ratio_pct DECIMAL(5,2), ss_ratio_pct DECIMAL(5,2),
            adjuvant_type VARCHAR(30), sterility_test VARCHAR(10),
            endotoxin_eu_per_ml DECIMAL(5,2), ph DECIMAL(3,1), appearance VARCHAR(50)
        )""",
    ]:
        cur.execute(stmt)

    # E. coli 表
    for stmt in [
        f"""CREATE TABLE {prod}.ecoli_production_batches (
            batch_id VARCHAR(25) PRIMARY KEY, product_type VARCHAR(15) DEFAULT 'ECOLI',
            antigen_strains TEXT[], medium_id VARCHAR(20),
            bioreactor_scale_l INT, start_date DATE, harvest_date DATE,
            status VARCHAR(20), operator_team VARCHAR(10), notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        f"""CREATE TABLE {prod}.ecoli_fermentation_log (
            id SERIAL PRIMARY KEY, batch_id VARCHAR(20),
            strain VARCHAR(10), fermentation_h DECIMAL(5,1),
            od600 DECIMAL(6,3), ph DECIMAL(3,1), do_pct DECIMAL(5,1),
            temp_c DECIMAL(3,1), glucose_g_per_l DECIMAL(5,2),
            cfu_per_ml DECIMAL(12,2), fimbriae_expression_elisa DECIMAL(5,2),
            lt_toxin_ug_per_ml DECIMAL(6,3)
        )""",
        f"""CREATE TABLE {prod}.ecoli_harvest_inactivation (
            record_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            harvest_date DATE, harvest_volume_l DECIMAL(8,2),
            pre_concentration_cfu DECIMAL(12,2),
            concentration_factor DECIMAL(5,2), post_conc_volume_l DECIMAL(8,2),
            inactivant VARCHAR(20) DEFAULT 'formaldehyde',
            inactivant_conc_pct DECIMAL(4,2),
            inactivation_temp_c DECIMAL(3,1), inactivation_duration_h DECIMAL(5,1),
            inactivation_verification VARCHAR(20),
            inactivation_completion_date DATE
        )""",
        f"""CREATE TABLE {prod}.ecoli_semi_product (
            semi_id VARCHAR(25) PRIMARY KEY, batch_id VARCHAR(20),
            volume_l DECIMAL(8,2),
            k88_antigen_ug_ml DECIMAL(6,2), k99_antigen_ug_ml DECIMAL(6,2),
            f6_antigen_ug_ml DECIMAL(6,2), f18_antigen_ug_ml DECIMAL(6,2),
            f41_antigen_ug_ml DECIMAL(6,2), lt_toxoid_ug_ml DECIMAL(6,2),
            adjuvant_type VARCHAR(30), adjuvant_ratio_pct DECIMAL(5,2),
            sterility_test VARCHAR(10), endotoxin_eu_per_ml DECIMAL(5,2),
            ph DECIMAL(3,1)
        )""",
    ]:
        cur.execute(stmt)

    # batch_material_usage (跨产品)
    cur.execute(f"""CREATE TABLE {prod}.batch_material_usage (
        id SERIAL PRIMARY KEY, batch_id VARCHAR(20), material_id VARCHAR(20),
        planned_qty DECIMAL(8,2), actual_qty DECIMAL(8,2),
        unit VARCHAR(20), consumed_date DATE, operator VARCHAR(30), notes TEXT
    )""")

    # ── analog_quality ──
    qual = "analog_quality"
    for stmt in [
        f"""CREATE TABLE {qual}.in_process_tests (
            test_id SERIAL PRIMARY KEY, product_type VARCHAR(15), batch_id VARCHAR(20),
            sample_point VARCHAR(30), test_type VARCHAR(40),
            test_date DATE, result_value VARCHAR(50),
            spec_min VARCHAR(50), spec_max VARCHAR(50),
            pass_fail VARCHAR(10), tested_by VARCHAR(30), notes TEXT
        )""",
        f"""CREATE TABLE {qual}.semi_product_qc (
            qc_id SERIAL PRIMARY KEY, product_type VARCHAR(15), batch_id VARCHAR(20),
            test_date DATE, sterility_test VARCHAR(10),
            endotoxin_eu_per_dose DECIMAL(5,2), ph DECIMAL(3,1),
            appearance VARCHAR(50), potency DECIMAL(10,2),
            purity_pct DECIMAL(5,2), inactivation_verification VARCHAR(20),
            residual_inactivant DECIMAL(8,4), pass_fail VARCHAR(10),
            tested_by VARCHAR(30), notes TEXT
        )""",
        f"""CREATE TABLE {qual}.final_product_qc (
            qc_report_id VARCHAR(25) PRIMARY KEY, product_type VARCHAR(15),
            batch_id VARCHAR(20), test_date DATE,
            appearance VARCHAR(50), ph DECIMAL(3,1),
            sterility_test VARCHAR(10), endotoxin_eu_per_dose DECIMAL(5,2),
            potency DECIMAL(10,2), potency_unit VARCHAR(20),
            safety_test VARCHAR(10), efficacy_test VARCHAR(10),
            residual_moisture_pct DECIMAL(4,2),
            filling_volume_ml DECIMAL(5,2),
            adjuvant_content_mg_ml DECIMAL(6,2),
            reconstitution_time_s INT,
            release_decision VARCHAR(15), reviewer VARCHAR(30), notes TEXT
        )""",
        f"""CREATE TABLE {qual}.deviations (
            dev_id VARCHAR(25) PRIMARY KEY, product_type VARCHAR(15),
            batch_id VARCHAR(20), dev_type VARCHAR(30),
            severity VARCHAR(10), description TEXT,
            root_cause TEXT, corrective_action TEXT,
            capa_id VARCHAR(25), reported_date DATE,
            resolved_date DATE, status VARCHAR(15), reported_by VARCHAR(30)
        )""",
        f"""CREATE TABLE {qual}.capa_records (
            capa_id VARCHAR(25) PRIMARY KEY, source_dev_ids VARCHAR[],
            description TEXT, action_plan TEXT,
            responsible_dept VARCHAR(30), due_date DATE,
            completion_date DATE, effectiveness_verified BOOLEAN,
            verification_date DATE, status VARCHAR(15)
        )""",
        f"""CREATE TABLE {qual}.stability_study (
            study_id SERIAL PRIMARY KEY, product_type VARCHAR(15),
            batch_id VARCHAR(20), study_type VARCHAR(15),
            storage_condition VARCHAR(20), time_point_months INT,
            test_date DATE, potency DECIMAL(10,2),
            potency_unit VARCHAR(20), ph DECIMAL(3,1),
            appearance VARCHAR(50), sterility_test VARCHAR(10),
            endotoxin_eu_per_dose DECIMAL(5,2),
            residual_moisture_pct DECIMAL(4,2), pass_fail VARCHAR(10)
        )""",
    ]:
        cur.execute(stmt)

    # ── analog_warehouse ──
    wh = "analog_warehouse"
    for stmt in [
        f"""CREATE TABLE {wh}.supplier_master (
            supplier_id VARCHAR(15) PRIMARY KEY, supplier_name VARCHAR(100),
            category VARCHAR(30), qualification_status VARCHAR(15),
            last_audit_date DATE, audit_score INT,
            country VARCHAR(30), contact_info TEXT, notes TEXT
        )""",
        f"""CREATE TABLE {wh}.material_inventory (
            material_id VARCHAR(25) PRIMARY KEY, material_name VARCHAR(100),
            category VARCHAR(20), supplier_id VARCHAR(15),
            lot_number VARCHAR(30), current_stock DECIMAL(8,2),
            safety_stock DECIMAL(8,2), unit VARCHAR(10),
            receipt_date DATE, expiry_date DATE,
            storage_condition VARCHAR(20), status VARCHAR(15),
            unit_price_cny DECIMAL(8,2), used_by_products VARCHAR[]
        )""",
        f"""CREATE TABLE {wh}.warehouse_monitoring (
            id SERIAL PRIMARY KEY, monitor_ts TIMESTAMPTZ,
            temp_c DECIMAL(4,1), humidity_pct DECIMAL(4,1),
            zone VARCHAR(30), alarm_flag BOOLEAN
        )""",
        f"""CREATE TABLE {wh}.storage_excursions (
            excursion_id VARCHAR(25) PRIMARY KEY, start_ts TIMESTAMPTZ,
            end_ts TIMESTAMPTZ, zone VARCHAR(30),
            excursion_type VARCHAR(20), max_humidity_pct DECIMAL(4,1),
            duration_hours DECIMAL(5,1), affected_material_ids VARCHAR[],
            root_cause TEXT, notes TEXT
        )""",
        f"""CREATE TABLE {wh}.material_quality_inspection (
            inspection_id SERIAL PRIMARY KEY, material_id VARCHAR(20),
            supplier_id VARCHAR(15), lot_number VARCHAR(30),
            inspection_date DATE, test_item VARCHAR(40),
            result_value VARCHAR(50), spec_min VARCHAR(50),
            spec_max VARCHAR(50), pass_fail VARCHAR(10),
            inspector VARCHAR(30), notes TEXT
        )""",
    ]:
        cur.execute(stmt)

    # ── analog_coldchain ──
    cc = "analog_coldchain"
    for stmt in [
        f"""CREATE TABLE {cc}.cold_storage_log (
            id SERIAL PRIMARY KEY, product_type VARCHAR(15),
            batch_id VARCHAR(20), monitor_ts TIMESTAMPTZ,
            temp_c DECIMAL(4,1), humidity_pct DECIMAL(4,1),
            storage_location VARCHAR(30), alarm_flag BOOLEAN
        )""",
        f"""CREATE TABLE {cc}.transport_monitoring (
            shipment_id VARCHAR(25) PRIMARY KEY, product_type VARCHAR(15),
            batch_id VARCHAR(20), route_from VARCHAR(50), route_to VARCHAR(50),
            departure_time TIMESTAMPTZ, arrival_time TIMESTAMPTZ,
            vehicle_type VARCHAR(30),
            temp_min_c DECIMAL(4,1), temp_max_c DECIMAL(4,1),
            temp_excursion_count INT, temp_excursion_duration_min INT,
            mkt_c DECIMAL(5,2), shock_exceeded BOOLEAN,
            product_assessment VARCHAR(20)
        )""",
    ]:
        cur.execute(stmt)

    # ── analog_equipment ──
    eq = "analog_equipment"
    for stmt in [
        f"""CREATE TABLE {eq}.equipment_master (
            equipment_id VARCHAR(15) PRIMARY KEY, equipment_name VARCHAR(100),
            equipment_type VARCHAR(30), model VARCHAR(50),
            location VARCHAR(50), installation_date DATE,
            iq_date DATE, oq_date DATE, pq_date DATE,
            status VARCHAR(15), notes TEXT
        )""",
        f"""CREATE TABLE {eq}.equipment_calibration (
            calibration_id SERIAL PRIMARY KEY, equipment_id VARCHAR(15),
            calibration_item VARCHAR(50), calibration_date DATE,
            due_date DATE, calibration_result VARCHAR(10),
            calibrated_by VARCHAR(30), certificate_number VARCHAR(30),
            notes TEXT
        )""",
        f"""CREATE TABLE {eq}.maintenance_schedule (
            schedule_id SERIAL PRIMARY KEY, equipment_id VARCHAR(15),
            maintenance_type VARCHAR(20), frequency_months INT,
            last_maintenance_date DATE, next_maintenance_date DATE,
            responsible_team VARCHAR(30), status VARCHAR(15)
        )""",
        f"""CREATE TABLE {eq}.maintenance_log (
            log_id SERIAL PRIMARY KEY, equipment_id VARCHAR(15),
            failure_date DATE, failure_description TEXT,
            impact_on_production VARCHAR(10),
            affected_batch_ids VARCHAR[], repair_action TEXT,
            repair_completion_date DATE, downtime_hours DECIMAL(6,1),
            dev_id VARCHAR(25), technician VARCHAR(30)
        )""",
        f"""CREATE TABLE {eq}.wfi_monitoring (
            monitor_id SERIAL PRIMARY KEY, sample_point VARCHAR(30),
            sample_date DATE, conductivity_us_cm DECIMAL(5,2),
            toc_ppb DECIMAL(6,1), endotoxin_eu_per_ml DECIMAL(5,3),
            microbial_limit_cfu_per_100ml INT,
            pass_fail VARCHAR(10), tested_by VARCHAR(30)
        )""",
    ]:
        cur.execute(stmt)

    # ── analog_hr ──
    cur.execute(f"""CREATE TABLE analog_hr.personnel_training (
        training_id SERIAL PRIMARY KEY, employee_name VARCHAR(50),
        department VARCHAR(30), training_topic VARCHAR(80),
        training_date DATE, expiry_date DATE,
        status VARCHAR(15), trainer VARCHAR(50), notes TEXT
    )""")

    # ── analog_pv ──
    cur.execute(f"""CREATE TABLE analog_pv.aefi_reports (
        aefi_id SERIAL PRIMARY KEY, product_type VARCHAR(15),
        batch_id VARCHAR(20), report_date DATE,
        patient_age_months INT, symptom VARCHAR(50),
        severity VARCHAR(15), onset_hours_post_vaccination INT,
        duration_hours INT, outcome VARCHAR(30),
        causality_assessment VARCHAR(30), notes TEXT
    )""")

    # 统一授权
    for schema in SCHEMAS:
        cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO vlm_reader")
        cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO vlm_reader")

    conn.commit()
    print("  DDL 创建完成: 7 schema, ~40 表")


# ═══════════════════════════════════════════════════════════
# 参考数据生成
# ═══════════════════════════════════════════════════════════

def gen_suppliers(conn):
    """供应商主数据 ~15 行"""
    suppliers = [
        ("SUP-001", "赛默飞世尔（上海）", "培养基", "qualified", date(2024,6,15), 92, "中国"),
        ("SUP-002", "默克生命科学", "试剂", "qualified", date(2024,8,20), 88, "德国"),
        ("SUP-003", "Seppic S.A.", "佐剂", "qualified", date(2024,5,10), 90, "法国"),
        ("SUP-004", "山东药玻", "包材", "qualified", date(2024,3,25), 85, "中国"),
        ("SUP-005", "国药试剂", "试剂", "qualified", date(2024,7,1), 82, "中国"),
        ("SUP-006", "GE Healthcare", "层析介质", "qualified", date(2024,4,18), 95, "美国"),
        ("SUP-007", "北京博奥森", "试剂", "probation", date(2024,9,10), 65, "中国"),
        ("SUP-008", "Sigma-Aldrich", "试剂", "qualified", date(2024,6,30), 91, "美国"),
        ("SUP-009", "武汉三利", "包材", "qualified", date(2024,2,14), 78, "中国"),
        ("SUP-010", "旭化成", "层析介质", "qualified", date(2024,8,5), 87, "日本"),
        ("SUP-011", "兰州民海", "培养基", "probation", date(2024,11,1), 62, "中国"),
        ("SUP-012", "Baxter Healthcare", "佐剂", "disqualified", date(2024,1,15), 45, "美国"),
        ("SUP-013", "上海百赛", "包材", "qualified", date(2024,5,22), 80, "中国"),
        ("SUP-014", "Avantor", "试剂", "qualified", date(2024,10,8), 89, "美国"),
        ("SUP-015", "南通海发", "设备", "qualified", date(2024,3,1), 83, "中国"),
    ]
    rows = [(s[0], s[1], s[2], s[3], s[4], s[5], s[6],
             f"contact_{s[0].lower()}@supplier.com", None) for s in suppliers]
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO analog_warehouse.supplier_master VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", r)
    conn.commit()
    print(f"  supplier_master: {len(rows)} 行")


def gen_materials(conn):
    """物料库存 ~60 行"""
    materials = [
        # 培养基
        ("MAT-MED-001", "MEM 液体培养基", "培养基", "SUP-001", "LOT-M2024001", 500, 100, "L",
         date(2024,1,15), date(2026,1,15), "2-8°C", "normal", 280.00, ["PEDV","PRRSV"]),
        ("MAT-MED-002", "DMEM 高糖培养基", "培养基", "SUP-001", "LOT-M2024002", 350, 80, "L",
         date(2024,2,1), date(2025,12,1), "2-8°C", "normal", 320.00, ["PEDV","PRRSV"]),
        ("MAT-MED-003", "TSB 胰蛋白胨大豆肉汤", "培养基", "SUP-011", "LOT-M2024003", 200, 50, "L",
         date(2024,3,10), date(2024,11,10), "Room Temp", "low_stock", 150.00, ["HPS","SS","ECOLI"]),
        ("MAT-MED-004", "BHI 脑心浸液培养基", "培养基", "SUP-011", "LOT-M2024004", 180, 50, "L",
         date(2024,4,5), date(2025,10,5), "Room Temp", "normal", 160.00, ["HPS","SS"]),
        ("MAT-MED-005", "LB 培养基", "培养基", "SUP-002", "LOT-M2024005", 600, 150, "L",
         date(2024,5,20), date(2026,5,20), "Room Temp", "normal", 100.00, ["APP","ECOLI"]),
        ("MAT-MED-006", "F68 Pluronic", "培养基", "SUP-001", "LOT-M2024006", 50, 10, "L",
         date(2024,6,1), date(2025,6,1), "2-8°C", "normal", 1200.00, ["PEDV","PRRSV"]),
        ("MAT-MED-007", "MEM 氨基酸补充液 (50×)", "培养基", "SUP-001", "LOT-M2024007", 80, 20, "L",
         date(2024,7,12), date(2025,7,12), "2-8°C", "normal", 850.00, ["PEDV","PRRSV"]),
        ("MAT-MED-008", "胰蛋白酶-EDTA (0.25%)", "培养基", "SUP-001", "LOT-M2024008", 30, 8, "L",
         date(2024,8,1), date(2025,2,1), "-20°C", "normal", 450.00, ["PRRSV"]),
        ("MAT-MED-009", "酵母提取物", "培养基", "SUP-002", "LOT-M2024009", 250, 60, "kg",
         date(2024,9,15), date(2026,9,15), "Room Temp", "normal", 220.00, ["HPS","SS","ECOLI","APP"]),
        ("MAT-MED-010", "蛋白胨", "培养基", "SUP-002", "LOT-M2024010", 300, 80, "kg",
         date(2024,10,1), date(2026,10,1), "Room Temp", "normal", 180.00, ["HPS","SS","ECOLI","APP"]),
        ("MAT-MED-011", "葡萄糖 (注射级)", "培养基", "SUP-005", "LOT-M2024011", 500, 100, "kg",
         date(2024,2,28), date(2026,2,28), "Room Temp", "normal", 45.00, ["PEDV","PRRSV","HPS","SS","ECOLI","APP"]),
        ("MAT-MED-012", "MEM 非必需氨基酸 (100×)", "培养基", "SUP-001", "LOT-M2024012", 60, 15, "L",
         date(2024,11,1), date(2025,5,1), "2-8°C", "normal", 920.00, ["PEDV","PRRSV"]),
        # 试剂
        ("MAT-REG-001", "BEI (2-bromoethylamine)", "试剂", "SUP-002", "LOT-R2024001", 25, 5, "L",
         date(2024,1,20), date(2025,7,20), "2-8°C", "normal", 3500.00, ["PEDV"]),
        ("MAT-REG-002", "甲醛溶液 (37%)", "试剂", "SUP-005", "LOT-R2024002", 50, 10, "L",
         date(2024,3,15), date(2026,3,15), "Room Temp", "normal", 120.00, ["HPS","SS","ECOLI"]),
        ("MAT-REG-003", "IPTG (异丙基硫代半乳糖苷)", "试剂", "SUP-002", "LOT-R2024003", 5, 1, "kg",
         date(2024,4,10), date(2025,10,10), "2-8°C", "normal", 8500.00, ["APP"]),
        ("MAT-REG-004", "尿素 (分子生物学级)", "试剂", "SUP-005", "LOT-R2024004", 100, 25, "kg",
         date(2024,5,5), date(2026,5,5), "Room Temp", "normal", 80.00, ["APP"]),
        ("MAT-REG-005", "咪唑", "试剂", "SUP-002", "LOT-R2024005", 20, 5, "kg",
         date(2024,6,20), date(2026,6,20), "Room Temp", "normal", 650.00, ["APP"]),
        ("MAT-REG-006", "PBS 缓冲液 (10×)", "试剂", "SUP-005", "LOT-R2024006", 200, 50, "L",
         date(2024,7,1), date(2026,7,1), "Room Temp", "normal", 90.00, ["PEDV","PRRSV","APP","HPS","SS","ECOLI"]),
        ("MAT-REG-007", "NaOH (1M, 分子生物学级)", "试剂", "SUP-005", "LOT-R2024007", 80, 20, "L",
         date(2024,8,10), date(2026,8,10), "Room Temp", "normal", 55.00, ["PEDV","PRRSV","APP","HPS","SS","ECOLI"]),
        ("MAT-REG-008", "Triton X-114", "试剂", "SUP-002", "LOT-R2024008", 10, 2, "L",
         date(2024,9,1), date(2025,9,1), "Room Temp", "normal", 1800.00, ["APP"]),
        ("MAT-REG-009", "Guanidine HCl (6M)", "试剂", "SUP-002", "LOT-R2024009", 30, 8, "L",
         date(2024,10,15), date(2025,10,15), "Room Temp", "expired", 2200.00, ["APP"]),
        ("MAT-REG-010", "氧化氘 (D₂O, 99.9%)", "试剂", "SUP-008", "LOT-R2024010", 2, 0.5, "L",
         date(2024,11,1), date(2026,11,1), "Room Temp", "normal", 15000.00, ["APP"]),
        # 佐剂
        ("MAT-ADJ-001", "ISA 201 VG", "佐剂", "SUP-003", "LOT-A2024001", 200, 50, "L",
         date(2024,1,10), date(2026,1,10), "2-8°C", "normal", 580.00, ["APP","HPS","SS","HPSSS_COMBO","ECOLI"]),
        ("MAT-ADJ-002", "ISA 206", "佐剂", "SUP-003", "LOT-A2024002", 150, 40, "L",
         date(2024,2,20), date(2025,8,20), "2-8°C", "normal", 620.00, ["PEDV"]),
        ("MAT-ADJ-003", "氢氧化铝凝胶", "佐剂", "SUP-012", "LOT-A2024003", 100, 30, "L",
         date(2024,3,1), date(2025,3,1), "2-8°C", "quarantined", 350.00, ["HPS","SS","ECOLI"]),
        ("MAT-ADJ-004", "Montanide IMS 1313", "佐剂", "SUP-003", "LOT-A2024004", 80, 20, "L",
         date(2024,4,15), date(2026,4,15), "2-8°C", "normal", 750.00, ["APP"]),
        # 包材
        ("MAT-PKG-001", "10mL 西林瓶 (中性硼硅)", "包材", "SUP-004", "LOT-P2024001", 50000, 10000, "pcs",
         date(2024,1,5), date(2026,1,5), "Room Temp", "normal", 0.45, ["PEDV","PRRSV","APP","HPS","SS","HPSSS_COMBO","ECOLI"]),
        ("MAT-PKG-002", "胶塞 (丁基橡胶)", "包材", "SUP-004", "LOT-P2024002", 40000, 8000, "pcs",
         date(2024,2,10), date(2026,2,10), "Room Temp", "normal", 0.28, ["PEDV","PRRSV","APP","HPS","SS","HPSSS_COMBO","ECOLI"]),
        ("MAT-PKG-003", "铝盖 (Flip-off)", "包材", "SUP-004", "LOT-P2024003", 45000, 9000, "pcs",
         date(2024,3,20), date(2026,3,20), "Room Temp", "normal", 0.35, ["PEDV","PRRSV","APP","HPS","SS","HPSSS_COMBO","ECOLI"]),
        ("MAT-PKG-004", "标签 (铜版纸)", "包材", "SUP-009", "LOT-P2024004", 60000, 12000, "pcs",
         date(2024,4,1), date(2025,10,1), "Room Temp", "low_stock", 0.08, ["PEDV","PRRSV","APP","HPS","SS","HPSSS_COMBO","ECOLI"]),
        ("MAT-PKG-005", "说明书", "包材", "SUP-009", "LOT-P2024005", 55000, 10000, "pcs",
         date(2024,5,15), date(2026,5,15), "Room Temp", "normal", 0.12, ["PEDV","PRRSV","APP","HPS","SS","HPSSS_COMBO","ECOLI"]),
        ("MAT-PKG-006", "纸箱 (30瓶/箱)", "包材", "SUP-009", "LOT-P2024006", 2000, 400, "pcs",
         date(2024,6,1), date(2025,12,1), "Room Temp", "normal", 3.50, ["PEDV","PRRSV","APP","HPS","SS","HPSSS_COMBO","ECOLI"]),
        ("MAT-PKG-007", "2mL 西林瓶 (冻干用)", "包材", "SUP-004", "LOT-P2024007", 30000, 6000, "pcs",
         date(2024,7,10), date(2026,7,10), "Room Temp", "normal", 0.38, ["PRRSV"]),
        ("MAT-PKG-008", "西林瓶 (中性硼硅 15mL)", "包材", "SUP-013", "LOT-P2024008", 25000, 5000, "pcs",
         date(2024,8,1), date(2025,8,1), "Room Temp", "normal", 0.52, ["HPS","SS","HPSSS_COMBO","ECOLI","APP"]),
        # 冻干辅料
        ("MAT-LYO-001", "蔗糖 (注射级)", "冻干辅料", "SUP-005", "LOT-L2024001", 200, 50, "kg",
         date(2024,1,25), date(2026,1,25), "Room Temp", "normal", 65.00, ["PRRSV"]),
        ("MAT-LYO-002", "明胶 (注射级)", "冻干辅料", "SUP-005", "LOT-L2024002", 100, 25, "kg",
         date(2024,3,1), date(2025,9,1), "Room Temp", "normal", 180.00, ["PRRSV"]),
        ("MAT-LYO-003", "海藻糖 (注射级)", "冻干辅料", "SUP-005", "LOT-L2024003", 120, 30, "kg",
         date(2024,4,10), date(2026,4,10), "Room Temp", "normal", 220.00, ["PRRSV"]),
        ("MAT-LYO-004", "甘露醇 (注射级)", "冻干辅料", "SUP-005", "LOT-L2024004", 80, 20, "kg",
         date(2024,6,1), date(2025,12,1), "Room Temp", "expired", 95.00, ["PRRSV"]),
        ("MAT-LYO-005", "右旋糖酐 (40kDa)", "冻干辅料", "SUP-005", "LOT-L2024005", 60, 15, "kg",
         date(2024,8,15), date(2026,2,15), "Room Temp", "normal", 350.00, ["PRRSV"]),
        # 层析介质
        ("MAT-CHR-001", "Ni-NTA Agarose FF", "层析介质", "SUP-006", "LOT-C2024001", 15, 5, "L",
         date(2024,1,30), date(2026,1,30), "2-8°C", "normal", 8500.00, ["APP"]),
        ("MAT-CHR-002", "Q Sepharose FF", "层析介质", "SUP-006", "LOT-C2024002", 10, 3, "L",
         date(2024,2,15), date(2026,2,15), "2-8°C", "normal", 6200.00, ["APP"]),
        ("MAT-CHR-003", "SP Sepharose FF", "层析介质", "SUP-010", "LOT-C2024003", 8, 2, "L",
         date(2024,3,20), date(2025,9,20), "2-8°C", "normal", 5800.00, ["APP"]),
        ("MAT-CHR-004", "Superdex 200 pg", "层析介质", "SUP-006", "LOT-C2024004", 6, 2, "L",
         date(2024,5,1), date(2026,5,1), "2-8°C", "low_stock", 12000.00, ["APP"]),
        ("MAT-CHR-005", "Chelating Sepharose FF", "层析介质", "SUP-010", "LOT-C2024005", 12, 4, "L",
         date(2024,7,1), date(2025,7,1), "2-8°C", "expired", 7800.00, ["APP"]),
    ]

    with conn.cursor() as cur:
        for m in materials:
            cur.execute(
                "INSERT INTO analog_warehouse.material_inventory VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                m)
    conn.commit()
    print(f"  material_inventory: {len(materials)} 行")


def gen_equipment_master(conn):
    """设备台账 ~30 行"""
    equip = [
        ("EQ-001", "生物反应器 #1 (50L)", "生物反应器", "Applikon 50L", "细胞培养间A (Grade C)", date(2022,6,1), date(2022,7,15), date(2022,8,20), date(2022,9,10), "operational"),
        ("EQ-002", "生物反应器 #2 (200L)", "生物反应器", "Sartorius BIOSTAT 200L", "细胞培养间B (Grade C)", date(2022,8,1), date(2022,9,15), date(2022,10,20), date(2022,11,5), "operational"),
        ("EQ-003", "生物反应器 #3 (500L)", "生物反应器", "Sartorius BIOSTAT 500L", "细胞培养间C (Grade C)", date(2023,1,10), date(2023,2,20), date(2023,3,25), date(2023,4,15), "maintenance"),
        ("EQ-004", "发酵罐 #1 (50L)", "发酵罐", "BioFlo 120", "发酵车间A (Grade D)", date(2022,9,1), date(2022,10,10), date(2022,11,15), date(2022,12,1), "operational"),
        ("EQ-005", "发酵罐 #2 (200L)", "发酵罐", "BioFlo 320", "发酵车间A (Grade D)", date(2022,10,1), date(2022,11,20), date(2022,12,25), date(2023,1,10), "operational"),
        ("EQ-006", "发酵罐 #3 (500L)", "发酵罐", "BioFlo 610", "发酵车间B (Grade D)", date(2023,2,1), date(2023,3,15), date(2023,4,20), date(2023,5,5), "operational"),
        ("EQ-007", "发酵罐 #4 (200L)", "发酵罐", "BioFlo 320", "发酵车间B (Grade D)", date(2023,3,1), date(2023,4,10), date(2023,5,15), date(2023,6,1), "operational"),
        ("EQ-008", "发酵罐 #5 (50L)", "发酵罐", "BioFlo 120", "发酵车间A (Grade D)", date(2023,5,1), date(2023,6,10), date(2023,7,15), date(2023,8,1), "out_of_service"),
        ("EQ-009", "冻干机 #1", "冻干机", "Labconco FreeZone 18L", "冻干间A (Grade B)", date(2022,11,1), date(2022,12,10), date(2023,1,15), date(2023,2,1), "operational"),
        ("EQ-010", "冻干机 #2", "冻干机", "Christ Epsilon 2-10D", "冻干间A (Grade B)", date(2023,1,15), date(2023,2,20), date(2023,3,25), date(2023,4,10), "operational"),
        ("EQ-011", "管式离心机 #1", "离心机", "GEA Westfalia", "纯化车间A (Grade D)", date(2022,5,1), date(2022,6,15), date(2022,7,20), date(2022,8,5), "operational"),
        ("EQ-012", "管式离心机 #2", "离心机", "Alfa Laval", "纯化车间A (Grade D)", date(2022,7,1), date(2022,8,10), date(2022,9,15), date(2022,10,1), "operational"),
        ("EQ-013", "管式离心机 #3", "离心机", "GEA Westfalia", "纯化车间B (Grade D)", date(2023,4,1), date(2023,5,15), date(2023,6,20), date(2023,7,5), "operational"),
        ("EQ-014", "AKTA Pure 150", "层析系统", "Cytiva AKTA Pure 150", "纯化车间A (Grade D)", date(2022,8,1), date(2022,9,10), date(2022,10,15), date(2022,11,1), "operational"),
        ("EQ-015", "AKTA Pilot 600", "层析系统", "Cytiva AKTA Pilot", "纯化车间B (Grade D)", date(2023,6,1), date(2023,7,10), date(2023,8,15), date(2023,9,1), "operational"),
        ("EQ-016", "灌装机 #1", "灌装机", "博世 FXS 2020", "灌装间A (Grade A/B)", date(2022,4,1), date(2022,5,10), date(2022,6,20), date(2022,7,5), "operational"),
        ("EQ-017", "灌装机 #2", "灌装机", "IMA SENSITIVE", "灌装间B (Grade A/B)", date(2023,7,1), date(2023,8,10), date(2023,9,20), date(2023,10,5), "operational"),
        ("EQ-018", "HVAC 系统 #1 (细胞培养区)", "HVAC", "定制", "空调机房A", date(2022,1,15), date(2022,2,20), date(2022,3,25), date(2022,4,10), "operational"),
        ("EQ-019", "HVAC 系统 #2 (灌装区)", "HVAC", "定制", "空调机房B", date(2022,1,15), date(2022,2,20), date(2022,3,25), date(2022,4,10), "operational"),
        ("EQ-020", "冷库 #1 (成品 2-8°C)", "冷库", "定制 100m³", "冷链仓库A", date(2022,1,1), date(2022,2,1), date(2022,3,1), date(2022,4,1), "operational"),
        ("EQ-021", "冷库 #2 (成品 2-8°C)", "冷库", "定制 150m³", "冷链仓库A", date(2022,1,1), date(2022,2,1), date(2022,3,1), date(2022,4,1), "operational"),
        ("EQ-022", "冷库 #3 (物料 2-8°C)", "冷库", "定制 50m³", "物料仓库A", date(2022,1,1), date(2022,2,1), date(2022,3,1), date(2022,4,1), "operational"),
        ("EQ-023", "冰箱 #1 (QC实验室)", "冷库", "海尔 HYC-390", "QC实验室", date(2022,3,1), date(2022,4,1), date(2022,5,1), date(2022,6,1), "operational"),
        ("EQ-024", "冰箱 #2 (QC实验室)", "冷库", "海尔 HYC-390", "QC实验室", date(2022,3,1), date(2022,4,1), date(2022,5,1), date(2022,6,1), "operational"),
        ("EQ-025", "纯化水系统", "纯化水系统", "Milli-Q HX 7000", "纯化水站", date(2022,1,1), date(2022,2,1), date(2022,3,1), date(2022,4,1), "operational"),
        ("EQ-026", "灭菌柜 #1", "灭菌柜", "新华医疗 XG1.D", "灭菌间A", date(2022,2,1), date(2022,3,10), date(2022,4,15), date(2022,5,1), "operational"),
        ("EQ-027", "灭菌柜 #2", "灭菌柜", "新华医疗 XG1.D", "灭菌间B", date(2022,2,1), date(2022,3,10), date(2022,4,15), date(2022,5,1), "operational"),
        ("EQ-028", "超滤系统", "层析系统", "Millipore Pellicon", "纯化车间B (Grade D)", date(2023,8,1), date(2023,9,10), date(2023,10,15), date(2023,11,1), "operational"),
    ]

    with conn.cursor() as cur:
        for e in equip:
            cur.execute(
                "INSERT INTO analog_equipment.equipment_master VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                e + (None,))
    conn.commit()
    print(f"  equipment_master: {len(equip)} 行")


def gen_personnel(conn):
    """人员培训 ~40 行"""
    employees = [
        ("张建国", "生产-A班组", "SOP-MFG-001 细胞培养操作"), ("张建国", "生产-A班组", "GMP 法规年度培训"),
        ("李明辉", "生产-A班组", "SOP-MFG-001 细胞培养操作"), ("李明辉", "生产-A班组", "无菌操作规范"),
        ("王海燕", "生产-A班组", "SOP-MFG-002 生物反应器操作"), ("王海燕", "生产-A班组", "生物安全培训"),
        ("陈大伟", "生产-B班组", "SOP-MFG-001 细胞培养操作"), ("陈大伟", "生产-B班组", "SOP-MFG-003 病毒接种操作"),
        ("刘芳", "生产-B班组", "SOP-MFG-002 生物反应器操作"), ("刘芳", "生产-B班组", "GMP 法规年度培训"),
        ("赵永强", "生产-B班组", "SOP-MFG-004 冻干机操作"), ("赵永强", "生产-B班组", "无菌操作规范"),
        ("孙文博", "生产-C班组", "SOP-MFG-005 发酵罐操作"), ("孙文博", "生产-C班组", "SOP-MFG-006 灭活操作"),
        ("周志远", "生产-C班组", "SOP-MFG-005 发酵罐操作"), ("周志远", "生产-C班组", "GMP 法规年度培训"),
        ("吴晓东", "生产-C班组", "SOP-MFG-003 病毒接种操作"), ("吴晓东", "生产-C班组", "生物安全培训"),
        ("钱学军", "QC实验室", "SOP-QC-001 无菌检查"), ("钱学军", "QC实验室", "SOP-QC-002 内毒素检测"),
        ("马晓燕", "QC实验室", "SOP-QC-003 ELISA检测"), ("马晓燕", "QC实验室", "SOP-QC-004 效价测定"),
        ("朱国强", "QC实验室", "SOP-QC-005 微生物限度"), ("朱国强", "QC实验室", "GMP 法规年度培训"),
        ("沈丽华", "QA部门", "SOP-QA-001 偏差管理"), ("沈丽华", "QA部门", "SOP-QA-002 CAPA管理"),
        ("韩雪峰", "QA部门", "SOP-QA-003 批签发管理"), ("韩雪峰", "QA部门", "GMP 法规年度培训"),
        ("何伟", "工程部", "SOP-ENG-001 设备维护"), ("何伟", "工程部", "SOP-ENG-002 校准管理"),
        ("蔡明宇", "工程部", "SOP-ENG-001 设备维护"), ("蔡明宇", "工程部", "安全操作培训"),
        ("许志强", "仓储部", "SOP-WH-001 物料管理"), ("许志强", "仓储部", "SOP-WH-002 冷链管理"),
        ("蒋建平", "仓储部", "SOP-WH-001 物料管理"), ("蒋建平", "仓储部", "GMP 法规年度培训"),
    ]

    # 培训日期从 2023-01 到 2025-06，部分过期
    base_dates = [
        date(2023, 6, 15), date(2023, 9, 1), date(2024, 1, 10), date(2024, 3, 20),
        date(2024, 6, 5), date(2024, 9, 12), date(2024, 11, 1), date(2025, 1, 15),
        date(2025, 3, 8), date(2025, 5, 20),
    ]
    # 有些人的培训即将或已经过期
    expiry_offsets = [365, 365, 365, 365, 365, 540, 540, 540, 730, 730]

    rows = []
    for i, (name, dept, topic) in enumerate(employees):
        train_date = base_dates[i % len(base_dates)]
        exp_offset = expiry_offsets[i % len(expiry_offsets)]
        exp_date = train_date + timedelta(days=exp_offset)
        status = "expired" if exp_date < date(2026, 5, 1) else "valid"
        if exp_date > date(2026, 5, 1) and exp_date < date(2026, 8, 1):
            status = "expiring_soon"
        rows.append((name, dept, topic, train_date, exp_date, status, f"培训师{random.randint(1,5)}", None))

    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO analog_hr.personnel_training (employee_name,department,training_topic,training_date,expiry_date,status,trainer,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", r)
    conn.commit()
    print(f"  personnel_training: {len(rows)} 行")
    return employees, base_dates, expiry_offsets
# ═══════════════════════════════════════════════════════════
# PEDV 生产数据生成
# ═══════════════════════════════════════════════════════════

def gen_pedv_data(conn):
    """PEDV 灭活疫苗 50 批完整数据"""
    cur = conn.cursor()
    prod = "analog_production"

    # 生产批次 50 批
    batches = []
    for i in range(1, 51):
        if i <= 26:
            year = 2024
            n = i
        else:
            year = 2025
            n = i - 26

        bid = batch_id("PEDV", year, n)
        scale = random.choice(SCALES)
        if i <= 10:
            scale = 50
        elif i <= 30:
            scale = random.choice([200, 500])
        else:
            scale = random.choice(SCALES)

        team = OPERATOR_TEAMS[(i-1) % 3]
        moi = round(random.uniform(0.03, 0.10), 2)
        if i == 19:
            moi = 0.03  # low MOI anomaly
        if i == 42:
            moi = 0.04  # CAPA repeat

        start = date(year, random.randint(1, 12), random.randint(1, 28))
        planned_harv = start + timedelta(days=21)
        actual_harv = planned_harv + timedelta(days=random.randint(-2, 3))

        # Status
        if i in PEDV_ANOMALIES:
            a = PEDV_ANOMALIES[i]
            if a["severity"] == "critical":
                status = "rejected"
            else:
                status = "completed"
        else:
            status = "completed"

        # 种细胞/种病毒选择
        if i <= 20:
            cell_seed = "WCB-VERO-2023-01"
            virus_seed = "WVSS-PEDV-CV777-F5" if i <= 12 else "WVSS-PEDV-CV777-F6"
        else:
            cell_seed = "WCB-VERO-2024-01"
            virus_seed = random.choice(["WVSS-PEDV-CV777-F6", "WVSS-PEDV-CV777-F7", "WVSS-PEDV-AJ1102-F5"])

        batches.append({
            "batch_id": bid, "cell_seed": cell_seed, "virus_seed": virus_seed,
            "growth_medium": "MAT-MED-001", "maintenance_medium": "MAT-MED-002",
            "scale": scale, "moi": moi, "start_date": start,
            "planned_harvest_date": planned_harv, "actual_harvest_date": actual_harv,
            "status": status, "team": team,
            "anomaly": PEDV_ANOMALIES.get(i),
        })

    for b in batches:
        cur.execute(f"""INSERT INTO {prod}.pedv_production_batches 
            (batch_id, product_type, cell_seed_id, virus_seed_id, growth_medium_id, maintenance_medium_id,
             bioreactor_scale_l, moi, start_date, planned_harvest_date, actual_harvest_date, status, operator_team)
            VALUES (%s,'PEDV',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (b["batch_id"], b["cell_seed"], b["virus_seed"], b["growth_medium"], b["maintenance_medium"],
             b["scale"], b["moi"], b["start_date"], b["planned_harvest_date"], b["actual_harvest_date"],
             b["status"], b["team"]))
    conn.commit()

    # 细胞培养日志
    cell_log_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        days = random.randint(5, 7)
        for d in range(days):
            base_density = 6.2 if b["scale"] <= 50 else 5.5
            density = max(1.5, norm(base_density - d * 0.8, 0.5, 3))
            if anomaly and anomaly["type"] == "do_anomaly" and d >= 2:
                density = norm(3.8, 0.3, 3)
            if anomaly and anomaly["type"] == "media_degraded" and d >= 2:
                density = norm(4.2, 0.3, 3)
            viability = max(60, norm(95 - d * 3, 2, 2))
            ph = norm(7.1, 0.1, 1)
            do_val = norm(45, 5, 1)
            if anomaly and anomaly["type"] == "do_anomaly" and d >= 2:
                do_val = norm(28, 5, 1)
            if anomaly and anomaly["type"] == "ph_anomaly" and d == 3:
                ph = 6.52
            cell_log_rows.append((b["batch_id"], d,
                density, viability, ph, do_val,
                norm(37.0, 0.3, 1), norm(3.5 - d*0.3, 0.3), norm(0.5 + d*0.3, 0.2),
                norm(1.2 + d*0.4, 0.2), norm_int(310 + d*10, 5),
                norm_int(120, 10),
                "DO anomaly day "+str(d) if anomaly and anomaly["type"] == "do_anomaly" and d >= 2 else None))

    execute_batch(conn, f"INSERT INTO {prod}.pedv_cell_culture_log (batch_id,culture_day,cell_density_10e6_ml,viability_pct,ph,do_pct,temp_c,glucose_g_per_l,lactate_g_per_l,ammonia_mm,osmolality_mosm,agitation_rpm,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", cell_log_rows)
    print(f"  pedv_cell_culture_log: {len(cell_log_rows)} 行")

    # 病毒培养日志
    virus_log_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        for d in range(5):
            cpe = min(95, norm(d * 20, 5, 1)) if d > 0 else 0
            density = norm(5.5 - d * 1.2, 0.4, 3)
            viability_val = max(30, norm(90 - d * 12, 4, 2))
            titer = norm(6.5 + d * 0.3, 0.2, 2) if d > 0 else None
            if anomaly and anomaly["type"] == "inactivation_failure":
                titer = norm(7.8 + d * 0.1, 0.1, 2) if d > 0 else None
            virus_log_rows.append((b["batch_id"], d, cpe,
                density, viability_val, norm(7.0, 0.15, 1), norm(40 - d*3, 5, 1),
                norm(3.0 - d*0.2, 0.3), norm(1.0 + d*0.5, 0.2),
                titer, None))
    execute_batch(conn, f"INSERT INTO {prod}.pedv_virus_culture_log (batch_id,dpi,cpe_pct,cell_density_10e6_ml,viability_pct,ph,do_pct,glucose_g_per_l,lactate_g_per_l,sample_titer_tcid50,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", virus_log_rows)
    print(f"  pedv_virus_culture_log: {len(virus_log_rows)} 行")

    # 收获灭活
    harv_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        hid = f"HARV-{b['batch_id']}"
        hv = norm(b["scale"] * 0.7, b["scale"] * 0.05, 2)
        pre_titer = norm(7.5, 0.3, 2)
        if anomaly and anomaly["type"] == "inactivation_failure":
            pre_titer = 8.30
        bb_pre = norm_int(50, 30)
        if anomaly and anomaly["type"] == "inactivation_failure":
            bb_pre = 180
        bei_conc = norm(1.5, 0.3, 3)
        if anomaly and anomaly["type"] == "inactivation_failure":
            bei_conc = 1.2
        resid_test = "FAIL" if anomaly and anomaly["type"] == "inactivation_failure" else "PASS"

        harv_rows.append((hid, b["batch_id"], b["actual_harvest_date"], hv,
            pre_titer, "depth_filtration", hv*0.92, pre_titer-0.15,
            bb_pre, norm_int(15, 10), "BEI", bei_conc,
            norm(37.0, 0.2, 1), norm(36, 4, 1), pre_titer, resid_test,
            b["actual_harvest_date"] + timedelta(days=2), norm(8, 1.5, 2), hv*0.12))
    execute_batch(conn, f"INSERT INTO {prod}.pedv_harvest_inactivation VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", harv_rows)
    print(f"  pedv_harvest_inactivation: {len(harv_rows)} 行")

    # 半成品
    semi_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        sid = f"SEMI-{b['batch_id']}"
        potency = norm(40, 4, 2)
        if anomaly and anomaly["type"] == "low_potency":
            potency = 28.0
        if anomaly and anomaly["type"] == "low_potency_repeat":
            potency = 30.0
        purity = norm(94, 2, 2)
        inact_ver = "FAIL" if anomaly and anomaly["type"] == "inactivation_failure" else "PASS"
        adjuvant = "ISA 206" if b["batch_id"].startswith("PEDV-2024") else random.choice(["ISA 206", "ISA 201 VG"])
        if anomaly and anomaly["type"] == "adjuvant_failure":
            adjuvant = "ISA 206"
        semi_rows.append((sid, b["batch_id"], norm(50, 5, 2), potency,
            norm(2.5, 0.3, 3), purity, norm(3.0, 1.0, 2),
            "PASS", norm(7.1, 0.1, 1),
            "乳白色均匀混悬液" if not (anomaly and anomaly["type"] == "adjuvant_failure") else "油水分离/不合格",
            inact_ver, adjuvant, norm(50, 3, 2)))
    execute_batch(conn, f"INSERT INTO {prod}.pedv_semi_product VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", semi_rows)
    print(f"  pedv_semi_product: {len(semi_rows)} 行")

    conn.commit()
    return batches



# ═══════════════════════════════════════════════════════════
# PRRSV 弱毒活苗生产数据
# ═══════════════════════════════════════════════════════════

def gen_prrsv_data(conn):
    """PRRSV 弱毒活苗 50 批"""
    cur = conn.cursor()
    prod = "analog_production"
    strains = ["VR2332", "JXA1-R", "TJM-F92", "CH-1R"]
    stabilizers = ["蔗糖-明胶", "海藻糖-甘露醇", "蔗糖-海藻糖"]

    batches = []
    for i in range(1, 51):
        year = 2024 if i <= 20 else 2025
        n = i if i <= 20 else i - 20
        bid = batch_id("PRRSV", year, n)
        strain = strains[(i-1) % 4]
        scale = random.choice(SCALES)
        team = OPERATOR_TEAMS[(i-1) % 3]
        start = date(year, random.randint(1, 12), random.randint(1, 28))
        harv = start + timedelta(days=random.randint(18, 24))
        lyo = harv + timedelta(days=random.randint(3, 7))

        # Status
        anomaly = PRRSV_ANOMALIES.get(i)
        if anomaly and anomaly["severity"] == "critical":
            status = "rejected"
        else:
            status = "completed"

        batches.append({
            "batch_id": bid, "strain": strain, "scale": scale,
            "start_date": start, "harvest_date": harv, "lyophilization_date": lyo,
            "status": status, "team": team, "anomaly": anomaly,
            "cell_seed": "WCB-VERO-2023-01" if i <= 30 else "WCB-VERO-2024-01",
            "virus_seed": f"WVSS-{strain}-F{random.randint(5,7)}",
            "growth_medium": "MAT-MED-001", "maintenance_medium": "MAT-MED-002",
            "moi": round(random.uniform(0.05, 0.15), 2),
        })

    for b in batches:
        cur.execute(f"""INSERT INTO {prod}.prrsv_production_batches
            (batch_id, product_type, virus_strain, cell_line, cell_seed_id, virus_seed_id,
             growth_medium_id, maintenance_medium_id, bioreactor_scale_l, moi,
             start_date, harvest_date, lyophilization_date, status, operator_team)
            VALUES (%s,'PRRSV',%s,'Marc-145',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (b["batch_id"], b["strain"], b["cell_seed"], b["virus_seed"],
             b["growth_medium"], b["maintenance_medium"], b["scale"], b["moi"],
             b["start_date"], b["harvest_date"], b["lyophilization_date"],
             b["status"], b["team"]))
    conn.commit()

    # 细胞培养日志
    cell_rows = []
    for b in batches:
        for d in range(6):
            density = norm(5.5 - d * 0.7, 0.5, 3)
            viab = max(60, norm(96 - d * 4, 3, 2))
            if b["anomaly"] and b["anomaly"]["type"] == "cell_growth_poor" and d >= 3:
                density = norm(2.8 - d * 0.3, 0.2, 3)
            cell_rows.append((b["batch_id"], d, density, viab,
                norm(7.1, 0.15, 1), norm(45 - d * 2, 5, 1), norm(37.0, 0.3, 1),
                norm(3.2 - d * 0.25, 0.3), norm(0.4 + d * 0.3, 0.2),
                norm(1.0 + d * 0.4, 0.2), norm_int(305 + d * 8, 8),
                norm_int(100, 10), None))
    execute_batch(conn, f"INSERT INTO {prod}.prrsv_cell_culture_log (batch_id,culture_day,cell_density_10e6_ml,viability_pct,ph,do_pct,temp_c,glucose_g_per_l,lactate_g_per_l,ammonia_mm,osmolality_mosm,agitation_rpm,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", cell_rows)
    print(f"  prrsv_cell_culture_log: {len(cell_rows)} 行")

    # 病毒培养日志
    virus_rows = []
    for b in batches:
        for d in range(5):
            cpe = min(95, norm(d * 18, 5, 1)) if d > 0 else 0
            titer = norm(6.0 + d * 0.25, 0.2, 2) if d > 0 else None
            if b["anomaly"] and b["anomaly"]["type"] == "cell_growth_poor" and d >= 2:
                titer = norm(5.5 + d * 0.1, 0.15, 2) if d > 0 else None
            virus_rows.append((b["batch_id"], d, cpe,
                norm(5.0 - d * 1.0, 0.4, 3), max(25, norm(88 - d * 11, 5, 2)),
                norm(7.0, 0.15, 1), norm(42 - d * 3, 5, 1),
                norm(2.8 - d * 0.2, 0.3), norm(0.8 + d * 0.4, 0.2), titer, None))
    execute_batch(conn, f"INSERT INTO {prod}.prrsv_virus_culture_log (batch_id,dpi,cpe_pct,cell_density_10e6_ml,viability_pct,ph,do_pct,glucose_g_per_l,lactate_g_per_l,sample_titer_tcid50,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", virus_rows)
    print(f"  prrsv_virus_culture_log: {len(virus_rows)} 行")

    # 收获
    harv_rows = []
    for b in batches:
        hid = f"HARV-{b['batch_id']}"
        titer = norm(7.3, 0.3, 2)
        if b["anomaly"] and b["anomaly"]["type"] == "cell_growth_poor":
            titer = norm(6.5, 0.2, 2)
        harv_rows.append((hid, b["batch_id"], b["harvest_date"],
            norm(b["scale"] * 0.65, b["scale"] * 0.05, 2), titer,
            "depth_filtration", norm(b["scale"] * 0.6, b["scale"] * 0.05, 2),
            titer - 0.1, norm_int(25, 15)))
    execute_batch(conn, f"INSERT INTO {prod}.prrsv_harvest_log VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", harv_rows)
    print(f"  prrsv_harvest_log: {len(harv_rows)} 行")

    # 冻干
    lyo_rows = []
    for b in batches:
        rid = f"LYO-{b['batch_id']}"
        anomaly = b["anomaly"]
        moisture = norm(1.8, 0.5, 2)
        cake = "白色疏松饼块,形态良好"
        vacuum_test = "PASS"
        recon_time = norm_int(35, 15)
        if anomaly and anomaly["type"] == "lyo_failure":
            moisture = 5.8
            cake = "饼块塌陷/萎缩"
            recon_time = norm_int(120, 20)
        if anomaly and anomaly["type"] == "reconstitution_slow":
            recon_time = 95
        if anomaly and anomaly["type"] == "vacuum_leak":
            vacuum_test = "FAIL (部分)"
        lyo_rows.append((rid, b["batch_id"], b["lyophilization_date"],
            norm(-45, 2, 1), norm(4, 0.5, 1),
            norm(-25, 2, 1), norm(15, 3, 2), norm(24, 3, 1),
            norm(25, 2, 1), norm(6, 1, 1),
            moisture, cake, vacuum_test, recon_time))
    execute_batch(conn, f"INSERT INTO {prod}.prrsv_lyophilization_log VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", lyo_rows)
    print(f"  prrsv_lyophilization_log: {len(lyo_rows)} 行")

    # 半成品
    semi_rows = []
    for b in batches:
        sid = f"SEMI-{b['batch_id']}"
        stab = random.choice(stabilizers)
        semi_rows.append((sid, b["batch_id"], norm(b["scale"] * 0.08, b["scale"] * 0.01, 2),
            norm(7.0, 0.3, 2), stab, norm(5, 1, 2),
            "PASS", norm(7.0, 0.1, 1), "微黄色透明液体"))
    execute_batch(conn, f"INSERT INTO {prod}.prrsv_semi_product VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", semi_rows)
    print(f"  prrsv_semi_product: {len(semi_rows)} 行")

    conn.commit()
    return batches


# ═══════════════════════════════════════════════════════════
# APP 亚单位疫苗生产数据
# ═══════════════════════════════════════════════════════════

def gen_app_data(conn):
    """APP 亚单位疫苗 50 批"""
    cur = conn.cursor()
    prod = "analog_production"
    components = ["ApxI", "ApxII", "ApxIII", "OMP"]

    batches = []
    for i in range(1, 51):
        year = 2024 if i <= 20 else 2025
        n = i if i <= 20 else i - 20
        bid = batch_id("APP", year, n)
        scale = random.choice(SCALES)
        team = OPERATOR_TEAMS[(i-1) % 3]
        start = date(year, random.randint(1, 12), random.randint(1, 28))
        harv = start + timedelta(days=random.randint(5, 8))

        anomaly = APP_ANOMALIES.get(i)
        status = "rejected" if (anomaly and anomaly["severity"] == "critical") else "completed"

        batches.append({
            "batch_id": bid, "scale": scale, "start_date": start,
            "harvest_date": harv, "status": status, "team": team,
            "anomaly": anomaly,
        })

    for b in batches:
        cur.execute(f"""INSERT INTO {prod}.app_production_batches
            (batch_id, product_type, antigen_components, expression_system,
             bioreactor_scale_l, start_date, harvest_date, status, operator_team)
            VALUES (%s,'APP',%s,%s,%s,%s,%s,%s,%s)""",
            (b["batch_id"], components, "E.coli BL21(DE3)",
             b["scale"], b["start_date"], b["harvest_date"],
             b["status"], b["team"]))
    conn.commit()

    # 发酵日志（4组分各独立发酵）
    ferm_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        for comp in components:
            for h in [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]:
                od = norm(2 + h * 2.5, 1.5, 3) if h <= 10 else norm(25 + (h-10) * 1.5, 2, 3)
                wcw = norm(5 + h * 3, 2, 2) if h >= 8 else None
                prot_pct = norm(25, 5, 2) if h >= 12 else None
                if anomaly and anomaly["type"] == "apxi_expression_low" and comp == "ApxI" and h >= 12:
                    prot_pct = norm(10, 3, 2)
                ferm_rows.append((b["batch_id"], comp, float(h),
                    od, norm(6.9, 0.15, 1), norm(35, 8, 1), norm(37.0, 0.3, 1),
                    norm(20 - h * 1.2, 2, 2),
                    norm(3, 1, 1) if h >= 4 else None,
                    norm(6, 2, 1) if h >= 4 else None,
                    wcw, prot_pct))
    execute_batch(conn, f"INSERT INTO {prod}.app_fermentation_log (batch_id,component,fermentation_h,od600,ph,do_pct,temp_c,glucose_g_per_l,iptg_induction_h,induction_duration_h,wet_cell_weight_g_per_l,target_protein_pct) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", ferm_rows)
    print(f"  app_fermentation_log: {len(ferm_rows)} 行")

    # 纯化日志
    pur_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        for comp in components:
            rid = f"PUR-{b['batch_id']}-{comp}"
            ib_purity = norm(65, 8, 2)
            aff_purity = norm(88, 4, 2)
            iex_purity = norm(94, 3, 2)
            sec_purity = norm(97, 1.5, 2)
            refold_rec = norm(65, 10, 2)
            if anomaly and anomaly["type"] == "refolding_low" and comp == "ApxI":
                refold_rec = 32.0
            if anomaly and anomaly["type"] == "omp_degradation" and comp == "OMP":
                sec_purity = norm(88, 2, 2)
            final_conc = norm(2.5, 0.5, 3)
            pur_rows.append((rid, b["batch_id"], comp,
                norm_int(3, 1), ib_purity, random.choice(["8M Urea", "6M GuHCl"]),
                "Ni-NTA", aff_purity, random.choice(["Q Sepharose", "SP Sepharose"]),
                iex_purity, sec_purity,
                random.choice(["稀释复性", "透析复性", "柱上复性"]),
                refold_rec, final_conc))
    execute_batch(conn, f"INSERT INTO {prod}.app_purification_log VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", pur_rows)
    print(f"  app_purification_log: {len(pur_rows)} 行")

    # 半成品
    semi_rows = []
    for b in batches:
        sid = f"SEMI-{b['batch_id']}"
        anomaly = b["anomaly"]
        apxi_conc = norm(50, 5, 2) if not (anomaly and anomaly["type"] == "refolding_low") else norm(18, 3, 2)
        sterility = "PASS"
        if anomaly and anomaly["type"] == "sterility_semi":
            sterility = "FAIL"
        semi_rows.append((sid, b["batch_id"], norm(b["scale"] * 0.06, b["scale"] * 0.01, 2),
            apxi_conc, norm(50, 5, 2), norm(50, 5, 2), norm(50, 5, 2),
            "ISA 201 VG", norm(50, 3, 2), norm(6.9, 0.1, 1),
            "乳白色均匀乳剂", sterility, norm(2.0, 1.0, 2)))
    execute_batch(conn, f"INSERT INTO {prod}.app_semi_product VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", semi_rows)
    print(f"  app_semi_product: {len(semi_rows)} 行")

    conn.commit()
    return batches


# ═══════════════════════════════════════════════════════════
# HPS 灭活中间体生产数据
# ═══════════════════════════════════════════════════════════

def gen_hps_data(conn):
    """HPS 灭活中间体 ~110 批"""
    cur = conn.cursor()
    prod = "analog_production"

    batches = []
    for serotype, count in sorted(HPS_SEROTYPE_BATCHES.items()):
        for n in range(1, count + 1):
            year = 2024 if n <= count // 2 else 2025
            num = n if year == 2024 else n - count // 2
            bid = hps_batch_id(serotype, year, num)
            scale = random.choice(SCALES)
            team = random.choice(OPERATOR_TEAMS)
            start = date(year, random.randint(1, 12), random.randint(1, 28))
            harv = start + timedelta(days=random.randint(3, 5))

            anomaly_key = (serotype, n)
            anomaly = HPS_ANOMALIES.get(anomaly_key)
            status = "rejected" if (anomaly and anomaly["severity"] == "critical") else "completed"

            batches.append({
                "batch_id": bid, "serotype": serotype, "scale": scale,
                "start_date": start, "harvest_date": harv,
                "status": status, "team": team, "anomaly": anomaly,
                "medium_id": "MAT-MED-003" if random.random() < 0.7 else "MAT-MED-004",
            })

    for b in batches:
        cur.execute(f"""INSERT INTO {prod}.hps_production_batches
            (batch_id, product_type, serotype, strain_name, medium_id,
             bioreactor_scale_l, start_date, harvest_date, status, operator_team)
            VALUES (%s,'HPS',%s,%s,%s,%s,%s,%s,%s,%s)""",
            (b["batch_id"], b["serotype"], f"HPS-Serotype-{b['serotype']}",
             b["medium_id"], b["scale"], b["start_date"], b["harvest_date"],
             b["status"], b["team"]))
    conn.commit()

    # 发酵日志
    ferm_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        for h in range(0, 12, 2):
            od = norm(1.5 + h * 1.8, 1.2, 3)
            cfu = norm(5e8 + h * 2e8, 1e8, 2) if h >= 4 else norm(5e7 + h * 3e7, 2e7, 2)
            do_val = norm(40 - h, 5, 1)
            if anomaly and anomaly["type"] == "do_failure" and h >= 6:
                do_val = norm(5, 2, 1)
            ph = norm(7.1, 0.1, 1)
            if anomaly and anomaly["type"] == "ph_deviation" and h >= 4:
                ph = norm(6.55, 0.1, 1)
            ferm_rows.append((b["batch_id"], float(h), od, ph, do_val,
                norm(37.0, 0.3, 1), norm(15 - h * 0.9, 2, 2), cfu, None))
    execute_batch(conn, f"INSERT INTO {prod}.hps_fermentation_log (batch_id,fermentation_h,od600,ph,do_pct,temp_c,glucose_g_per_l,cfu_per_ml,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", ferm_rows)
    print(f"  hps_fermentation_log: {len(ferm_rows)} 行")

    # 收获灭活
    harv_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        rid = f"HARV-{b['batch_id']}"
        cfu_pre = norm(2e9, 5e8, 2)
        conc_factor = norm(8, 1.5, 2)
        inact_ver = "PASS"
        if anomaly and anomaly["type"] == "inactivation_incomplete":
            inact_ver = "FAIL"
        if anomaly and anomaly["type"] == "concentration_low":
            conc_factor = norm(4.5, 0.5, 2)
        harv_rows.append((rid, b["batch_id"], b["harvest_date"],
            norm(b["scale"] * 0.65, b["scale"] * 0.05, 2),
            cfu_pre, conc_factor, norm(b["scale"] * 0.08, b["scale"] * 0.01, 2),
            "formaldehyde", norm(0.20, 0.02, 2),
            norm(37.0, 0.2, 1), norm(24, 4, 1),
            inact_ver, b["harvest_date"] + timedelta(days=1)))
    execute_batch(conn, f"INSERT INTO {prod}.hps_harvest_inactivation VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", harv_rows)
    print(f"  hps_harvest_inactivation: {len(harv_rows)} 行")

    # 半成品
    semi_rows = []
    for b in batches:
        sid = f"SEMI-{b['batch_id']}"
        anomaly = b["anomaly"]
        antigen = norm(500, 60, 2)
        if anomaly and anomaly["type"] == "low_antigen":
            antigen = norm(300, 30, 2)
        endotoxin = norm(5, 3, 2)
        if anomaly and anomaly["type"] == "endotoxin_high":
            endotoxin = 25.0
        semi_rows.append((sid, b["batch_id"], norm(b["scale"] * 0.06, b["scale"] * 0.01, 2),
            antigen, "PASS", endotoxin, norm(7.0, 0.1, 1),
            "微黄色混悬液"))
    execute_batch(conn, f"INSERT INTO {prod}.hps_semi_product VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", semi_rows)
    print(f"  hps_semi_product: {len(semi_rows)} 行")

    conn.commit()
    return batches


# ═══════════════════════════════════════════════════════════
# SS 灭活中间体生产数据
# ═══════════════════════════════════════════════════════════

def gen_ss_data(conn):
    """SS 灭活中间体 ~90 批"""
    cur = conn.cursor()
    prod = "analog_production"

    batches = []
    for serotype, count in sorted(SS_SEROTYPE_BATCHES.items()):
        for n in range(1, count + 1):
            year = 2024 if n <= count // 2 else 2025
            num = n if year == 2024 else n - count // 2
            bid = ss_batch_id(serotype, year, num)
            scale = random.choice(SCALES)
            team = random.choice(OPERATOR_TEAMS)
            start = date(year, random.randint(1, 12), random.randint(1, 28))
            harv = start + timedelta(days=random.randint(3, 5))

            anomaly_key = (serotype, n)
            anomaly = SS_ANOMALIES.get(anomaly_key)
            status = "rejected" if (anomaly and anomaly["severity"] == "critical") else "completed"

            batches.append({
                "batch_id": bid, "serotype": serotype, "scale": scale,
                "start_date": start, "harvest_date": harv,
                "status": status, "team": team, "anomaly": anomaly,
                "medium_id": "MAT-MED-003" if random.random() < 0.6 else "MAT-MED-004",
            })

    for b in batches:
        cur.execute(f"""INSERT INTO {prod}.ss_production_batches
            (batch_id, product_type, serotype, strain_name, medium_id,
             bioreactor_scale_l, start_date, harvest_date, status, operator_team)
            VALUES (%s,'SS',%s,%s,%s,%s,%s,%s,%s,%s)""",
            (b["batch_id"], b["serotype"], f"SS-Serotype-{b['serotype']}",
             b["medium_id"], b["scale"], b["start_date"], b["harvest_date"],
             b["status"], b["team"]))
    conn.commit()

    # 发酵日志
    ferm_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        for h in range(0, 12, 2):
            od = norm(1.2 + h * 1.9, 1.3, 3)
            cfu = norm(4e8 + h * 2.2e8, 1.2e8, 2) if h >= 4 else norm(3e7 + h * 3e7, 2e7, 2)
            if anomaly and anomaly["type"] == "low_cfu" and h >= 6:
                cfu = norm(2e8 + h * 5e7, 5e7, 2)
            ph = norm(7.0, 0.15, 1)
            if anomaly and anomaly["type"] == "ph_deviation" and h >= 6:
                ph = norm(8.1, 0.1, 1)
            ferm_rows.append((b["batch_id"], float(h), od, ph,
                norm(38 - h, 5, 1), norm(37.0, 0.3, 1),
                norm(12 - h * 0.8, 2, 2), cfu, None))
    execute_batch(conn, f"INSERT INTO {prod}.ss_fermentation_log (batch_id,fermentation_h,od600,ph,do_pct,temp_c,glucose_g_per_l,cfu_per_ml,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", ferm_rows)
    print(f"  ss_fermentation_log: {len(ferm_rows)} 行")

    # 收获灭活
    harv_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        rid = f"HARV-{b['batch_id']}"
        cfu_pre = norm(1.8e9, 5e8, 2)
        inact_ver = "PASS"
        if anomaly and anomaly["type"] == "inactivation_incomplete":
            inact_ver = "FAIL (复检PASS)"
        harv_rows.append((rid, b["batch_id"], b["harvest_date"],
            norm(b["scale"] * 0.6, b["scale"] * 0.05, 2),
            cfu_pre, norm(7, 1.2, 2), norm(b["scale"] * 0.08, b["scale"] * 0.01, 2),
            "formaldehyde", norm(0.20, 0.02, 2),
            norm(37.0, 0.2, 1), norm(24, 4, 1),
            inact_ver, b["harvest_date"] + timedelta(days=1)))
    execute_batch(conn, f"INSERT INTO {prod}.ss_harvest_inactivation VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", harv_rows)
    print(f"  ss_harvest_inactivation: {len(harv_rows)} 行")

    # 半成品
    semi_rows = []
    for b in batches:
        sid = f"SEMI-{b['batch_id']}"
        anomaly = b["anomaly"]
        antigen = norm(480, 55, 2)
        if anomaly and anomaly["type"] == "low_antigen":
            antigen = norm(280, 30, 2)
        endotoxin = norm(4, 2.5, 2)
        if anomaly and anomaly["type"] == "endotoxin_high":
            endotoxin = 18.0
        semi_rows.append((sid, b["batch_id"], norm(b["scale"] * 0.05, b["scale"] * 0.01, 2),
            antigen, "PASS", endotoxin, norm(7.0, 0.1, 1), "微黄色混悬液"))
    execute_batch(conn, f"INSERT INTO {prod}.ss_semi_product VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", semi_rows)
    print(f"  ss_semi_product: {len(semi_rows)} 行")

    conn.commit()
    return batches


# ═══════════════════════════════════════════════════════════
# HPS+SS 连苗成品生产数据
# ═══════════════════════════════════════════════════════════

def gen_combo_data(conn, hps_batches, ss_batches):
    """HPS+SS 连苗成品 150 批（3 组合 × 50）"""
    cur = conn.cursor()
    prod = "analog_production"

    # 按血清型索引中间体
    hps_by_sero = {}
    for b in hps_batches:
        s = b["serotype"]
        if s not in hps_by_sero:
            hps_by_sero[s] = []
        hps_by_sero[s].append(b)

    ss_by_sero = {}
    for b in ss_batches:
        s = b["serotype"]
        if s not in ss_by_sero:
            ss_by_sero[s] = []
        ss_by_sero[s].append(b)

    batches = []
    for variant, combo in sorted(COMBO_VARIANTS.items()):
        hps_needed = combo["hps"]
        ss_needed = combo["ss"]
        for n in range(1, 51):
            year = 2024 if n <= 25 else 2025
            num = n if year == 2024 else n - 25
            bid = combo_batch_id(variant, year, num)
            team = random.choice(OPERATOR_TEAMS)

            # 随机选择中间体
            hps_semi_ids = []
            ss_semi_ids = []
            for st in hps_needed:
                if st in hps_by_sero and hps_by_sero[st]:
                    chosen = random.choice(hps_by_sero[st])
                    hps_semi_ids.append(f"SEMI-{chosen['batch_id']}")
            for st in ss_needed:
                st_str = str(st)
                if st_str in ss_by_sero and ss_by_sero[st_str]:
                    chosen = random.choice(ss_by_sero[st_str])
                    ss_semi_ids.append(f"SEMI-{chosen['batch_id']}")

            anomaly_key = (variant, n)
            anomaly = COMBO_ANOMALIES.get(anomaly_key)
            status = "rejected" if (anomaly and anomaly["severity"] == "critical") else "completed"

            batches.append({
                "batch_id": bid, "variant": variant,
                "hps_serotypes": hps_needed, "ss_serotypes": ss_needed,
                "hps_semi_ids": hps_semi_ids, "ss_semi_ids": ss_semi_ids,
                "status": status, "team": team, "anomaly": anomaly,
            })

    for b in batches:
        cur.execute(f"""INSERT INTO {prod}.hpsss_combo_production_batches
            (batch_id, product_type, combo_variant, hps_serotypes, ss_serotypes,
             hps_semi_ids, ss_semi_ids, adjuvant_type, adjuvant_ratio_pct,
             formulation_volume_l, filling_date, status, operator_team)
            VALUES (%s,'HPSSS_COMBO',%s,%s,%s,%s,%s,'ISA 201 VG',%s,%s,%s,%s,%s)""",
            (b["batch_id"], b["variant"],
             b["hps_serotypes"], [str(s) for s in b["ss_serotypes"]],
             b["hps_semi_ids"], b["ss_semi_ids"],
             norm(50, 3, 2), norm(200, 30, 2),
             date(int(b["batch_id"].split("-")[1]), random.randint(1, 12), random.randint(1, 28)),
             b["status"], b["team"]))
    conn.commit()

    # 连苗半成品
    semi_rows = []
    for b in batches:
        sid = f"SEMI-{b['batch_id']}"
        anomaly = b["anomaly"]
        sterility = "PASS"
        ph = norm(6.9, 0.1, 1)
        if anomaly and anomaly["type"] == "ph_deviation":
            ph = 6.3
        semi_rows.append((sid, b["batch_id"], norm(180, 25, 2),
            norm(500, 60, 2), norm(50, 3, 2), norm(50, 3, 2),
            "ISA 201 VG", sterility, norm(3, 1.5, 2),
            ph, "乳白色均匀乳剂"))
    execute_batch(conn, f"INSERT INTO {prod}.hpsss_combo_semi_product VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", semi_rows)
    print(f"  hpsss_combo_semi_product: {len(semi_rows)} 行")

    conn.commit()
    return batches


# ═══════════════════════════════════════════════════════════
# E. coli 灭活疫苗生产数据
# ═══════════════════════════════════════════════════════════

def gen_ecoli_data(conn):
    """E. coli 灭活疫苗 50 批"""
    cur = conn.cursor()
    prod = "analog_production"
    strains = ["K88", "K99", "987P", "F18", "F41"]

    batches = []
    for i in range(1, 51):
        year = 2024 if i <= 25 else 2025
        n = i if year == 2024 else i - 25
        bid = batch_id("ECOLI", year, n)
        scale = random.choice(SCALES)
        team = OPERATOR_TEAMS[(i-1) % 3]
        start = date(year, random.randint(1, 12), random.randint(1, 28))
        harv = start + timedelta(days=random.randint(3, 6))

        anomaly = ECOLI_ANOMALIES.get(i)
        status = "rejected" if (anomaly and anomaly["severity"] == "critical") else "completed"

        batches.append({
            "batch_id": bid, "scale": scale, "start_date": start,
            "harvest_date": harv, "status": status, "team": team,
            "anomaly": anomaly,
        })

    for b in batches:
        cur.execute(f"""INSERT INTO {prod}.ecoli_production_batches
            (batch_id, product_type, antigen_strains, medium_id,
             bioreactor_scale_l, start_date, harvest_date, status, operator_team)
            VALUES (%s,'ECOLI',%s,'MAT-MED-005',%s,%s,%s,%s,%s)""",
            (b["batch_id"], strains, b["scale"], b["start_date"],
             b["harvest_date"], b["status"], b["team"]))
    conn.commit()

    # 发酵日志（5菌株独立发酵）
    ferm_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        for strain in strains:
            for h in range(0, 12, 2):
                od = norm(1.3 + h * 2.0, 1.4, 3)
                cfu = norm(3e8 + h * 2.5e8, 1.5e8, 2) if h >= 4 else norm(2e7 + h * 3e7, 2e7, 2)
                fimb_elisa = norm(0.8 + h * 0.25, 0.15, 2) if h >= 8 else None
                lt_tox = norm(50, 15, 3) if strain == "F41" and h >= 8 else (norm(80, 20, 3) if strain in ("K88", "K99") and h >= 8 else None)
                if anomaly and anomaly["type"] == "lt_expression_low" and strain == "F41" and h >= 8:
                    lt_tox = norm(15, 5, 3)
                ferm_rows.append((b["batch_id"], strain, float(h),
                    od, norm(6.9, 0.15, 1), norm(38, 7, 1),
                    norm(37.0, 0.3, 1), norm(18 - h * 1.2, 2, 2),
                    cfu, fimb_elisa, lt_tox))
    execute_batch(conn, f"INSERT INTO {prod}.ecoli_fermentation_log (batch_id,strain,fermentation_h,od600,ph,do_pct,temp_c,glucose_g_per_l,cfu_per_ml,fimbriae_expression_elisa,lt_toxin_ug_per_ml) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", ferm_rows)
    print(f"  ecoli_fermentation_log: {len(ferm_rows)} 行")

    # 收获灭活
    harv_rows = []
    for b in batches:
        anomaly = b["anomaly"]
        rid = f"HARV-{b['batch_id']}"
        inact_ver = "PASS"
        if anomaly and anomaly["type"] == "inactivation_incomplete":
            inact_ver = "FAIL (延长灭活后PASS)"
        harv_rows.append((rid, b["batch_id"], b["harvest_date"],
            norm(b["scale"] * 0.65, b["scale"] * 0.05, 2),
            norm(3e9, 8e8, 2), norm(8, 1.5, 2),
            norm(b["scale"] * 0.08, b["scale"] * 0.01, 2),
            "formaldehyde", norm(0.20, 0.02, 2),
            norm(37.0, 0.2, 1), norm(24, 4, 1),
            inact_ver, b["harvest_date"] + timedelta(days=1)))
    execute_batch(conn, f"INSERT INTO {prod}.ecoli_harvest_inactivation VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", harv_rows)
    print(f"  ecoli_harvest_inactivation: {len(harv_rows)} 行")

    # 半成品
    semi_rows = []
    for b in batches:
        sid = f"SEMI-{b['batch_id']}"
        anomaly = b["anomaly"]
        lt_toxoid = norm(100, 15, 2)
        if anomaly and anomaly["type"] == "lt_expression_low":
            lt_toxoid = norm(35, 8, 2)
        endotoxin = norm(3, 1.5, 2)
        if anomaly and anomaly["type"] == "endotoxin_high":
            endotoxin = 8.5
        semi_rows.append((sid, b["batch_id"], norm(b["scale"] * 0.05, b["scale"] * 0.01, 2),
            norm(80, 10, 2), norm(80, 10, 2), norm(80, 10, 2),
            norm(80, 10, 2), norm(80, 10, 2), lt_toxoid,
            "ISA 201 VG", norm(50, 3, 2), "PASS", endotoxin, norm(6.9, 0.1, 1)))
    execute_batch(conn, f"INSERT INTO {prod}.ecoli_semi_product VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", semi_rows)
    print(f"  ecoli_semi_product: {len(semi_rows)} 行")

    conn.commit()
    return batches


# ═══════════════════════════════════════════════════════════
# 质量管理数据生成
# ═══════════════════════════════════════════════════════════

def gen_quality_data(conn, all_batches_info):
    """生成所有 QC/偏差/CAPA/稳定性数据"""
    qual = "analog_quality"
    cur = conn.cursor()

    # 收集所有成品批次的 product_type, batch_id, status
    product_batches = all_batches_info

    # ── in_process_tests ──
    ipt_rows = []
    sample_points = ["pre_inoc", "post_inoc", "mid_culture", "pre_harvest", "post_clarify", "post_inactivation", "pre_lyophilization", "pre_formulation"]
    test_types = ["sterility", "mycoplasma", "bioburden", "endotoxin", "titer", "ph", "osmolality", "glucose", "protein", "cfu", "od600", "purity"]
    testers = ["钱学军", "马晓燕", "朱国强"]

    for info in product_batches:
        pt = info["product_type"]
        bid = info["batch_id"]
        anomaly = info.get("anomaly")
        for sp in random.sample(sample_points, min(3, len(sample_points))):
            for tt in random.sample(test_types, min(2, len(test_types))):
                result = "PASS" if random.random() > 0.05 else "FAIL"
                if anomaly:
                    # 异常批次有更高概率 FAIL
                    if anomaly["severity"] == "critical":
                        result = "FAIL" if random.random() < 0.4 else "PASS"
                    elif anomaly["severity"] == "major":
                        result = "FAIL" if random.random() < 0.2 else "PASS"
                val = f"{norm(100, 20, 1)}" if "titer" in tt or "cfu" in tt else f"{norm(7.0, 0.3, 1)}"
                ipt_rows.append((pt, bid, sp, tt,
                    random_date(date(2024,1,1), date(2026,6,1)),
                    val, "N/A" if result == "PASS" else f"{norm(90, 10, 1)}-{norm(110, 10, 1)}",
                    "N/A" if result == "PASS" else f"{norm(80, 10, 1)}-{norm(120, 10, 1)}",
                    result, random.choice(testers), None))
    execute_batch(conn, f"INSERT INTO {qual}.in_process_tests (product_type,batch_id,sample_point,test_type,test_date,result_value,spec_min,spec_max,pass_fail,tested_by,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", ipt_rows)
    print(f"  in_process_tests: {len(ipt_rows)} 行")

    # ── semi_product_qc ──
    sp_rows = []
    for info in product_batches:
        pt = info["product_type"]
        bid = info["batch_id"]
        anomaly = info.get("anomaly")
        is_rejected = info.get("status") == "rejected"
        potency = norm(38, 5, 2) if pt in ("PEDV", "PRRSV") else norm(480, 55, 2)
        purity = norm(94, 3, 2)
        pass_fail = "FAIL" if is_rejected else "PASS"
        inact_ver = None
        if pt in ("PEDV", "HPS", "SS", "ECOLI", "HPSSS_COMBO"):
            inact_ver = "FAIL" if (anomaly and "inactivation" in anomaly.get("type", "")) else "PASS"
        sp_rows.append((pt, bid, random_date(date(2024,1,1), date(2026,6,1)),
            "PASS", norm(3, 1.5, 2), norm(7.0, 0.1, 1),
            "符合规定", potency, purity, inact_ver,
            norm(0.05, 0.03, 4) if inact_ver else None,
            pass_fail, random.choice(testers), None))
    execute_batch(conn, f"INSERT INTO {qual}.semi_product_qc (product_type,batch_id,test_date,sterility_test,endotoxin_eu_per_dose,ph,appearance,potency,purity_pct,inactivation_verification,residual_inactivant,pass_fail,tested_by,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", sp_rows)
    print(f"  semi_product_qc: {len(sp_rows)} 行")

    # ── final_product_qc ──
    fpq_rows = []
    reviewers = ["沈丽华", "韩雪峰"]
    for info in product_batches:
        pt = info["product_type"]
        bid = info["batch_id"]
        anomaly = info.get("anomaly")
        is_rejected = info.get("status") == "rejected"

        # Determine release decision
        if is_rejected:
            release = "rejected"
        elif anomaly and anomaly["severity"] == "major":
            release = "conditional" if random.random() < 0.6 else "released"
        else:
            release = "released"

        potency_val = norm(40, 5, 2) if pt in ("PEDV",) else (norm(6.8, 0.3, 2) if pt == "PRRSV" else norm(500, 60, 2))
        potency_unit = "U" if pt == "PEDV" else ("log10 TCID50/mL" if pt == "PRRSV" else "ug/dose")
        if anomaly and ("potency" in anomaly.get("type", "") or "low_potency" in anomaly.get("type", "")):
            potency_val = norm(28, 3, 2)
        if anomaly and anomaly.get("type") == "cell_growth_poor":
            potency_val = norm(5.5, 0.2, 2)

        moisture = norm(1.8, 0.5, 2) if pt == "PRRSV" else None
        if anomaly and anomaly.get("type") == "lyo_failure":
            moisture = 5.8
        recon_time = norm_int(35, 15) if pt == "PRRSV" else None
        if anomaly and anomaly.get("type") == "reconstitution_slow":
            recon_time = 95

        sterility_test = "FAIL" if (anomaly and ("sterility" in anomaly.get("type", "") or "contamination" in anomaly.get("type", ""))) else "PASS"
        efficacy = "FAIL" if (anomaly and anomaly.get("type") == "inactivation_failure") else "PASS"
        safety = "PASS"

        fpq_rows.append((f"QC-{bid}", pt, bid,
            random_date(date(2024,1,1), date(2026,6,1)),
            "符合规定", norm(7.0, 0.1, 1),
            sterility_test, norm(3, 2, 2),
            potency_val, potency_unit,
            safety, efficacy, moisture,
            norm(2.0, 0.15, 2) if random.random() > 0.1 else norm(1.82, 0.1, 2),
            norm(1.0, 0.2, 2) if pt != "PRRSV" else None,
            recon_time,
            release, random.choice(reviewers), None))
    execute_batch(conn, f"INSERT INTO {qual}.final_product_qc (qc_report_id,product_type,batch_id,test_date,appearance,ph,sterility_test,endotoxin_eu_per_dose,potency,potency_unit,safety_test,efficacy_test,residual_moisture_pct,filling_volume_ml,adjuvant_content_mg_ml,reconstitution_time_s,release_decision,reviewer,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", fpq_rows)
    print(f"  final_product_qc: {len(fpq_rows)} 行")

    # ── deviations ──
    dev_rows = []
    capa_counter = [0]
    dev_counter = {}

    # 从各产品异常定义生成偏差
    all_anomalies_map = {
        "PEDV": PEDV_ANOMALIES,
        "PRRSV": PRRSV_ANOMALIES,
        "APP": APP_ANOMALIES,
    }
    # HPS anomalies use (serotype, n) keys
    # SS anomalies use (serotype_str, n) keys
    # Combo anomalies use (variant, n) keys
    # E.coli anomalies use numeric keys

    def add_deviation(pt, bid, a):
        nonlocal capa_counter
        year = int(bid.split("-")[1]) if bid.split("-")[1].isdigit() else 2024
        dev_key = pt
        if dev_key not in dev_counter:
            dev_counter[dev_key] = 0
        dev_counter[dev_key] += 1
        d_id = dev_id(pt, year, dev_counter[dev_key])
        capa_id_val = None
        if a["severity"] in ("critical", "major"):
            capa_counter[0] += 1
            capa_id_val = capa_id(year, capa_counter[0])
        dev_rows.append((d_id, pt, bid, a["type"], a["severity"],
            a["desc"], f"根因分析: {a['desc'][:80]}",
            f"纠正措施: {a['desc'][:80]}", capa_id_val,
            random_date(date(2024,1,1), date(2026,6,1)),
            random_date(date(2024,3,1), date(2026,6,1)),
            "closed" if random.random() < 0.8 else "investigation",
            random.choice(["沈丽华", "韩雪峰"])))

    # PEDV anomalies
    for num, a in PEDV_ANOMALIES.items():
        year = 2024 if num <= 26 else 2025
        n = num if year == 2024 else num - 26
        bid = batch_id("PEDV", year, n)
        add_deviation("PEDV", bid, a)

    # PRRSV anomalies
    for num, a in PRRSV_ANOMALIES.items():
        year = 2024 if num <= 20 else 2025
        n = num if year == 2024 else num - 20
        bid = batch_id("PRRSV", year, n)
        add_deviation("PRRSV", bid, a)

    # APP anomalies
    for num, a in APP_ANOMALIES.items():
        year = 2024 if num <= 20 else 2025
        n = num if year == 2024 else num - 20
        bid = batch_id("APP", year, n)
        add_deviation("APP", bid, a)

    # HPS anomalies
    for (sero, n), a in HPS_ANOMALIES.items():
        year = 2024 if n <= 3 else 2025
        num = n if year == 2024 else n - 3
        bid = hps_batch_id(sero, year, num)
        add_deviation("HPS", bid, a)

    # SS anomalies
    for (sero, n), a in SS_ANOMALIES.items():
        year = 2024 if n <= 3 else 2025
        num = n if year == 2024 else n - 3
        bid = ss_batch_id(sero, year, num)
        add_deviation("SS", bid, a)

    # Combo anomalies
    for (variant, n), a in COMBO_ANOMALIES.items():
        year = 2024 if n <= 25 else 2025
        num = n if year == 2024 else n - 25
        bid = combo_batch_id(variant, year, num)
        add_deviation("HPSSS_COMBO", bid, a)

    # E.coli anomalies
    for num, a in ECOLI_ANOMALIES.items():
        year = 2024 if num <= 25 else 2025
        n = num if year == 2024 else num - 25
        bid = batch_id("ECOLI", year, n)
        add_deviation("ECOLI", bid, a)

    # Add a few warehouse/coldchain non-batch deviations
    extra_devs = [
        ("PEDV", "PEDV-2024-0038", "warehouse_humidity", "major",
         "仓储湿度长期超标(85%RH)导致培养基受潮变质",
         "HVAC除湿能力不足", "增加除湿设备+加强监控", "investigation"),
        ("HPSSS_COMBO", "COMBOA-2025-0003", "cold_chain_failure", "major",
         "冷链运输途中冷藏车故障3h，产品温度升至22°C",
         "制冷机组压缩机故障", "维修+更换老化压缩机", "closed"),
    ]
    for pt, bid, d_type, sev, desc, root, action, status in extra_devs:
        capa_counter[0] += 1
        d_id = dev_id(pt, 2025, dev_counter.get(pt, 0) + 1)
        dev_counter[pt] = dev_counter.get(pt, 0) + 1
        dev_rows.append((d_id, pt, bid, d_type, sev, desc, root, action,
            capa_id(2025, capa_counter[0]),
            random_date(date(2024,1,1), date(2026,6,1)),
            date(2026, 6, 1) if status == "investigation" else random_date(date(2025,1,1), date(2026,3,1)),
            status, random.choice(["沈丽华", "韩雪峰"])))

    execute_batch(conn, f"INSERT INTO {qual}.deviations (dev_id,product_type,batch_id,dev_type,severity,description,root_cause,corrective_action,capa_id,reported_date,resolved_date,status,reported_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", dev_rows)
    print(f"  deviations: {len(dev_rows)} 行")

    # ── capa_records ──
    capa_rows = []
    for c_num in range(1, capa_counter[0] + 1):
        cid = capa_id(2024 if c_num <= 15 else 2025, c_num)
        completed = random.random() < 0.7
        capa_rows.append((cid, [f"DEV-xxx-{c_num:04d}"],
            f"CAPA #{c_num} — 纠正与预防措施",
            f"行动方案 #{c_num}", random.choice(["生产部", "QA", "工程部", "QC"]),
            random_date(date(2024,6,1), date(2026,6,1)),
            random_date(date(2024,8,1), date(2026,6,1)) if completed else None,
            completed, random_date(date(2024,9,1), date(2026,6,1)) if completed else None,
            "completed" if completed else random.choice(["in_progress", "overdue"])))
    execute_batch(conn, f"INSERT INTO {qual}.capa_records (capa_id,source_dev_ids,description,action_plan,responsible_dept,due_date,completion_date,effectiveness_verified,verification_date,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", capa_rows)
    print(f"  capa_records: {len(capa_rows)} 行")

    # ── stability_study ──
    stab_rows = []
    # 每产品选 3 批做稳定性
    for pt in PRODUCTS:
        pt_batches = [b for b in product_batches if b["product_type"] == pt]
        sampled = random.sample(pt_batches, min(3, len(pt_batches)))
        for b in sampled:
            bid = b["batch_id"]
            # 长期稳定性 2-8°C
            for m in [0, 3, 6, 9, 12, 18, 24]:
                test_date = date(2024, 1, 1) + timedelta(days=m * 30)
                if test_date > date(2026, 6, 1):
                    continue
                potency_init = 40 if pt in ("PEDV",) else (6.8 if pt == "PRRSV" else 500)
                decay = 0.03 * m if pt != "PRRSV" else 0.05 * m
                potency_val = norm(potency_init * (1 - decay / 100), potency_init * 0.03, 2)
                stab_rows.append((pt, bid, "long_term", "2-8°C", m,
                    test_date, potency_val,
                    "U" if pt in ("PEDV",) else ("log10 TCID50/mL" if pt == "PRRSV" else "ug/dose"),
                    norm(7.0, 0.1, 1), "符合规定", "PASS", norm(3, 1, 2),
                    norm(2.0, 0.5, 2) if pt == "PRRSV" else None, "PASS"))
            # 加速稳定性 25°C
            for m in [0, 1, 3, 6]:
                test_date = date(2024, 1, 1) + timedelta(days=m * 30)
                if test_date > date(2026, 6, 1):
                    continue
                potency_init = 40 if pt in ("PEDV",) else (6.8 if pt == "PRRSV" else 500)
                decay = 0.15 * m
                potency_val = norm(potency_init * (1 - decay / 100), potency_init * 0.05, 2)
                pass_fail_stab = "PASS" if decay < 0.35 else "FAIL"
                stab_rows.append((pt, bid, "accelerated", "25°C/60%RH", m,
                    test_date, potency_val,
                    "U" if pt in ("PEDV",) else ("log10 TCID50/mL" if pt == "PRRSV" else "ug/dose"),
                    norm(7.0, 0.2, 1), "符合规定" if pass_fail_stab == "PASS" else "轻微变色",
                    "PASS", norm(5, 2, 2),
                    norm(2.5, 0.8, 2) if pt == "PRRSV" else None,
                    pass_fail_stab))
    execute_batch(conn, f"INSERT INTO {qual}.stability_study (product_type,batch_id,study_type,storage_condition,time_point_months,test_date,potency,potency_unit,ph,appearance,sterility_test,endotoxin_eu_per_dose,residual_moisture_pct,pass_fail) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", stab_rows)
    print(f"  stability_study: {len(stab_rows)} 行")

    conn.commit()


# ═══════════════════════════════════════════════════════════
# 仓储数据生成
# ═══════════════════════════════════════════════════════════

def gen_warehouse_data(conn, product_batches):
    """仓储温湿度、异常、物料检验、BOM消耗"""
    wh = "analog_warehouse"
    prod = "analog_production"
    cur = conn.cursor()

    # ── warehouse_monitoring (2024-01 ~ 2026-06, 每小时) ──
    wh_rows = []
    zones = ["物料仓库A", "物料仓库B", "成品仓库", "冷库区"]
    start_ts = datetime(2024, 1, 1, 0, 0, 0)
    end_ts = datetime(2026, 6, 30, 23, 0, 0)
    current = start_ts
    while current <= end_ts:
        if random.random() < 0.3:  # 采样率约30%（每小时1条)
            for zone in random.sample(zones, 2):
                temp = norm(22, 3, 1) if "冷库" not in zone else norm(5, 2, 1)
                humidity = norm(55, 10, 1)
                alarm = False
                # 7月湿度超标 (2024-07)
                if zone == "物料仓库A" and current.year == 2024 and current.month == 7:
                    humidity = norm(82, 5, 1)
                    alarm = humidity > 80
                # 8月冷库故障
                if zone == "冷库区" and current.year == 2024 and current.month == 8 and current.day in (15, 16, 17):
                    temp = norm(15, 3, 1)
                    alarm = True
                wh_rows.append((current, temp, humidity, zone, alarm))
        current += timedelta(hours=1)
    execute_batch(conn, f"INSERT INTO {wh}.warehouse_monitoring (monitor_ts,temp_c,humidity_pct,zone,alarm_flag) VALUES (%s,%s,%s,%s,%s)", wh_rows)
    print(f"  warehouse_monitoring: {len(wh_rows)} 行")

    # ── storage_excursions ──
    excursions = [
        ("EXC-2024-001", datetime(2024, 7, 5, 14, 0), datetime(2024, 7, 20, 8, 0),
         "物料仓库A", "humidity", 88.5, 358.0,
         ["MAT-MED-001", "MAT-MED-009", "MAT-MED-011"],
         "连续降雨+除湿机故障导致湿度超标，培养基包装密封性受破坏"),
        ("EXC-2024-002", datetime(2024, 8, 15, 3, 0), datetime(2024, 8, 17, 22, 0),
         "冷库区", "temperature", None, 67.0,
         ["MAT-ADJ-002"],
         "冷库#3制冷机组压缩机故障，温度升至18°C，ISA 206佐剂物理性质改变"),
        ("EXC-2025-001", datetime(2025, 1, 10, 6, 0), datetime(2025, 1, 10, 14, 0),
         "物料仓库B", "humidity", 76.0, 8.0,
         ["MAT-CHR-005"],
         "短暂湿度偏高，层析介质受潮风险，已转移至干燥柜"),
        ("EXC-2025-002", datetime(2025, 3, 20, 18, 0), datetime(2025, 3, 21, 4, 0),
         "成品仓库", "temperature", None, 10.0,
         ["MAT-PKG-001", "MAT-PKG-002"],
         "HVAC送风温度传感器故障，温度短暂异常"),
        ("EXC-2025-003", datetime(2025, 6, 5, 10, 0), datetime(2025, 6, 6, 16, 0),
         "物料仓库A", "humidity", 82.0, 30.0,
         ["MAT-REG-009"],
         "Guanidine HCl储存区湿度超标，化学试剂吸潮风险"),
        ("EXC-2026-001", datetime(2026, 2, 14, 8, 0), datetime(2026, 2, 14, 20, 0),
         "冷库区", "temperature", None, 12.0,
         ["MAT-ADJ-001"],
         "冷库门未完全关闭导致温度波动，已纠正"),
    ]
    exc_rows = [(e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[8], None) for e in excursions]
    execute_batch(conn, f"INSERT INTO {wh}.storage_excursions (excursion_id,start_ts,end_ts,zone,excursion_type,max_humidity_pct,duration_hours,affected_material_ids,root_cause,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", exc_rows)
    print(f"  storage_excursions: {len(exc_rows)} 行")

    # ── material_quality_inspection ──
    mqi_rows = []
    materials = ["MAT-MED-001", "MAT-MED-002", "MAT-MED-003", "MAT-MED-005",
                 "MAT-REG-001", "MAT-REG-002", "MAT-REG-006", "MAT-REG-009",
                 "MAT-ADJ-001", "MAT-ADJ-002", "MAT-ADJ-003",
                 "MAT-PKG-001", "MAT-PKG-004", "MAT-PKG-007",
                 "MAT-LYO-001", "MAT-LYO-004",
                 "MAT-CHR-001", "MAT-CHR-005"]
    for mat_id in materials:
        for lot_suffix in ["L1", "L2"]:
            lot = f"INSP-LOT-{mat_id}-{lot_suffix}"
            test_items = ["外观", "pH", "纯度", "无菌", "内毒素", "鉴别"]
            for item in random.sample(test_items, 2):
                pass_f = "FAIL" if random.random() < 0.1 else "PASS"
                if mat_id == "MAT-REG-009" and pass_f == "FAIL":
                    pass_f = "FAIL"
                if mat_id == "MAT-CHR-005":
                    pass_f = "FAIL" if random.random() < 0.4 else "PASS"
                supplier_id = "SUP-001" if "MED" in mat_id else ("SUP-002" if "REG" in mat_id or "CHR" in mat_id else
                              ("SUP-003" if "ADJ" in mat_id else ("SUP-004" if "PKG" in mat_id else "SUP-005")))
                mqi_rows.append((mat_id, supplier_id, lot,
                    random_date(date(2024, 1, 1), date(2026, 6, 1)),
                    item, norm(98, 1, 1) if pass_f == "PASS" else norm(75, 10, 1),
                    "≥95", "≤105" if item != "内毒素" else "≤10 EU",
                    pass_f, random.choice(["钱学军", "马晓燕", "朱国强"]), None))
    execute_batch(conn, f"INSERT INTO {wh}.material_quality_inspection (material_id,supplier_id,lot_number,inspection_date,test_item,result_value,spec_min,spec_max,pass_fail,inspector,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", mqi_rows)
    print(f"  material_quality_inspection: {len(mqi_rows)} 行")

    # ── batch_material_usage ──
    bmu_rows = []
    # 为每种产品的部分批次生成物料消耗记录
    for b in random.sample(product_batches, min(80, len(product_batches))):
        bid = b["batch_id"]
        pt = b["product_type"]
        mats = ["MAT-PKG-001", "MAT-PKG-002", "MAT-PKG-003", "MAT-PKG-005"]
        if pt in ("PEDV", "PRRSV"):
            mats += ["MAT-MED-001", "MAT-MED-002", "MAT-MED-011", "MAT-REG-006"]
        if pt == "PRRSV":
            mats += ["MAT-LYO-001", "MAT-LYO-003"]
        if pt == "APP":
            mats += ["MAT-MED-005", "MAT-REG-003", "MAT-REG-004", "MAT-REG-005", "MAT-CHR-001", "MAT-ADJ-001"]
        if pt in ("HPS", "SS"):
            mats += ["MAT-MED-003", "MAT-MED-011", "MAT-REG-002", "MAT-ADJ-001"]
        if pt == "HPSSS_COMBO":
            mats += ["MAT-ADJ-001", "MAT-PKG-001", "MAT-PKG-008"]
        if pt == "ECOLI":
            mats += ["MAT-MED-005", "MAT-MED-011", "MAT-REG-002", "MAT-ADJ-001"]
        for mat in random.sample(mats, min(4, len(mats))):
            planned = norm(100, 15, 2)
            bmu_rows.append((bid, mat, planned, planned * norm(1, 0.05, 2),
                "kg" if "MED" in mat or "REG" in mat or "LYO" in mat else ("L" if "ADJ" in mat or "CHR" in mat else "pcs"),
                random_date(date(2024, 1, 1), date(2026, 6, 1)),
                random.choice(["张建国", "李明辉", "王海燕", "陈大伟", "孙文博"]), None))
    execute_batch(conn, f"INSERT INTO {prod}.batch_material_usage (batch_id,material_id,planned_qty,actual_qty,unit,consumed_date,operator,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", bmu_rows)
    print(f"  batch_material_usage: {len(bmu_rows)} 行")

    conn.commit()


# ═══════════════════════════════════════════════════════════
# 冷链数据生成
# ═══════════════════════════════════════════════════════════

def gen_coldchain_data(conn, product_batches):
    """冷链储存和运输"""
    cc = "analog_coldchain"
    cur = conn.cursor()

    # ── cold_storage_log ──
    csl_rows = []
    locations = ["冷库#1-A区", "冷库#1-B区", "冷库#2-A区", "冰箱#1", "冰箱#2"]
    for b in random.sample(product_batches, min(60, len(product_batches))):
        bid = b["batch_id"]
        pt = b["product_type"]
        loc = random.choice(locations)
        # 每批约 50 个温度点
        start_ts = datetime(2024, random.randint(1, 12), random.randint(1, 28), 0, 0, 0)
        for i in range(50):
            ts = start_ts + timedelta(hours=i * 3)
            if ts > datetime(2026, 6, 30, 23, 0, 0):
                break
            temp = norm(5.0, 1.5, 1)
            alarm = False
            if random.random() < 0.03:
                temp = norm(10, 2, 1) if random.random() < 0.5 else norm(0, 2, 1)
                alarm = True
            csl_rows.append((pt, bid, ts, temp, norm(45, 10, 1), loc, alarm))
    execute_batch(conn, f"INSERT INTO {cc}.cold_storage_log (product_type,batch_id,monitor_ts,temp_c,humidity_pct,storage_location,alarm_flag) VALUES (%s,%s,%s,%s,%s,%s,%s)", csl_rows)
    print(f"  cold_storage_log: {len(csl_rows)} 行")

    # ── transport_monitoring ──
    trans_rows = []
    routes = [("疫苗企业-成都仓库", "四川省动物疫控中心"), ("疫苗企业-成都仓库", "重庆市动物疫控中心"),
              ("疫苗企业-成都仓库", "贵州省动物疫控中心"), ("疫苗企业-成都仓库", "云南省动物疫控中心"),
              ("疫苗企业-成都仓库", "广西动物疫控中心"), ("疫苗企业-成都仓库", "广东省动物疫控中心")]
    for b in random.sample(product_batches, min(120, len(product_batches))):
        bid = b["batch_id"]
        pt = b["product_type"]
        route = random.choice(routes)
        dep = datetime(2024, random.randint(1, 12), random.randint(1, 28), random.randint(6, 18), 0, 0)
        arr = dep + timedelta(hours=random.randint(4, 48))
        mkt = norm(5.5, 1.2, 2)
        temp_max = norm(7, 1, 1)
        exc_count = 0
        exc_dur = 0
        shock = False
        assessment = "合格"
        # 冷链断链场景
        shipment = f"SHIP-{bid}"
        anomaly = b.get("anomaly")
        if (anomaly and "cold_chain" in anomaly.get("type", "")) or shipment == "SHIP-PEDV-2025-0025":
            temp_max = 22.0
            mkt = 12.3
            exc_count = 3
            exc_dur = 180
            assessment = "降级放行(稳定性加测后)"
        trans_rows.append((shipment, pt, bid, route[0], route[1],
            dep, arr, "冷藏车(2-8°C)", norm(2, 1, 1), temp_max,
            exc_count, exc_dur, mkt, shock, assessment))
    execute_batch(conn, f"INSERT INTO {cc}.transport_monitoring (shipment_id,product_type,batch_id,route_from,route_to,departure_time,arrival_time,vehicle_type,temp_min_c,temp_max_c,temp_excursion_count,temp_excursion_duration_min,mkt_c,shock_exceeded,product_assessment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", trans_rows)
    print(f"  transport_monitoring: {len(trans_rows)} 行")

    conn.commit()


# ═══════════════════════════════════════════════════════════
# 设备数据生成
# ═══════════════════════════════════════════════════════════

def gen_equipment_data(conn):
    """校准、维护、维修、WFI"""
    eq = "analog_equipment"
    cur = conn.cursor()

    # ── equipment_calibration ──
    cal_rows = []
    cal_items = {
        "EQ-001": ("温度传感器", "压力传感器"), "EQ-002": ("温度传感器", "DO电极"),
        "EQ-003": ("pH电极", "温度传感器"), "EQ-004": ("pH电极", "温度传感器"),
        "EQ-005": ("DO电极", "压力传感器"), "EQ-006": ("温度传感器", "搅拌转速计"),
        "EQ-009": ("温度探头", "真空度传感器"), "EQ-010": ("温度探头", "真空度传感器"),
        "EQ-014": ("UV检测器", "pH电极", "电导率探头"),
        "EQ-016": ("装量校准", "封口力度"), "EQ-017": ("装量校准", "封口力度"),
        "EQ-025": ("电导率探头", "TOC分析仪"),
    }
    for eq_id, items in cal_items.items():
        for item in items:
            cal_date = random_date(date(2023, 1, 1), date(2025, 12, 31))
            due = cal_date + timedelta(days=random.choice([180, 365, 365, 365, 365, 540]))
            result = "PASS" if random.random() < 0.88 else "FAIL"
            # 确保有逾期设备
            if eq_id in ("EQ-003", "EQ-009", "EQ-014"):
                due = date(2025, 3, 1)  # 已逾期
            cal_rows.append((eq_id, item, cal_date, due, result,
                random.choice(["何伟", "蔡明宇"]),
                f"CERT-{eq_id}-{item[:4]}-{cal_date.year}", None))
    execute_batch(conn, f"INSERT INTO {eq}.equipment_calibration (equipment_id,calibration_item,calibration_date,due_date,calibration_result,calibrated_by,certificate_number,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", cal_rows)
    print(f"  equipment_calibration: {len(cal_rows)} 行")

    # ── maintenance_schedule ──
    maint_sched = []
    for eq_id in [f"EQ-{n:03d}" for n in range(1, 28)]:
        freq = random.choice([3, 6, 6, 12, 12])
        last = random_date(date(2024, 1, 1), date(2025, 12, 31))
        next_d = last + timedelta(days=freq * 30)
        status = "on_track"
        if next_d < date(2026, 5, 1):
            status = "overdue" if random.random() < 0.3 else "upcoming"
        maint_sched.append((eq_id, random.choice(["preventive", "predictive"]),
            freq, last, next_d, "工程部", status))
    execute_batch(conn, f"INSERT INTO {eq}.maintenance_schedule (equipment_id,maintenance_type,frequency_months,last_maintenance_date,next_maintenance_date,responsible_team,status) VALUES (%s,%s,%s,%s,%s,%s,%s)", maint_sched)
    print(f"  maintenance_schedule: {len(maint_sched)} 行")

    # ── maintenance_log ──
    maint_logs = [
        ("EQ-012", date(2024, 5, 20), "离心机运行中振动超标，自动停机", "YES", ["PEDV-2024-0024"],
         "更换轴承+动平衡校准", date(2024, 5, 21), 18.0, "DEV-PEDV-2024-0006", "何伟"),
        ("EQ-003", date(2024, 9, 10), "DO电极响应迟缓，读数偏低20%", "YES", ["PEDV-2024-0028"],
         "更换DO电极膜片+重新校准", date(2024, 9, 10), 4.0, "DEV-PEDV-2024-0011", "蔡明宇"),
        ("EQ-006", date(2024, 11, 5), "搅拌桨机械密封泄漏", "YES", ["HPS4-2024-0005"],
         "更换机械密封+润滑油", date(2024, 11, 6), 18.0, "DEV-HPS-2024-0007", "何伟"),
        ("EQ-009", date(2025, 1, 15), "冻干机真空泵油位过低，真空度不达标", "YES", ["PRRSV-2024-0012"],
         "更换真空泵油+真空泄漏测试", date(2025, 1, 15), 6.0, "DEV-PRRSV-2025-0002", "蔡明宇"),
        ("EQ-016", date(2025, 3, 8), "灌装机陶瓷泵密封圈磨损，装量偏差超标", "YES", ["PEDV-2025-0022"],
         "更换陶瓷泵密封圈+装量验证", date(2025, 3, 9), 12.0, "DEV-PEDV-2025-0015", "何伟"),
        ("EQ-005", date(2025, 4, 12), "发酵罐供气系统管路泄漏，DO降至2%", "YES", ["HPS13-2025-0003"],
         "更换供气管路+气密性测试", date(2025, 4, 12), 8.0, "DEV-HPS-2025-0008", "蔡明宇"),
        ("EQ-022", date(2024, 8, 15), "冷库#3制冷压缩机故障，温度升至18°C", "YES", ["PEDV-2024-0040"],
         "更换压缩机+温度分布验证", date(2024, 8, 17), 48.0, "DEV-PEDV-2024-0014", "何伟"),
        ("EQ-003", date(2025, 6, 1), "反应器DO控制波动增大，怀疑供氧管路部分堵塞", "YES", ["PEDV-2025-0023"],
         "清洗供氧管路+更换DO电极", date(2025, 6, 2), 14.0, None, "蔡明宇"),
        ("EQ-003", date(2025, 10, 15), "温度控制精度下降±0.5°C→±1.5°C", "YES", ["PEDV-2025-0036"],
         "校准温度传感器+更换PID控制器", date(2025, 10, 16), 10.0, None, "何伟"),
        ("EQ-016", date(2025, 8, 22), "灌装机传送带偏移，西林瓶卡顿", "YES", ["COMBOA-2025-0017"],
         "调整传送带张力+轨道校准", date(2025, 8, 22), 3.0, None, "蔡明宇"),
        ("EQ-009", date(2025, 9, 5), "冻干机搁板温度不均匀(±3°C)", "YES", ["PRRSV-2025-0018"],
         "更换导热硅脂+搁板温度分布验证", date(2025, 9, 6), 10.0, None, "何伟"),
        ("EQ-025", date(2025, 7, 3), "WFI系统产水电导率偏高(1.8 μS/cm)", "NO", [],
         "更换RO膜+EDI模块清洗", date(2025, 7, 4), 20.0, None, "蔡明宇"),
    ]
    log_rows = [(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9]) for row in maint_logs]
    execute_batch(conn, f"INSERT INTO {eq}.maintenance_log (equipment_id,failure_date,failure_description,impact_on_production,affected_batch_ids,repair_action,repair_completion_date,downtime_hours,dev_id,technician) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", log_rows)
    print(f"  maintenance_log: {len(log_rows)} 行")

    # ── wfi_monitoring ──
    wfi_rows = []
    sample_points_wfi = ["WFI-总送", "WFI-总回", "WFI-使用点1(细胞培养)", "WFI-使用点2(纯化)", "WFI-使用点3(灌装)"]
    for sp in sample_points_wfi:
        for d in daterange(date(2024, 1, 1), date(2026, 6, 30)):
            if random.random() < 0.15:  # 采样频率 ~15%
                cond = norm(0.6, 0.2, 2)
                toc = norm(80, 30, 1)
                endo = norm(0.05, 0.03, 3)
                microb = norm_int(2, 2)
                pf = "PASS"
                if random.random() < 0.02:  # 2% 异常率
                    cond = norm(1.5, 0.3, 2)
                    pf = "FAIL"
                wfi_rows.append((sp, d, cond, toc, endo, microb, pf, random.choice(["何伟", "蔡明宇"])))
    execute_batch(conn, f"INSERT INTO {eq}.wfi_monitoring (sample_point,sample_date,conductivity_us_cm,toc_ppb,endotoxin_eu_per_ml,microbial_limit_cfu_per_100ml,pass_fail,tested_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", wfi_rows)
    print(f"  wfi_monitoring: {len(wfi_rows)} 行")

    conn.commit()


# ═══════════════════════════════════════════════════════════
# AEFI 数据生成
# ═══════════════════════════════════════════════════════════

def gen_aefi_data(conn, product_batches):
    """不良反应报告"""
    cur = conn.cursor()

    aefi_rows = []
    # 发热病例（用于信号检测）
    fever_batches = [
        ("PEDV", "PEDV-2024-0003"), ("PEDV", "PEDV-2024-0009"),
        ("PEDV", "PEDV-2024-0015"), ("PEDV", "PEDV-2025-0003"),
        ("PEDV", "PEDV-2025-0007"), ("PEDV", "PEDV-2025-0011"),
        ("PRRSV", "PRRSV-2024-0005"), ("PRRSV", "PRRSV-2025-0008"),
        ("HPS", "HPS4-2024-0002"), ("HPS", "HPS5-2025-0003"),
        ("SS", "SS7-2024-0004"), ("SS", "SS2-2025-0003"),
        ("HPSSS_COMBO", "COMBOA-2024-0008"), ("HPSSS_COMBO", "COMBOC-2025-0005"),
        ("ECOLI", "ECOLI-2024-0006"), ("ECOLI", "ECOLI-2025-0003"),
    ]

    for pt, bid in fever_batches:
        temp = norm(40.2, 0.4, 1)
        onset = norm_int(4, 2)
        dur = norm_int(24, 8)
        severity = "moderate" if temp < 41.0 else "severe"
        outcome = "recovered"
        causality = "possibly_related" if random.random() < 0.7 else "probably_related"
        aefi_rows.append((pt, bid, random_date(date(2024, 1, 1), date(2026, 6, 1)),
            norm_int(60, 20), f"发热({temp}°C)", severity, onset, dur,
            outcome, causality, f"接种后{onset}h出现发热，{dur}h后退热"))

    # 局部反应
    for _ in range(10):
        b = random.choice(product_batches)
        aefi_rows.append((b["product_type"], b["batch_id"],
            random_date(date(2024, 1, 1), date(2026, 6, 1)),
            norm_int(70, 30), "注射部位红肿/硬结", "mild",
            norm_int(12, 6), norm_int(48, 24), "recovered",
            "possibly_related", "局部反应，自行消退"))

    # SAE
    sae_cases = [
        ("PEDV", "PEDV-2024-0008", "过敏性休克", "severe"),
        ("PRRSV", "PRRSV-2025-0012", "呼吸道窘迫", "severe"),
    ]
    for pt, bid, symptom, sev in sae_cases:
        aefi_rows.append((pt, bid, random_date(date(2024, 1, 1), date(2026, 6, 1)),
            norm_int(65, 15), symptom, sev,
            norm_int(2, 1), norm_int(72, 24), "recovered_with_intervention",
            "probably_related", f"严重不良事件: {symptom}，经医疗干预后恢复"))

    # 死亡案例
    aefi_rows.append(("ECOLI", "ECOLI-2025-0012",
        random_date(date(2025, 1, 1), date(2025, 12, 31)),
        45, "急性休克致死", "fatal", 1, 24, "fatal",
        "possibly_related", "接种后1h出现急性休克，抢救无效死亡，尸检未发现疫苗直接因果"))

    execute_batch(conn, "INSERT INTO analog_pv.aefi_reports (product_type,batch_id,report_date,patient_age_months,symptom,severity,onset_hours_post_vaccination,duration_hours,outcome,causality_assessment,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", aefi_rows)
    print(f"  aefi_reports: {len(aefi_rows)} 行")
    conn.commit()


# ═══════════════════════════════════════════════════════════
# 验证
# ═══════════════════════════════════════════════════════════

def verify_all(conn):
    """数据完整性验证"""
    cur = conn.cursor()
    print("\n" + "="*60)
    print("  数据验证")
    print("="*60)

    # Schema 数
    cur.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name LIKE 'analog_%'")
    schema_count = cur.fetchone()[0]
    print(f"\n  Schema 数: {schema_count} (预期 7)")

    # 各 schema 表数
    total_tables = 0
    total_rows = 0
    for schema in SCHEMAS:
        cur.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{schema}'")
        tb = cur.fetchone()[0]
        total_tables += tb

        schema_rows = 0
        cur.execute(f"""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = '{schema}' AND table_type = 'BASE TABLE'
        """)
        for (tname,) in cur.fetchall():
            try:
                cur.execute(f"SELECT COUNT(*) FROM {schema}.{tname}")
                cnt = cur.fetchone()[0]
                schema_rows += cnt
                if cnt > 0:
                    print(f"    {schema}.{tname}: {cnt} 行")
            except Exception as e:
                print(f"    {schema}.{tname}: ERROR {e}")
        total_rows += schema_rows

    print(f"\n  表总数: {total_tables}")
    print(f"  总行数: {total_rows}")

    # 异常批次分布
    print("\n  [偏差严重等级分布]")
    cur.execute("SELECT severity, COUNT(*) FROM analog_quality.deviations GROUP BY severity ORDER BY severity")
    for sev, cnt in cur.fetchall():
        print(f"    {sev}: {cnt}")

    # 各产品批次数
    print("\n  [各产品批次分布]")
    for tbl_prefix, pt_label in [
        ("pedv_production_batches", "PEDV"),
        ("prrsv_production_batches", "PRRSV"),
        ("app_production_batches", "APP"),
        ("hps_production_batches", "HPS (中间体)"),
        ("ss_production_batches", "SS (中间体)"),
        ("hpsss_combo_production_batches", "HPS+SS 连苗"),
        ("ecoli_production_batches", "E. coli"),
    ]:
        try:
            cur.execute(f"SELECT COUNT(*) FROM analog_production.{tbl_prefix}")
            print(f"    {pt_label}: {cur.fetchone()[0]} 批")
        except:
            pass

    # 放行决策分布
    print("\n  [放行决策分布]")
    cur.execute("SELECT release_decision, COUNT(*) FROM analog_quality.final_product_qc GROUP BY release_decision")
    for dec, cnt in cur.fetchall():
        print(f"    {dec}: {cnt}")

    print("\n  验证完成.")


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(description="疫苗生产模拟数据生成器 v3.0")
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
    print("  疫苗生产模拟数据生成器 v3.0")
    print(f"  7 Schema × ~40 表 × 6 产品")
    print(f"  连接: {args.host}:{args.port}/{args.db}")
    print("=" * 60)

    conn = connect_db(args)

    # Phase 1: DDL
    print("\n[Phase 1] 创建 Schema 与 DDL...")
    generate_ddl(conn)

    # Phase 2: 参考数据
    print("\n[Phase 2] 生成参考数据...")
    gen_suppliers(conn)
    gen_materials(conn)
    gen_equipment_master(conn)
    gen_personnel(conn)

    # Phase 3: 生产数据
    print("\n[Phase 3] 生成生产数据...")
    print("  [PEDV 灭活疫苗]")
    pedv_batches = gen_pedv_data(conn)
    print("  [PRRSV 弱毒活苗]")
    prrsv_batches = gen_prrsv_data(conn)
    print("  [APP 亚单位疫苗]")
    app_batches = gen_app_data(conn)
    print("  [HPS 灭活中间体]")
    hps_batches = gen_hps_data(conn)
    print("  [SS 灭活中间体]")
    ss_batches = gen_ss_data(conn)
    print("  [HPS+SS 连苗成品]")
    combo_batches = gen_combo_data(conn, hps_batches, ss_batches)
    print("  [E. coli 灭活疫苗]")
    ecoli_batches = gen_ecoli_data(conn)

    # 收集所有批次信息用于质量管理
    all_batches = []
    for b in pedv_batches:
        all_batches.append({"product_type": "PEDV", "batch_id": b["batch_id"],
                           "status": b["status"], "anomaly": b["anomaly"]})
    for b in prrsv_batches:
        all_batches.append({"product_type": "PRRSV", "batch_id": b["batch_id"],
                           "status": b["status"], "anomaly": b["anomaly"]})
    for b in app_batches:
        all_batches.append({"product_type": "APP", "batch_id": b["batch_id"],
                           "status": b["status"], "anomaly": b["anomaly"]})
    for b in hps_batches:
        all_batches.append({"product_type": "HPS", "batch_id": b["batch_id"],
                           "status": b["status"], "anomaly": b["anomaly"]})
    for b in ss_batches:
        all_batches.append({"product_type": "SS", "batch_id": b["batch_id"],
                           "status": b["status"], "anomaly": b["anomaly"]})
    for b in combo_batches:
        all_batches.append({"product_type": "HPSSS_COMBO", "batch_id": b["batch_id"],
                           "status": b["status"], "anomaly": b["anomaly"]})
    for b in ecoli_batches:
        all_batches.append({"product_type": "ECOLI", "batch_id": b["batch_id"],
                           "status": b["status"], "anomaly": b["anomaly"]})

    # Phase 4: 质量管理
    print("\n[Phase 4] 生成质量管理数据...")
    gen_quality_data(conn, all_batches)

    # Phase 5: 仓储冷链
    print("\n[Phase 5] 生成仓储与冷链数据...")
    gen_warehouse_data(conn, all_batches)
    gen_coldchain_data(conn, all_batches)

    # Phase 6: 设备
    print("\n[Phase 6] 生成设备数据...")
    gen_equipment_data(conn)

    # Phase 7: AEFI
    print("\n[Phase 7] 生成 AEFI 数据...")
    gen_aefi_data(conn, all_batches)

    # Phase 8: 验证
    verify_all(conn)

    conn.close()
    print("\n数据生成完成!")


if __name__ == "__main__":
    main()
