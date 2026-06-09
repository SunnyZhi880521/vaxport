#!/usr/bin/env python
"""自动评分器 — 结构/一致性/幻觉/约束/SKILL遵循率"""

import json
import re
from pathlib import Path
from typing import Optional


# ── Schema 参考数据（用于幻觉检测）──────────────────

VALID_SCHEMAS = [
    "analog_production", "analog_quality", "analog_coldchain",
    "analog_warehouse", "analog_equipment", "analog_hr", "analog_pv",
]


def load_schema_info() -> dict:
    """加载实际 schema 信息（从测试输出推断或硬编码）"""
    return {
        "schemas": VALID_SCHEMAS,
        "tables": {
            "analog_production": [
                "batch_records", "cell_culture_log", "virus_culture_log",
                "harvest_inactivation", "intermediate_qc", "batch_material_usage",
                "fermentation_log", "purification_log",
            ],
            "analog_quality": [
                "deviations", "capa", "in_process_qc", "finished_product_qc",
                "stability_study", "oos_results",
            ],
            "analog_coldchain": [
                "cold_storage", "transport_monitoring",
            ],
            "analog_warehouse": [
                "material_inventory", "warehouse_monitoring", "storage_excursions",
            ],
            "analog_equipment": [
                "equipment_calibration", "maintenance_log", "maintenance_schedule",
            ],
            "analog_hr": [
                "training_records",
            ],
            "analog_pv": [
                "aefi_reports",
            ],
        },
    }


# ── 评分器 ─────────────────────────────────────

