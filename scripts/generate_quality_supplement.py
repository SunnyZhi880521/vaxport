#!/usr/bin/env python3
"""质量管理补充数据生成器

补充 6 张表：OOS/OOT/变更控制/清洁验证，数据量与现有库匹配。
好坏数据混合，模拟真实业务场景。

用法:
    python scripts/generate_quality_supplement.py [--host localhost] [--port 5432] \
        [--db myappdb] [--user postgres] [--password xxx]
"""

import argparse
import os
import random
import sys
from datetime import date, timedelta

import psycopg2
from psycopg2 import sql

SEED = 99
random.seed(SEED)

QUAL = "analog_quality"

PRODUCTS = ["PEDV", "PRRSV", "APP", "HPS", "SS", "HPSSS_COMBO", "ECOLI", "FLU"]

PRODUCT_BATCHES = {
    "PEDV": 50, "PRRSV": 50, "APP": 50,
    "HPS": 110, "SS": 90, "HPSSS_COMBO": 150,
    "ECOLI": 50, "FLU": 50,
}

EQUIPMENT_IDS = [
    "EQ-001", "EQ-002", "EQ-003", "EQ-004", "EQ-005", "EQ-006",
    "EQ-007", "EQ-008", "EQ-009", "EQ-010", "EQ-011", "EQ-012",
    "EQ-013", "EQ-014", "EQ-015", "EQ-016", "EQ-017", "EQ-018",
    "EQ-019", "EQ-020", "EQ-021", "EQ-022", "EQ-023", "EQ-024",
    "EQ-025", "EQ-026", "EQ-027", "EQ-028",
]

CLEANING_EQUIPMENT = [
    "EQ-001", "EQ-002", "EQ-003", "EQ-004", "EQ-005", "EQ-006",
    "EQ-007", "EQ-008", "EQ-011", "EQ-012", "EQ-013",
    "EQ-014", "EQ-015", "EQ-016", "EQ-017", "EQ-028",
]

ANALYSTS = ["沈丽华", "韩雪峰", "张伟", "李娜", "王芳", "赵敏", "陈静", "刘洋"]


def rdate(start, end):
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, max(0, delta)))


def bid(product, year, num):
    return f"{product}-{year}-{num:04d}"


def oos_id(year, num):
    return f"OOS-{year}-{num:04d}"


def oot_id(year, num):
    return f"OOT-{year}-{num:04d}"


def cc_id(year, num):
    return f"CC-{year}-{num:04d}"


def ra_id(year, num):
    return f"RA-{year}-{num:04d}"


def cv_id(year, num):
    return f"CV-{year}-{num:04d}"


def cl_id(year, num):
    return f"CL-{year}-{num:04d}"


# ═══════════════════════════════════════════════════════════
# DDL
# ═══════════════════════════════════════════════════════════

DDL_STATEMENTS = [
    f"DROP TABLE IF EXISTS {QUAL}.cleaning_validation_limits CASCADE",
    f"DROP TABLE IF EXISTS {QUAL}.cleaning_validation_results CASCADE",
    f"DROP TABLE IF EXISTS {QUAL}.change_control_risk_assessment CASCADE",
    f"DROP TABLE IF EXISTS {QUAL}.change_control_records CASCADE",
    f"DROP TABLE IF EXISTS {QUAL}.oot_records CASCADE",
    f"DROP TABLE IF EXISTS {QUAL}.oos_records CASCADE",
    f"""CREATE TABLE {QUAL}.oos_records (
        oos_id VARCHAR(25) PRIMARY KEY,
        batch_id VARCHAR(30), product_type VARCHAR(15),
        test_type VARCHAR(40), specification VARCHAR(50),
        result_value DECIMAL(15,4), oos_type VARCHAR(20),
        phase_i_findings TEXT, phase_ii_findings TEXT,
        root_cause TEXT, investigation_status VARCHAR(20),
        retest_result DECIMAL(15,4), disposition VARCHAR(20),
        dev_id VARCHAR(25),
        investigation_date DATE, conclusion_date DATE
    )""",
    f"""CREATE TABLE {QUAL}.oot_records (
        oot_id VARCHAR(25) PRIMARY KEY,
        batch_id VARCHAR(30), product_type VARCHAR(15),
        test_parameter VARCHAR(50),
        trend_method VARCHAR(30),
        rule_triggered VARCHAR(50), expected_range VARCHAR(50),
        actual_value DECIMAL(15,4),
        oot_severity VARCHAR(10),
        assessment TEXT,
        detection_date DATE, review_date DATE
    )""",
    f"""CREATE TABLE {QUAL}.change_control_records (
        cc_id VARCHAR(25) PRIMARY KEY,
        cc_type VARCHAR(20),
        description TEXT, reason TEXT,
        risk_level VARCHAR(10),
        impact_assessment TEXT, required_validation VARCHAR(50),
        approval_status VARCHAR(15),
        initiator VARCHAR(30), approver VARCHAR(30),
        related_equipment VARCHAR(15),
        related_capa_id VARCHAR(25),
        initiation_date DATE, target_completion_date DATE,
        actual_completion_date DATE
    )""",
    f"""CREATE TABLE {QUAL}.change_control_risk_assessment (
        assessment_id VARCHAR(25) PRIMARY KEY,
        cc_id VARCHAR(25),
        risk_method VARCHAR(20),
        severity_score INT, probability_score INT, detectability_score INT,
        rpn INT, risk_category VARCHAR(20),
        mitigation_action TEXT, residual_rpn INT,
        assessment_date DATE
    )""",
    f"""CREATE TABLE {QUAL}.cleaning_validation_results (
        validation_id VARCHAR(25) PRIMARY KEY,
        product_type VARCHAR(15), equipment VARCHAR(30),
        worst_case_product VARCHAR(15),
        residue_type VARCHAR(20),
        residue_limit_mg_per_cm2 DECIMAL(8,6),
        actual_residue_mg_per_cm2 DECIMAL(8,6),
        sampling_location VARCHAR(30),
        sampling_method VARCHAR(10),
        acceptance_criteria VARCHAR(50),
        result VARCHAR(10),
        validation_date DATE, analyst VARCHAR(30)
    )""",
    f"""CREATE TABLE {QUAL}.cleaning_validation_limits (
        limit_id VARCHAR(25) PRIMARY KEY,
        product_type VARCHAR(15), residue_type VARCHAR(20),
        calculation_method VARCHAR(20),
        pde_mg_per_day DECIMAL(8,4), safety_factor INT,
        limit_mg_per_cm2 DECIMAL(8,6), limit_mg_per_swab DECIMAL(8,6),
        surface_area_cm2 DECIMAL(10,2), batch_size_doses INT,
        maximum_allowable_carryover_mg DECIMAL(10,4)
    )""",
]


