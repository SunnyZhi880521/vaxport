#!/usr/bin/env python
"""SKILL 对比报告生成器 — baseline vs with_skill 结果对比"""

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from test_framework import TestResult, load_test_result
from auto_scorer import AutoScorer, QUESTION_CHECKLISTS


def load_all_results(output_dir: Path, qids: list[str]) -> dict[str, list[TestResult]]:
    """加载指定目录下所有题目的结果"""
    results = {}
    for qid in qids:
        runs = []
        for run in range(1, 4):
            r = load_test_result(output_dir, qid, run)
            if r:
                runs.append(r)
        if runs:
            results[qid] = runs
    return results


def compare_results(baseline: dict, skill: dict, scorer: AutoScorer) -> dict:
    """对比两组结果"""
    comparison = {}

    all_qids = sorted(set(list(baseline.keys()) + list(skill.keys())))

    for qid in all_qids:
        b_runs = baseline.get(qid, [])
        s_runs = skill.get(qid, [])

        if not b_runs and not s_runs:
            continue

        checklist = QUESTION_CHECKLISTS.get(qid, {})
        required_sections = checklist.get("required_sections", [])
        forbidden_patterns = checklist.get("forbidden_patterns", [])

        entry = {
            "qid": qid,
            "category": checklist.get("category", "unknown"),
            "baseline": {},
            "skill": {},
            "delta": {},
        }

        for label, runs in [("baseline", b_runs), ("skill", s_runs)]:
            if not runs:
                continue

            answers = [r.answer for r in runs if r.answer]
            tool_calls = []
            for r in runs:
                tool_calls.extend([tc.get("name", "") for tc in r.tool_calls])

            struct = scorer.score_structure(answers[0] if answers else "", required_sections)
            hall = scorer.score_hallucination(answers[0] if answers else "")
            const = scorer.score_constraint(answers[0] if answers else "", forbidden_patterns)
            consist = scorer.score_consistency(answers) if len(answers) >= 2 else {"score": 3, "detail": "不足2次运行"}

            avg_tokens = sum(r.tokens_used for r in runs) / len(runs) if runs else 0
            avg_turns = sum(r.turns for r in runs) / len(runs) if runs else 0
            avg_elapsed = sum(r.elapsed_seconds for r in runs) / len(runs) if runs else 0

            skill_val = None
            if runs and runs[0].skill_validation:
                skill_val = runs[0].skill_validation

            entry[label] = {
                "runs": len(runs),
                "structure": struct["score"],
                "hallucination": hall["score"],
                "constraint": const["score"],
                "consistency": consist["score"],
                "avg_tokens": round(avg_tokens),
                "avg_turns": round(avg_turns, 1),
                "avg_elapsed": round(avg_elapsed),
                "skill_validation": skill_val,
                "missing_sections": struct.get("missing", []),
            }

        if entry["baseline"] and entry["skill"]:
            for dim in ["structure", "hallucination", "constraint", "consistency"]:
                b = entry["baseline"].get(dim, 0)
                s = entry["skill"].get(dim, 0)
                entry["delta"][dim] = s - b

        comparison[qid] = entry

    return comparison


def generate_report(comparison: dict, output_path: Path):
    """生成 Markdown 对比报告"""
    lines = [
        "# SKILL v1.4.0 对比报告",
        "",
        "> Baseline (无 SKILL) vs With SKILL 对比",
        "",
    ]

    has_both = [qid for qid, e in comparison.items() if e.get("baseline") and e.get("skill")]
    has_baseline_only = [qid for qid, e in comparison.items() if e.get("baseline") and not e.get("skill")]

    if has_both:
        lines.append("## 对比结果")
        lines.append("")
        lines.append("| 题目 | 类别 | 维度 | Baseline | With SKILL | Δ |")
        lines.append("|------|------|------|----------|------------|---|")

        for qid in has_both:
            e = comparison[qid]
            b = e["baseline"]
            s = e["skill"]
            d = e["delta"]
            lines.append(
                f"| {qid} | {e['category']} | 结构 | {b['structure']}/5 | {s['structure']}/5 | "
                f"{d.get('structure', 0):+.0f} |"
            )
            lines.append(
                f"| | | 一致性 | {b['consistency']}/5 | {s['consistency']}/5 | "
                f"{d.get('consistency', 0):+.0f} |"
            )

        lines.append("")

        avg_b_struct = sum(comparison[qid]["baseline"]["structure"] for qid in has_both) / len(has_both)
        avg_s_struct = sum(comparison[qid]["skill"]["structure"] for qid in has_both) / len(has_both)
        lines.append(f"### 结构平均分: Baseline {avg_b_struct:.1f} → With SKILL {avg_s_struct:.1f} (Δ {avg_s_struct - avg_b_struct:+.1f})")
        lines.append("")

        skill_scores = []
        for qid in has_both:
            sv = comparison[qid]["skill"].get("skill_validation")
            if sv:
                skill_scores.append(sv.get("score", 0))
        if skill_scores:
            avg_compliance = sum(skill_scores) / len(skill_scores)
            lines.append(f"### SKILL 合规率: {avg_compliance:.0%}")
            lines.append("")

    if has_baseline_only:
        lines.append("## 仅 Baseline 结果（With SKILL 待运行）")
        lines.append("")
        for qid in has_baseline_only:
            e = comparison[qid]
            b = e["baseline"]
            lines.append(f"- {qid} ({e['category']}): 结构 {b['structure']}/5, 幻觉 {b['hallucination']}/5")
        lines.append("")

    lines.append("## 资源消耗对比")
    lines.append("")
    lines.append("| 题目 | Baseline tokens | SKILL tokens | Baseline 时长 | SKILL 时长 |")
    lines.append("|------|----------------|-------------|--------------|-----------|")
    for qid in has_both:
        e = comparison[qid]
        b = e["baseline"]
        s = e["skill"]
        lines.append(
            f"| {qid} | {b['avg_tokens']} | {s['avg_tokens']} | "
            f"{b['avg_elapsed']}s | {s['avg_elapsed']}s |"
        )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成: {output_path}")


def main():
    baseline_dir = Path(__file__).parent.parent / "tests" / "baseline"
    skill_dir = Path(__file__).parent.parent / "tests" / "with_skill"
    output_path = Path(__file__).parent.parent / "tests" / "comparison_report.md"

    all_qids = list(QUESTION_CHECKLISTS.keys())

    baseline = load_all_results(baseline_dir, all_qids) if baseline_dir.exists() else {}
    skill = load_all_results(skill_dir, all_qids) if skill_dir.exists() else {}

    print(f"Baseline: {len(baseline)} 题")
    print(f"With SKILL: {len(skill)} 题")

    scorer = AutoScorer()
    comparison = compare_results(baseline, skill, scorer)
    generate_report(comparison, output_path)


if __name__ == "__main__":
    main()