class AutoScorer:
    """自动评分器"""

    def __init__(self, schema_info: Optional[dict] = None):
        self.schema_info = schema_info or load_schema_info()

    def score_structure(self, answer: str, required_sections: list[str]) -> dict:
        """结构评分（1-5）：检查必需章节是否存在"""
        if not answer or not required_sections:
            return {"score": 3, "found": [], "missing": required_sections, "detail": "无评分依据"}

        found = []
        missing = []
        for section in required_sections:
            # 模糊匹配：章节标题或关键内容
            if any(kw in answer for kw in [section, section.replace(" ", "")]):
                found.append(section)
            else:
                missing.append(section)

        coverage = len(found) / len(required_sections)
        if coverage >= 0.9:
            score = 5
        elif coverage >= 0.7:
            score = 4
        elif coverage >= 0.5:
            score = 3
        elif coverage >= 0.3:
            score = 2
        else:
            score = 1

        return {
            "score": score,
            "found": found,
            "missing": missing,
            "coverage": round(coverage, 2),
        }

    def score_consistency(self, answers: list[str]) -> dict:
        """一致性评分（1-5）：3 次输出的关键数值对比"""
        if len(answers) < 2:
            return {"score": 3, "detail": "不足 2 次运行"}

        # 提取所有数值
        all_numbers = []
        for ans in answers:
            nums = self._extract_numbers(ans)
            all_numbers.append(nums)

        # 比较数值集合的交集
        if not all(all_numbers):
            return {"score": 3, "detail": "部分运行未提取到数值"}

        # 计算数值一致性
        consistent_count = 0
        total_checks = 0
        for i, nums1 in enumerate(all_numbers):
            for j in range(i + 1, len(all_numbers)):
                nums2 = all_numbers[j]
                overlap = len(set(nums1) & set(nums2))
                total = max(len(set(nums1)), len(set(nums2)))
                if total > 0:
                    consistent_count += overlap
                    total_checks += total

        if total_checks > 0:
            ratio = consistent_count / total_checks
        else:
            ratio = 0.5

        if ratio >= 0.8:
            score = 5
        elif ratio >= 0.6:
            score = 4
        elif ratio >= 0.4:
            score = 3
        elif ratio >= 0.2:
            score = 2
        else:
            score = 1

        return {
            "score": score,
            "consistency_ratio": round(ratio, 2),
            "detail": f"数值一致性: {ratio:.0%}",
        }

    def score_hallucination(self, answer: str) -> dict:
        """幻觉评分（1-5）：检测不存在的表名/列名"""
        if not answer:
            return {"score": 3, "detail": "无输出"}

        hallucinated = []
        valid_tables = set()
        for tables in self.schema_info["tables"].values():
            valid_tables.update(tables)

        # 检测 SQL 中的表名引用
        sql_pattern = r'(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(\w+\.?\w+)'
        for m in re.finditer(sql_pattern, answer, re.IGNORECASE):
            table_ref = m.group(1)
            parts = table_ref.split(".")
            table_name = parts[-1]
            if table_name not in valid_tables and len(table_name) > 3:
                # 排除常见的非表名关键词
                skip = {"information_schema", "pg_catalog", "dual", "values"}
                if table_name.lower() not in skip:
                    hallucinated.append(table_ref)

        # 检测不存在的 schema
        schema_refs = re.findall(r'analog_\w+', answer)
        for ref in set(schema_refs):
            if ref not in VALID_SCHEMAS:
                hallucinated.append(ref)

        if not hallucinated:
            score = 5
        elif len(hallucinated) <= 1:
            score = 4
        elif len(hallucinated) <= 3:
            score = 3
        elif len(hallucinated) <= 5:
            score = 2
        else:
            score = 1

        return {
            "score": score,
            "hallucinated_refs": list(set(hallucinated)),
            "count": len(set(hallucinated)),
        }

    def score_constraint(self, answer: str,
                         forbidden_patterns: list[dict]) -> dict:
        """约束评分（1-5）：检测禁止模式"""
        if not answer or not forbidden_patterns:
            return {"score": 5, "violations": []}

        violations = []
        for rule in forbidden_patterns:
            pattern = rule["pattern"]
            if re.search(pattern, answer, re.IGNORECASE):
                violations.append(rule["message"])

        if not violations:
            score = 5
        elif len(violations) == 1:
            score = 3
        else:
            score = 1

        return {
            "score": score,
            "violations": violations,
        }

    def score_compliance(self, answer: str, tool_calls: list[str],
                         checklist: dict) -> dict:
        """SKILL 遵循率评分（0-100%）"""
        if not checklist:
            return {"score": 0, "compliance": 0, "detail": "无 checklist"}

        total = 0
        completed = 0

        # 检查 required_steps
        for step in checklist.get("required_steps", []):
            total += 1
            if step.get("required_tool"):
                tool = step["required_tool"]
                if any(tool in tc for tc in tool_calls):
                    completed += 1
            elif step.get("keywords"):
                if any(kw in answer for kw in step["keywords"]):
                    completed += 1

        # 检查 required_sections
        for section in checklist.get("required_sections", []):
            total += 1
            if section in answer:
                completed += 1

        # 检查 required_charts
        for chart in checklist.get("required_charts", []):
            total += 1
            if any(kw in answer for kw in [chart, "chart", "图"]):
                completed += 1

        # 检查 forbidden_patterns（反向：触发则扣分）
        for rule in checklist.get("forbidden_patterns", []):
            total += 1
            if not re.search(rule["pattern"], answer, re.IGNORECASE):
                completed += 1  # 未触发 = 通过

        compliance = (completed / total * 100) if total > 0 else 0
        return {
            "score": round(compliance, 1),
            "compliance": round(compliance, 1),
            "completed": completed,
            "total": total,
        }

    # ── 辅助方法 ──

    def _extract_numbers(self, text: str) -> list[str]:
        """提取文本中的关键数值（标准化后）"""
        numbers = []
        # 匹配小数和整数（排除年份等常见数字）
        for m in re.finditer(r'(?<!\d)(\d+\.\d+)(?!\d)', text):
            numbers.append(m.group(1))
        # 匹配 Cpk=1.33 类的关键数值
        for m in re.finditer(r'(?:Cpk|Cp|均值|mean|std|stddev)[^\d]*(\d+\.?\d*)', text, re.IGNORECASE):
            numbers.append(m.group(1))
        return numbers


# ── 各题目的评分配置 ──────────────────────────