# ═══════════════════════════════════════════════════════════
# OOS Records (~40 rows)
# ═══════════════════════════════════════════════════════════

OOS_SCENARIOS = [
    # (test_type, spec, result_range, is_lab_error, disposition, retest_range, phase_i, phase_ii, root_cause)
    ("potency_assay", ">=32 U", (22.0, 28.0), False, "conditional", None,
     "初次检测效价偏低，复检确认", None,
     "MOI偏低导致病毒增殖不充分，调整MOI至0.05后复检"),
    ("potency_assay", ">=32 U", (18.0, 25.0), False, "rejected", None,
     "效价持续偏低，排除实验室误差", "确认生产OOS，病毒收获时机偏晚",
     "病毒传代次数过多导致毒力衰减，更换低传代毒种"),
    ("sterility_test", "Pass", (None,), True, "released", (None,),
     "无菌检查阳性，疑似取样污染", "复检3次均阴性，确认为假阳性",
     "B级区操作人员手套消毒不规范，重新培训后复检通过"),
    ("sterility_test", "Pass", (None,), True, "released", (None,),
     "无菌检查阳性，培养法检出革兰阳性球菌", "确认为培养基污染，非产品污染",
     "培养基配制时操作台面消毒不彻底，加强消毒SOP后复检通过"),
    ("endotoxin_test", "<=5 EU/dose", (6.5, 12.0), False, "conditional", None,
     "内毒素超标，排除取样误差", "确认为生产OOS，与纯化工艺相关",
     "层析柱清洗不彻底，残留内毒素，更换层析柱后复检合格"),
    ("endotoxin_test", "<=5 EU/dose", (5.8, 8.5), True, "released", (3.2, 4.8),
     "内毒素轻微超标", "复检合格，怀疑为取样器具污染",
     "取样用注射器灭菌不彻底，更换一次性无菌取样器"),
    ("ph_test", "6.8-7.4", (6.2, 6.7), False, "conditional", None,
     "pH偏低，排除仪器校准问题", "确认为生产OOS，缓冲液配制偏差",
     "PBS缓冲液配制时称量误差，重新配制后复检合格"),
    ("ph_test", "6.8-7.4", (7.5, 7.9), True, "released", (7.0, 7.3),
     "pH偏高，怀疑电极老化", "更换电极后复检合格",
     "pH电极老化导致读数漂移，更换电极后复检通过"),
    ("appearance_test", "澄清透明", (None,), False, "rejected", None,
     "外观浑浊，肉眼可见颗粒", "确认为生产OOS，过滤完整性失败",
     "0.22μm滤器完整性测试失败，更换滤器后复检合格"),
    ("purity_test", ">=95%", (88.0, 93.0), False, "conditional", None,
     "纯度偏低，排除检测方法问题", "确认为生产OOS，纯化步骤效率不足",
     "层析流速过快导致分离不完全，调整流速后复检合格"),
    ("potency_assay", ">=6.5 log10 TCID50/mL", (5.8, 6.2), False, "conditional", None,
     "病毒滴度偏低", "确认为生产OOS，细胞密度偏低",
     "细胞接种密度不足，调整接种密度至1.5E6 cells/mL"),
    ("potency_assay", ">=6.5 log10 TCID50/mL", (5.2, 5.9), False, "rejected", None,
     "病毒滴度严重偏低", "确认为生产OOS，培养条件异常",
     "培养温度偏高0.5°C导致病毒复制受限，温控系统校准后复检"),
    ("residual_moisture", "<=3.0%", (3.5, 5.2), False, "conditional", None,
     "残余水分超标", "确认为生产OOS，冻干工艺异常",
     "冻干曲线设置不当，延长二次干燥时间后复检合格"),
    ("potency_assay", ">=450 ug/dose", (380.0, 420.0), False, "conditional", None,
     "蛋白含量偏低", "确认为生产OOS，表达量不足",
     "诱导温度偏高导致蛋白降解，调整至37°C后复检"),
    ("potency_assay", ">=450 ug/dose", (320.0, 380.0), False, "rejected", None,
     "蛋白含量严重偏低", "确认为生产OOS，表达系统故障",
     "IPTG诱导剂批次问题，更换供应商后复检"),
    ("cfu_count", ">=1E8 CFU/mL", (5.0E7, 8.5E7), False, "conditional", None,
     "菌落计数偏低", "确认为生产OOS，发酵条件不佳",
     "溶氧偏低限制菌体生长，提高搅拌转速后复检"),
    ("inactivation_test", "No viable virus", (None,), False, "rejected", None,
     "灭活验证阳性，检出活病毒", "确认为生产OOS，灭活不彻底",
     "BEI浓度偏低+灭活时间不足，调整工艺参数后重新灭活"),
    ("adjuvant_content", "0.8-1.2 mg/mL", (0.5, 0.7), False, "conditional", None,
     "佐剂含量偏低", "确认为生产OOS，乳化不完全",
     "乳化时间不足，延长至45分钟后复检"),
    ("filling_volume", "1.95-2.05 mL", (1.80, 1.90), True, "released", (1.98, 2.02),
     "灌装量偏低", "复检合格，怀疑为取样时机不当",
     "灌装初期管路未充分润洗，正式灌装后复检通过"),
    ("reconstitution_time", "<=60 s", (85.0, 120.0), False, "conditional", None,
     "复溶时间超标", "确认为生产OOS，冻干工艺异常",
     "冻干饼结构致密，调整预冻速率后复检合格"),
    # ── 补充20条：增加实验室OOS占比 + PEDV场景 ──
    ("sterility_test", "Pass", (None,), True, "released", (None,),
     "无菌检查阳性，疑似环境菌污染", "复检5次均阴性，确认为取样环境污染",
     "B级区取样操作不规范，加强无菌操作培训后复检通过"),
    ("sterility_test", "Pass", (None,), True, "released", (None,),
     "无菌检查阳性，培养法检出微球菌", "确认为培养基或试剂污染",
     "培养基批次问题，更换培养基批次后复检通过"),
    ("mycoplasma_test", "Negative", (None,), True, "released", (None,),
     "支原体检查阳性（DNA荧光法）", "培养法确认为阴性，DNA法假阳性",
     "DNA荧光染色试剂盒交叉反应，更换检测试剂盒后复检通过"),
    ("mycoplasma_test", "Negative", (None,), True, "released", (None,),
     "支原体PCR检测阳性", "培养法阴性，确认为PCR污染",
     "PCR实验室气溶胶污染，加强实验室清洁后复检通过"),
    ("endotoxin_test", "<=5 EU/dose", (5.2, 6.8), True, "released", (3.0, 4.5),
     "内毒素轻微超标", "复检合格，怀疑为取样容器污染",
     "玻璃容器清洗不彻底，改用一次性无菌容器"),
    ("endotoxin_test", "<=5 EU/dose", (5.1, 5.9), True, "released", (2.8, 4.2),
     "内毒素边界超标", "复检合格，怀疑为检测试剂问题",
     "LAL试剂灵敏度漂移，更换试剂批次后复检通过"),
    ("ph_test", "6.8-7.4", (7.5, 7.8), True, "released", (6.9, 7.3),
     "pH偏高", "复检合格，怀疑为电极校准偏差",
     "pH计校准液过期，更换校准液后复检通过"),
    ("appearance_test", "澄清透明", (None,), True, "released", (None,),
     "外观轻微浑浊", "复检合格，怀疑为取样瓶不洁净",
     "取样瓶清洗后残留清洗剂，更换一次性取样瓶"),
    ("potency_assay", ">=32 U", (28.0, 31.0), True, "released", (33.0, 38.0),
     "效价偏低", "复检合格，怀疑为标准品配制问题",
     "标准品稀释误差，重新配制标准品后复检通过"),
    ("potency_assay", ">=32 U", (29.0, 31.5), True, "released", (34.0, 39.0),
     "效价临界偏低", "复检合格，怀疑为检测系统漂移",
     "ELISA试剂盒批次差异，更换试剂盒后复检通过"),
    ("purity_test", ">=95%", (93.0, 94.8), True, "released", (95.5, 97.0),
     "纯度临界偏低", "复检合格，怀疑为色谱柱老化",
     "HPLC色谱柱柱效下降，更换色谱柱后复检通过"),
    ("potency_assay", ">=6.5 log10 TCID50/mL", (6.0, 6.4), True, "released", (6.8, 7.2),
     "病毒滴度偏低", "复检合格，怀疑为细胞传代次数过多",
     "检测用细胞传代次数偏高，更换低传代细胞后复检通过"),
    ("residual_moisture", "<=3.0%", (3.1, 3.5), True, "released", (2.0, 2.8),
     "残余水分临界超标", "复检合格，怀疑为取样时机不当",
     "冻干后未充分平衡即取样，延长平衡时间后复检通过"),
    ("cfu_count", ">=1E8 CFU/mL", (7.0E7, 9.5E7), True, "released", (1.2E8, 1.5E8),
     "菌落计数偏低", "复检合格，怀疑为稀释误差",
     "系列稀释操作误差，改用自动稀释仪后复检通过"),
    ("adjuvant_content", "0.8-1.2 mg/mL", (0.65, 0.78), True, "released", (0.9, 1.1),
     "佐剂含量偏低", "复检合格，怀疑为取样不均匀",
     "乳化液取样前未充分混匀，加强混匀后复检通过"),
    ("potency_assay", ">=450 ug/dose", (420.0, 445.0), True, "released", (460.0, 510.0),
     "蛋白含量偏低", "复检合格，怀疑为标准品问题",
     "BCA法标准蛋白浓度偏差，更换标准品后复检通过"),
    ("inactivation_test", "No viable virus", (None,), True, "released", (None,),
     "灭活验证可疑阳性", "延长培养时间确认为阴性，假阳性",
     "细胞培养体系污染导致假阳性，加强无菌操作后复检通过"),
    ("endotoxin_test", "<=5 EU/dose", (8.0, 15.0), False, "rejected", None,
     "内毒素严重超标", "确认为生产OOS，纯化系统故障",
     "超滤膜破损导致内毒素泄漏，更换超滤膜后复检"),
    ("potency_assay", ">=32 U", (15.0, 22.0), False, "rejected", None,
     "效价严重偏低", "确认为生产OOS，病毒失活",
     "收获后存放时间过长导致病毒降解，缩短存放时间"),
    ("appearance_test", "澄清透明", (None,), False, "rejected", None,
     "外观明显异常，可见异物", "确认为生产OOS，过滤失效",
     "除菌过滤器完整性失败，更换过滤器并增加完整性检测"),
]