QUESTION_CHECKLISTS = {
    "H2": {
        "category": "process_capability",
        "required_sections": ["过程能力", "Cpk", "控制图", "规格限", "结论"],
        "forbidden_patterns": [
            {"pattern": r"假设.*规格[限标准]", "message": "禁止假设规格限"},
        ],
        "required_tools": ["calc_cpk", "control_chart_rules"],
    },
    "H15": {
        "category": "process_capability",
        "required_sections": ["效价", "内毒素", "无菌", "联合", "通过率", "瓶颈"],
        "forbidden_patterns": [],
        "required_tools": ["calc_cpk"],
    },
    "H11": {
        "category": "trend_spc",
        "required_sections": ["趋势", "季节性", "月度", "夏季"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H29": {
        "category": "trend_spc",
        "required_sections": ["SPC", "控制图", "异常", "Western Electric"],
        "forbidden_patterns": [],
        "required_tools": ["control_chart_rules"],
    },
    "H5": {
        "category": "deviation_capa",
        "required_sections": ["CAPA", "有效性", "复发", "根因"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H21": {
        "category": "deviation_capa",
        "required_sections": ["CAPA", "完成率", "复发", "ICH Q10", "健康度"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H8": {
        "category": "stability",
        "required_sections": ["长期", "加速", "衰减", "效期"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H36": {
        "category": "stability",
        "required_sections": ["Arrhenius", "效期", "长期", "加速", "衰减"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H1": {
        "category": "traceability",
        "required_sections": ["仓库", "湿度", "物料", "批次", "追溯"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H27": {
        "category": "traceability",
        "required_sections": ["湿度", "物料", "变质", "批次", "CAPA", "证据"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H3": {
        "category": "comprehensive_report",
        "required_sections": ["效价", "偏差", "OOS", "放行率", "GMP", "改进建议"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H17": {
        "category": "comprehensive_report",
        "required_sections": ["放行", "偏差", "变更", "供应商", "AEFI", "培训", "质量目标"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H9": {
        "category": "cross_domain",
        "required_sections": ["质量", "安全", "生产", "评分", "排名"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H39": {
        "category": "cross_domain",
        "required_sections": ["Top", "根因", "时间线", "可预防", "改进"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
    "H40": {
        "category": "cross_domain",
        "required_sections": ["ICH Q10", "CAPA", "变更", "成熟度", "评分", "改进"],
        "forbidden_patterns": [],
        "required_tools": [],
    },
}


def score_question(scorer: AutoScorer, results: list) -> dict:
    """对单个题目的多次运行进行完整评分"""
    if not results:
        return {}

    qid = results[0].qid
    checklist = QUESTION_CHECKLISTS.get(qid, {})
    answers = [r.answer for r in results if r.answer]

    scores = {}

    # 1. 结构评分（每次运行单独评，取均值）
    structure_scores = []
    for ans in answers:
        s = scorer.score_structure(ans, checklist.get("required_sections", []))
        structure_scores.append(s["score"])
    scores["structure"] = {
        "scores": structure_scores,
        "mean": round(sum(structure_scores) / len(structure_scores), 1) if structure_scores else 0,
    }

    # 2. 一致性评分
    consistency = scorer.score_consistency(answers)
    scores["consistency"] = consistency

    # 3. 幻觉评分（每次单独评，取均值）
    hallucination_scores = []
    hallucination_details = []
    for ans in answers:
        s = scorer.score_hallucination(ans)
        hallucination_scores.append(s["score"])
        if s.get("hallucinated_refs"):
            hallucination_details.append(s["hallucinated_refs"])
    scores["hallucination"] = {
        "scores": hallucination_scores,
        "mean": round(sum(hallucination_scores) / len(hallucination_scores), 1) if hallucination_scores else 0,
        "details": hallucination_details,
    }

    # 4. 约束评分
    constraint_scores = []
    constraint_violations = []
    for ans in answers:
        s = scorer.score_constraint(ans, checklist.get("forbidden_patterns", []))
        constraint_scores.append(s["score"])
        if s.get("violations"):
            constraint_violations.append(s["violations"])
    scores["constraint"] = {
        "scores": constraint_scores,
        "mean": round(sum(constraint_scores) / len(constraint_scores), 1) if constraint_scores else 0,
        "violations": constraint_violations,
    }

    # 5. 综合得分
    all_means = [
        scores["structure"]["mean"],
        scores["consistency"]["score"],
        scores["hallucination"]["mean"],
        scores["constraint"]["mean"],
    ]
    scores["overall"] = round(sum(all_means) / len(all_means), 1)

    # 6. 元数据
    scores["meta"] = {
        "qid": qid,
        "category": checklist.get("category", "unknown"),
        "runs": len(results),
        "avg_elapsed": round(sum(r.elapsed_seconds for r in results) / len(results), 1),
        "avg_turns": round(sum(r.turns for r in results) / len(results), 1),
    }

    return scores