def gen_oos_records(conn):
    cur = conn.cursor()
    rows = []
    counter = 0
    used_batches = set()

    for scenario in OOS_SCENARIOS:
        test_type, spec, result_range, is_lab, disposition, retest_range, p1, p2, rc = scenario
        counter += 1
        year = 2024 if counter <= 20 else 2025
        num = counter if year == 2024 else counter - 20
        oid = oos_id(year, num)

        pt = random.choice(PRODUCTS)
        batch_num = random.randint(1, PRODUCT_BATCHES[pt])
        b = bid(pt, year, batch_num)
        while b in used_batches:
            batch_num = random.randint(1, PRODUCT_BATCHES[pt])
            b = bid(pt, year, batch_num)
        used_batches.add(b)

        if result_range[0] is not None:
            rv = round(random.uniform(*result_range), 4)
        else:
            rv = None

        if is_lab and retest_range and retest_range[0] is not None:
            rt = round(random.uniform(*retest_range), 4)
        elif is_lab:
            rt = None
        else:
            rt = None

        inv_date = rdate(date(year, 1, 1), date(year, 11, 30))
        conc_date = inv_date + timedelta(days=random.randint(7, 45)) if disposition != "rejected" else None

        dev_id_val = None
        if not is_lab and random.random() < 0.6:
            dev_year = year
            dev_pt = pt
            dev_num = random.randint(1, 5)
            dev_id_val = f"DEV-{dev_pt}-{dev_year}-{dev_num:04d}"

        rows.append((oid, b, pt, test_type, spec, rv, "laboratory" if is_lab else "production",
                      p1, p2, rc,
                      "closed", rt, disposition, dev_id_val, inv_date, conc_date))

    for row in rows:
        try:
            cur.execute("SAVEPOINT oos_row")
            cur.execute(f"""INSERT INTO {QUAL}.oos_records VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", row)
            cur.execute("RELEASE SAVEPOINT oos_row")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT oos_row")
            print(f"  [WARN] OOS: {e} | {row[0]}")

    conn.commit()
    print(f"  oos_records: {len(rows)} rows")


# ═══════════════════════════════════════════════════════════
# OOT Records (~20 rows)
# ═══════════════════════════════════════════════════════════

OOT_SCENARIOS = [
    # (param, method, rule, expected, actual_range, severity, assessment, is_false_alarm)
    ("potency_trend", "Western_Electric", "Rule 1: 1点>3sigma", "38-44 U", (28.0, 32.0), "action",
     "效价持续下降趋势，建议检查毒种传代次数", False),
    ("potency_trend", "Western_Electric", "Rule 2: 连续9点同一侧", "38-44 U", (35.0, 38.0), "alert",
     "效价连续偏低，需调查工艺稳定性", False),
    ("endotoxin_trend", "Nelson", "Rule 5: 2/3点>2sigma", "<=5 EU/dose", (4.2, 4.9), "alert",
     "内毒素水平接近上限，需关注纯化柱清洗", False),
    ("endotoxin_trend", "Nelson", "Rule 1: 1点>3sigma", "<=5 EU/dose", (5.5, 7.0), "action",
     "内毒素超标趋势，建议立即调查", False),
    ("ph_trend", "Western_Electric", "Rule 3: 5/6点同侧递增", "6.8-7.4", (7.1, 7.3), "alert",
     "pH缓慢上升，需关注缓冲液配制", False),
    ("ph_trend", "regression", "线性回归斜率>0", "6.8-7.4", (7.2, 7.5), "alert",
     "pH呈上升趋势，建议检查缓冲液稳定性", True),
    ("moisture_trend", "Western_Electric", "Rule 4: 连续6点递增", "<=3.0%", (2.2, 2.8), "alert",
     "残余水分上升趋势，需检查冻干机密封性", False),
    ("moisture_trend", "Western_Electric", "Rule 2: 连续9点同一侧", "<=3.0%", (2.5, 2.9), "action",
     "残余水分持续偏高，建议调整冻干曲线", False),
    ("adjuvant_trend", "Nelson", "Rule 6: 连续4点>1sigma", "0.8-1.2 mg/mL", (1.15, 1.25), "alert",
     "佐剂含量接近上限，需检查乳化工艺", True),
    ("adjuvant_trend", "Western_Electric", "Rule 1: 1点>3sigma", "0.8-1.2 mg/mL", (0.5, 0.6), "action",
     "佐剂含量突然下降，建议立即调查", False),
    ("filling_volume_trend", "regression", "线性回归斜率<0", "1.95-2.05 mL", (1.92, 1.96), "alert",
     "灌装量呈下降趋势，需检查灌装泵", True),
    ("filling_volume_trend", "Western_Electric", "Rule 3: 5/6点同侧递减", "1.95-2.05 mL", (1.88, 1.93), "action",
     "灌装量持续偏低，建议校准灌装系统", False),
    ("cfu_trend", "Nelson", "Rule 2: 连续9点同一侧", ">=1E8 CFU/mL", (7.5E7, 9.0E7), "alert",
     "菌落计数持续偏低，需检查发酵条件", False),
    ("cfu_trend", "Western_Electric", "Rule 4: 连续6点递减", ">=1E8 CFU/mL", (6.0E7, 8.0E7), "action",
     "菌落计数下降趋势，建议检查溶氧和营养", False),
    ("purity_trend", "regression", "线性回归斜率<0", ">=95%", (93.0, 95.0), "alert",
     "纯度呈下降趋势，需关注层析工艺", True),
    ("purity_trend", "Western_Electric", "Rule 1: 1点>3sigma", ">=95%", (88.0, 92.0), "action",
     "纯度突然下降，建议立即调查", False),
    ("reconstitution_trend", "Nelson", "Rule 5: 2/3点>2sigma", "<=60 s", (55.0, 65.0), "alert",
     "复溶时间接近上限，需检查冻干工艺", True),
    ("reconstitution_trend", "Western_Electric", "Rule 2: 连续9点同一侧", "<=60 s", (52.0, 58.0), "action",
     "复溶时间持续偏长，建议调整冻干曲线", False),
    ("protein_content_trend", "Western_Electric", "Rule 3: 5/6点同侧递增", ">=450 ug/dose", (420.0, 445.0), "alert",
     "蛋白含量接近下限，需检查表达系统", False),
    ("protein_content_trend", "regression", "线性回归斜率>0.5", ">=450 ug/dose", (460.0, 480.0), "alert",
     "蛋白含量呈上升趋势，需关注诱导条件", True),
]


def gen_oot_records(conn):
    cur = conn.cursor()
    rows = []
    counter = 0

    for scenario in OOT_SCENARIOS:
        param, method, rule, expected, actual_range, severity, assessment, is_false = scenario
        counter += 1
        year = 2024 if counter <= 10 else 2025
        num = counter if year == 2024 else counter - 10
        oid = oot_id(year, num)

        pt = random.choice(PRODUCTS)
        batch_num = random.randint(1, PRODUCT_BATCHES[pt])
        b = bid(pt, year, batch_num)

        actual = round(random.uniform(*actual_range), 4)

        det_date = rdate(date(year, 1, 1), date(year, 10, 31))
        review_date = det_date + timedelta(days=random.randint(1, 14))

        final_assessment = assessment
        if is_false:
            final_assessment = assessment + "。后续评估确认为正常波动，无需采取行动"

        rows.append((oid, b, pt, param, method, rule, expected, actual,
                      severity, final_assessment, det_date, review_date))

    for row in rows:
        try:
            cur.execute("SAVEPOINT oot_row")
            cur.execute(f"""INSERT INTO {QUAL}.oot_records VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", row)
            cur.execute("RELEASE SAVEPOINT oot_row")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT oot_row")
            print(f"  [WARN] OOT: {e} | {row[0]}")

    conn.commit()
    print(f"  oot_records: {len(rows)} rows")


# ═══════════════════════════════════════════════════════════
# Change Control Records (~30 rows)
# ═══════════════════════════════════════════════════════════

CC_SCENARIOS = [
    # (type, desc, reason, risk, validation, status, is_overdue)
    ("equipment", "生物反应器#1升级为500L", "产能需求增长", "high", "IQ/OQ/PQ", "approved", False),
    ("equipment", "冻干机#2更换真空泵", "设备老化故障频发", "medium", "OQ", "closed", False),
    ("equipment", "灌装机#1增加在线称重系统", "提高灌装精度控制", "low", "PQ", "closed", True),
    ("equipment", "HVAC系统#1更换高效过滤器", "过滤器达到使用寿命", "medium", "PQ", "approved", False),
    ("equipment", "冷库#1增加温度冗余探头", "提高监控可靠性", "low", "OQ", "closed", False),
    ("process", "PEDV灭活工艺参数调整", "BEI浓度从0.05%调整为0.08%", "critical", "工艺验证", "approved", False),
    ("process", "PRRSV冻干曲线优化", "降低残余水分至2%以下", "high", "工艺验证", "approved", True),
    ("process", "APP层析流速调整", "提高纯化效率", "medium", "工艺验证", "closed", False),
    ("process", "HPS发酵培养基优化", "提高菌体密度", "medium", "工艺验证", "approved", False),
    ("process", "E.coli诱导温度调整", "从42°C降至37°C减少蛋白降解", "high", "工艺验证", "closed", False),
    ("material", "培养基供应商更换", "原供应商产能不足", "high", "物料验证", "approved", False),
    ("material", "佐剂供应商新增备选", "降低供应链风险", "medium", "物料验证", "closed", False),
    ("material", "缓冲液原料规格升级", "提高内毒素控制标准", "low", "物料验证", "approved", False),
    ("material", "冻干保护剂配方调整", "改善复溶性能", "medium", "物料验证", "rejected", False),
    ("material", "滤器供应商更换", "原供应商停产", "high", "物料验证", "approved", True),
    ("packaging", "西林瓶规格从2mL改为5mL", "适应新剂型需求", "medium", "包装验证", "closed", False),
    ("packaging", "标签印刷工艺升级", "提高耐水性和耐磨性", "low", "包装验证", "approved", False),
    ("packaging", "外包装纸箱材质更换", "降低运输破损率", "low", "包装验证", "closed", False),
    ("packaging", "说明书折叠方式调整", "提高装盒效率", "low", "包装验证", "rejected", False),
    ("equipment", "灭菌柜#1更换密封圈", "密封性下降", "medium", "OQ", "closed", False),
    ("equipment", "离心机#2增加自动平衡系统", "减少操作误差", "low", "PQ", "approved", False),
    ("process", "连苗乳化工艺参数调整", "提高乳化均匀性", "high", "工艺验证", "pending", False),
    ("process", "FLU裂解剂浓度调整", "降低残留毒性", "critical", "工艺验证", "pending", False),
    ("material", "血清原料供应商新增", "降低对单一供应商依赖", "high", "物料验证", "pending", False),
    ("material", "IPTG诱导剂更换批次", "原批次库存不足", "medium", "物料验证", "rejected", False),
    ("equipment", "纯化水系统更换RO膜", "产水质量下降", "medium", "OQ/PQ", "closed", True),
    ("equipment", "冻干机#1升级控制系统", "提高温度控制精度", "high", "IQ/OQ/PQ", "approved", False),
    ("process", "SS灭活工艺验证", "新工艺参数验证", "high", "工艺验证", "pending", False),
    ("packaging", "冷链运输包装升级", "延长保温时间至48小时", "medium", "包装验证", "closed", False),
    ("material", "佐剂乳化剂更换", "提高乳化稳定性", "high", "物料验证", "rejected", False),
]


def gen_change_control_records(conn):
    cur = conn.cursor()
    rows = []
    counter = 0
    eq_counter = 0

    for scenario in CC_SCENARIOS:
        ctype, desc, reason, risk, validation, status, is_overdue = scenario
        counter += 1
        year = 2024 if counter <= 15 else 2025
        num = counter if year == 2024 else counter - 15
        cid = cc_id(year, num)

        initiator = random.choice(ANALYSTS[:4])
        approver = random.choice(ANALYSTS[4:])

        related_eq = None
        if ctype == "equipment" and eq_counter < len(EQUIPMENT_IDS):
            related_eq = EQUIPMENT_IDS[eq_counter % len(EQUIPMENT_IDS)]
            eq_counter += 1

        related_capa = None
        if random.random() < 0.3:
            capa_year = year
            capa_num = random.randint(1, 10)
            related_capa = f"CAPA-{capa_year}-{capa_num:04d}"

        init_date = rdate(date(year, 1, 1), date(year, 6, 30))
        target_date = init_date + timedelta(days=random.randint(30, 120))

        actual_date = None
        if status in ("closed", "approved"):
            if is_overdue:
                actual_date = target_date + timedelta(days=random.randint(10, 45))
            else:
                actual_date = target_date - timedelta(days=random.randint(0, 15))

        rows.append((cid, ctype, desc, reason, risk,
                      f"影响评估: {desc}，风险等级{risk}", validation,
                      status, initiator, approver, related_eq, related_capa,
                      init_date, target_date, actual_date))

    for row in rows:
        try:
            cur.execute("SAVEPOINT cc_row")
            cur.execute(f"""INSERT INTO {QUAL}.change_control_records VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", row)
            cur.execute("RELEASE SAVEPOINT cc_row")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT cc_row")
            print(f"  [WARN] CC: {e} | {row[0]}")

    conn.commit()
    print(f"  change_control_records: {len(rows)} rows")
    return rows


# ═══════════════════════════════════════════════════════════
# Change Control Risk Assessment (~20 rows)
# ═══════════════════════════════════════════════════════════

def gen_risk_assessments(conn, cc_rows):
    cur = conn.cursor()
    rows = []
    counter = 0

    for cc in cc_rows[:20]:
        cid, ctype, desc, reason, risk = cc[0], cc[1], cc[2], cc[3], cc[4]
        counter += 1
        year = int(cid.split("-")[1])
        num = counter
        aid = ra_id(year, num)

        method = random.choice(["FMEA", "HACCP", "FTA"])

        if risk == "critical":
            sev = random.randint(8, 10)
            prob = random.randint(6, 9)
            det = random.randint(5, 8)
        elif risk == "high":
            sev = random.randint(6, 9)
            prob = random.randint(5, 8)
            det = random.randint(4, 7)
        elif risk == "medium":
            sev = random.randint(4, 7)
            prob = random.randint(3, 6)
            det = random.randint(3, 6)
        else:
            sev = random.randint(2, 5)
            prob = random.randint(2, 5)
            det = random.randint(2, 5)

        rpn = sev * prob * det

        if rpn > 100:
            cat = "high"
        elif rpn > 50:
            cat = "medium"
        else:
            cat = "low"

        mitigation = f"针对{desc}的风险缓解措施: "
        if sev >= 7:
            mitigation += "加强过程监控，增加关键参数检测频次"
        elif prob >= 6:
            mitigation += "优化工艺参数，降低故障发生概率"
        else:
            mitigation += "完善SOP，加强操作人员培训"

        residual_rpn = max(20, int(rpn * random.uniform(0.3, 0.6)))
        if random.random() < 0.2:
            residual_rpn = max(50, int(rpn * random.uniform(0.6, 0.8)))

        assess_date = rdate(date(year, 1, 1), date(year, 8, 31))

        rows.append((aid, cid, method, sev, prob, det, rpn, cat,
                      mitigation, residual_rpn, assess_date))

    for row in rows:
        try:
            cur.execute("SAVEPOINT ra_row")
            cur.execute(f"""INSERT INTO {QUAL}.change_control_risk_assessment VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", row)
            cur.execute("RELEASE SAVEPOINT ra_row")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT ra_row")
            print(f"  [WARN] RA: {e} | {row[0]}")

    conn.commit()
    print(f"  change_control_risk_assessment: {len(rows)} rows")


# ═══════════════════════════════════════════════════════════
# Cleaning Validation Results (~30 rows)
# ═══════════════════════════════════════════════════════════

CLEANING_SCENARIOS = [
    # (equipment, worst_case, residue, limit, actual_range, is_fail, is_borderline)
    ("EQ-001", "PEDV", "active", 0.0012, (0.0003, 0.0009), False, False),
    ("EQ-001", "HPS", "total_protein", 0.0025, (0.0008, 0.0018), False, False),
    ("EQ-002", "PRRSV", "active", 0.0010, (0.0004, 0.0008), False, False),
    ("EQ-002", "APP", "TOC", 0.0030, (0.0028, 0.0035), False, True),
    ("EQ-003", "HPSSS_COMBO", "active", 0.0015, (0.0005, 0.0012), False, False),
    ("EQ-003", "ECOLI", "total_protein", 0.0028, (0.0032, 0.0040), True, False),
    ("EQ-004", "HPS", "active", 0.0018, (0.0006, 0.0014), False, False),
    ("EQ-004", "SS", "TOC", 0.0035, (0.0010, 0.0025), False, False),
    ("EQ-005", "APP", "active", 0.0020, (0.0008, 0.0016), False, False),
    ("EQ-005", "HPS", "total_protein", 0.0030, (0.0035, 0.0045), True, False),
    ("EQ-006", "ECOLI", "active", 0.0022, (0.0009, 0.0018), False, False),
    ("EQ-006", "SS", "TOC", 0.0040, (0.0038, 0.0048), False, True),
    ("EQ-007", "PEDV", "active", 0.0014, (0.0005, 0.0011), False, False),
    ("EQ-007", "PRRSV", "total_protein", 0.0026, (0.0010, 0.0020), False, False),
    ("EQ-008", "HPS", "active", 0.0016, (0.0007, 0.0013), False, False),
    ("EQ-008", "HPSSS_COMBO", "TOC", 0.0032, (0.0034, 0.0042), True, False),
    ("EQ-011", "APP", "active", 0.0025, (0.0010, 0.0020), False, False),
    ("EQ-011", "ECOLI", "total_protein", 0.0035, (0.0033, 0.0040), False, True),
    ("EQ-012", "HPS", "active", 0.0020, (0.0008, 0.0016), False, False),
    ("EQ-012", "SS", "TOC", 0.0038, (0.0012, 0.0028), False, False),
    ("EQ-013", "PEDV", "active", 0.0018, (0.0007, 0.0014), False, False),
    ("EQ-013", "PRRSV", "total_protein", 0.0028, (0.0030, 0.0038), True, False),
    ("EQ-014", "APP", "active", 0.0022, (0.0009, 0.0018), False, False),
    ("EQ-014", "HPS", "total_protein", 0.0032, (0.0012, 0.0024), False, False),
    ("EQ-015", "ECOLI", "active", 0.0024, (0.0010, 0.0020), False, False),
    ("EQ-015", "SS", "TOC", 0.0036, (0.0014, 0.0028), False, False),
    ("EQ-016", "PEDV", "active", 0.0015, (0.0006, 0.0012), False, False),
    ("EQ-016", "HPSSS_COMBO", "total_protein", 0.0030, (0.0011, 0.0022), False, False),
    ("EQ-017", "PRRSV", "active", 0.0012, (0.0005, 0.0010), False, False),
    ("EQ-017", "APP", "TOC", 0.0028, (0.0010, 0.0022), False, False),
]


def gen_cleaning_validation_results(conn):
    cur = conn.cursor()
    rows = []
    counter = 0
    locations = ["最难清洁部位-顶部", "最难清洁部位-死角", "最难清洁部位-阀门", "常规部位-罐壁", "常规部位-搅拌桨"]

    for scenario in CLEANING_SCENARIOS:
        eq, worst, residue, limit, actual_range, is_fail, is_border = scenario
        counter += 1
        year = 2024 if counter <= 15 else 2025
        num = counter if year == 2024 else counter - 15
        vid = cv_id(year, num)

        actual = round(random.uniform(*actual_range), 6)
        result = "fail" if is_fail else "pass"
        if is_border and not is_fail:
            actual = round(limit * random.uniform(0.92, 0.98), 6)

        loc = random.choice(locations)
        method = random.choice(["swab", "rinse"])
        criteria = f"<= {limit} mg/cm2"
        analyst = random.choice(ANALYSTS)
        val_date = rdate(date(year, 1, 1), date(year, 10, 31))

        rows.append((vid, worst, eq, worst, residue, limit, actual,
                      loc, method, criteria, result, val_date, analyst))

    for row in rows:
        try:
            cur.execute("SAVEPOINT cv_row")
            cur.execute(f"""INSERT INTO {QUAL}.cleaning_validation_results VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", row)
            cur.execute("RELEASE SAVEPOINT cv_row")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT cv_row")
            print(f"  [WARN] CV: {e} | {row[0]}")

    conn.commit()
    print(f"  cleaning_validation_results: {len(rows)} rows")


# ═══════════════════════════════════════════════════════════
# Cleaning Validation Limits (~12 rows)
# ═══════════════════════════════════════════════════════════

LIMIT_SCENARIOS = [
    # (product, residue, method, pde, sf, limit_cm2, limit_swab, area, batch_doses, maco)
    ("PEDV", "active", "PDE", 2.5, 1000, 0.0012, 0.0240, 20000.0, 10000, 25000.0),
    ("PEDV", "total_protein", "0.001_dose", None, 1000, 0.0025, 0.0500, 20000.0, 10000, 50000.0),
    ("PRRSV", "active", "PDE", 1.8, 1000, 0.0010, 0.0200, 20000.0, 10000, 18000.0),
    ("PRRSV", "total_protein", "ADE", 3.2, 500, 0.0020, 0.0400, 20000.0, 10000, 32000.0),
    ("APP", "active", "PDE", 3.5, 1000, 0.0020, 0.0400, 20000.0, 10000, 35000.0),
    ("APP", "TOC", "0.001_dose", None, 100, 0.0030, 0.0600, 20000.0, 10000, 60000.0),
    ("HPS", "active", "PDE", 4.2, 1000, 0.0018, 0.0360, 20000.0, 10000, 42000.0),
    ("HPS", "total_protein", "ADE", 5.0, 500, 0.0028, 0.0560, 20000.0, 10000, 50000.0),
    ("ECOLI", "active", "PDE", 2.8, 1000, 0.0022, 0.0440, 20000.0, 10000, 28000.0),
    ("ECOLI", "TOC", "0.001_dose", None, 100, 0.0035, 0.0700, 20000.0, 10000, 70000.0),
    ("HPSSS_COMBO", "active", "PDE", 5.5, 1000, 0.0015, 0.0300, 20000.0, 10000, 55000.0),
    ("HPSSS_COMBO", "total_protein", "ADE", 6.0, 100, 0.0030, 0.0600, 20000.0, 10000, 60000.0),
]


def gen_cleaning_validation_limits(conn):
    cur = conn.cursor()
    rows = []
    counter = 0

    for scenario in LIMIT_SCENARIOS:
        pt, residue, method, pde, sf, limit_cm2, limit_swab, area, batch_doses, maco = scenario
        counter += 1
        year = 2024
        num = counter
        lid = cl_id(year, num)

        rows.append((lid, pt, residue, method, pde, sf, limit_cm2, limit_swab,
                      area, batch_doses, maco))

    for row in rows:
        try:
            cur.execute("SAVEPOINT cl_row")
            cur.execute(f"""INSERT INTO {QUAL}.cleaning_validation_limits VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", row)
            cur.execute("RELEASE SAVEPOINT cl_row")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT cl_row")
            print(f"  [WARN] CL: {e} | {row[0]}")

    conn.commit()
    print(f"  cleaning_validation_limits: {len(rows)} rows")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(description="质量管理补充数据生成器")
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
    conn.autocommit = False
    return conn


def main():
    args = parse_args()
    print("=" * 60)
    print("  质量管理补充数据生成器")
    print(f"  6 张新表: OOS/OOT/变更控制/清洁验证")
    print(f"  连接: {args.host}:{args.port}/{args.db}")
    print("=" * 60)

    conn = connect_db(args)
    cur = conn.cursor()

    print("\n[Phase 1] 创建 DDL...")
    for stmt in DDL_STATEMENTS:
        try:
            cur.execute(stmt)
        except Exception as e:
            print(f"  [WARN] DDL: {e}")
    conn.commit()
    print("  DDL 创建完成")

    print("\n[Phase 2] 生成 OOS 记录...")
    gen_oos_records(conn)

    print("\n[Phase 3] 生成 OOT 记录...")
    gen_oot_records(conn)

    print("\n[Phase 4] 生成变更控制记录...")
    cc_rows = gen_change_control_records(conn)

    print("\n[Phase 5] 生成变更风险评估...")
    gen_risk_assessments(conn, cc_rows)

    print("\n[Phase 6] 生成清洁验证结果...")
    gen_cleaning_validation_results(conn)

    print("\n[Phase 7] 生成清洁验证限度...")
    gen_cleaning_validation_limits(conn)

    print("\n[Phase 8] 授权 vlm_reader...")
    try:
        cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {QUAL} TO vlm_reader")
        cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {QUAL} GRANT SELECT ON TABLES TO vlm_reader")
        conn.commit()
        print("  vlm_reader 权限授予完成")
    except Exception as e:
        print(f"  [WARN] 权限授予: {e}")
        conn.rollback()

    print("\n[Phase 9] 验证数据...")
    tables = [
        "oos_records", "oot_records", "change_control_records",
        "change_control_risk_assessment", "cleaning_validation_results",
        "cleaning_validation_limits"
    ]
    for tbl in tables:
        cur.execute(f"SELECT count(*) FROM {QUAL}.{tbl}")
        count = cur.fetchone()[0]
        print(f"  {tbl}: {count} rows")

    conn.close()
    print("\n数据补充完成!")


if __name__ == "__main__":
    main()
