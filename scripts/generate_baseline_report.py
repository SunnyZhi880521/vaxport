#!/usr/bin/env python
"""SKILL 基线评估报告生成器 — 读取测试结果，自动评分，生成 Markdown 报告

使用方式：
  python scripts/generate_baseline_report.py
  python scripts/generate_baseline_report.py --output tests/baseline/evaluation_report.md
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_framework import (
    load_phase0_questions, load_all_results, ALL_PHASE0_IDS, PHASE0_QUESTIONS,
)
from auto_scorer import AutoScorer, score_question, QUESTION_CHECKLISTS


def generate_report(output_dir: Path, report_path: Path, runs: int = 3):
    """生成基线评估报告"""
    questions = load_phase0_questions()
    results = load_all_results(output_dir, ALL_PHASE0_IDS, runs)
    scorer = AutoScorer()

    # 按题目评分
    question_scores = {}
    for qid in ALL_PHASE0_IDS:
        q_results = results.get(qid, [])
        if q_results:
            question_scores[qid] = score_question(scorer, q_results)

    # 按类别分组
    category_scores = defaultdict(list)
    for qid, scores in question_scores.items():
        cat = scores["meta"]["category"]
        category_scores[cat].append(scores)

    # 计算类别平均分
    category_averages = {}
    for cat, scores_list in category_scores.items():
        dims = ["structure", "consistency", "hallucination", "constraint"]
        avg = {}
        for dim in dims:
            vals = []
            for s in scores_list:
                if dim == "consistency":
                    vals.append(s[dim]["score"])
                else:
                    vals.append(s[dim]["mean"])
            avg[dim] = round(sum(vals) / len(vals), 1) if vals else 0
        avg["overall"] = round(sum(avg.values()) / len(avg), 1)
        avg["count"] = len(scores_list)
        category_averages[cat] = avg

    # 生成报告
    lines = []
    lines.append("# SKILL 基线评估报告 (Phase 0)")
    lines.append("")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> 测试集: {len(questions)} 题 × {runs} 次 = {len(questions) * runs} 次")
    lines.append(f"> 已完成: {sum(len(v) for v in results.values())} 次")
    lines.append("")

    # 总览
    lines.append("## 一、总体评分")
    lines.append("")
    lines.append("| 维度 | 平均分 | 说明 |")
    lines.append("|------|--------|------|")

    all_overall = [s["overall"] for s in question_scores.values()]
    dim_avgs = {}
    for dim in ["structure", "consistency", "hallucination", "constraint"]:
        vals = []
        for s in question_scores.values():
            if dim == "consistency":
                vals.append(s[dim]["score"])
            else:
                vals.append(s[dim]["mean"])
        dim_avgs[dim] = round(sum(vals) / len(vals), 1) if vals else 0

    dim_desc = {
        "structure": "章节结构完整性",
        "consistency": "3 次运行一致性",
        "hallucination": "幻觉控制（无虚假表名/schema）",
        "constraint": "约束遵守（无违规假设）",
    }
    for dim in ["structure", "consistency", "hallucination", "constraint"]:
        lines.append(f"| {dim_desc[dim]} | {dim_avgs[dim]}/5 | |")

    overall_avg = round(sum(all_overall) / len(all_overall), 1) if all_overall else 0
    lines.append(f"| **综合** | **{overall_avg}/5** | |")
    lines.append("")

    # 按类别分析
    lines.append("## 二、按分析模式评分")
    lines.append("")
    lines.append("| 分析模式 | 题目数 | 结构 | 一致性 | 幻觉 | 约束 | 综合 |")
    lines.append("|---------|--------|------|--------|------|------|------|")

    cat_names = {
        "process_capability": "过程能力评估",
        "trend_spc": "趋势/SPC 检测",
        "deviation_capa": "偏差/CAPA 分析",
        "stability": "稳定性研究",
        "traceability": "全链条追溯",
        "comprehensive_report": "综合报告",
        "cross_domain": "跨域综合分析",
    }

    sorted_cats = sorted(category_averages.items(), key=lambda x: x[1]["overall"])
    for cat, avg in sorted_cats:
        name = cat_names.get(cat, cat)
        lines.append(
            f"| {name} | {avg['count']} | "
            f"{avg['structure']} | {avg['consistency']} | "
            f"{avg['hallucination']} | {avg['constraint']} | "
            f"**{avg['overall']}** |"
        )
    lines.append("")

    # 逐题明细
    lines.append("## 三、逐题评分明细")
    lines.append("")

    for qid in ALL_PHASE0_IDS:
        if qid not in question_scores:
            q = questions.get(qid, {})
            lines.append(f"### {qid}. {q.get('title', '未运行')}")
            lines.append("")
            lines.append("*未运行或无结果*")
            lines.append("")
            continue

        q = questions.get(qid, {})
        scores = question_scores[qid]
        lines.append(f"### {qid}. {q.get('title', '')}")
        lines.append("")
        lines.append(f"- 类别: {cat_names.get(scores['meta']['category'], '未知')}")
        lines.append(f"- 平均耗时: {scores['meta']['avg_elapsed']}s")
        lines.append(f"- 平均轮次: {scores['meta']['avg_turns']}")
        lines.append(f"- 结构: {scores['structure']['mean']}/5")
        lines.append(f"- 一致性: {scores['consistency']['score']}/5 ({scores['consistency']['detail']})")
        lines.append(f"- 幻觉: {scores['hallucination']['mean']}/5")
        if scores["hallucination"].get("details"):
            lines.append(f"  - 检测到的虚假引用: {scores['hallucination']['details']}")
        lines.append(f"- 约束: {scores['constraint']['mean']}/5")
        if scores["constraint"].get("violations"):
            for v in scores["constraint"]["violations"]:
                lines.append(f"  - ⚠ 违规: {v}")
        lines.append(f"- **综合: {scores['overall']}/5**")
        lines.append("")

    # 排名：最需要 SKILL 的场景
    lines.append("## 四、SKILL 需求优先级")
    lines.append("")
    lines.append("按综合得分从低到高排列，得分越低越需要 SKILL 辅助：")
    lines.append("")
    lines.append("| 排名 | 分析模式 | 综合得分 | 主要问题 |")
    lines.append("|------|---------|---------|---------|")

    for i, (cat, avg) in enumerate(sorted_cats, 1):
        name = cat_names.get(cat, cat)
        # 识别主要问题
        issues = []
        if avg["structure"] <= 3:
            issues.append("结构不完整")
        if avg["consistency"] <= 3:
            issues.append("一致性差")
        if avg["hallucination"] <= 3:
            issues.append("有幻觉")
        if avg["constraint"] <= 3:
            issues.append("有违规")
        issue_str = "、".join(issues) if issues else "表现良好"
        lines.append(f"| {i} | {name} | {avg['overall']} | {issue_str} |")
    lines.append("")

    # 建议
    lines.append("## 五、SKILL 设计建议")
    lines.append("")
    lines.append("基于基线测试结果，推荐优先为以下分析模式设计 SKILL：")
    lines.append("")

    top3 = sorted_cats[:3]
    for i, (cat, avg) in enumerate(top3, 1):
        name = cat_names.get(cat, cat)
        qids = PHASE0_QUESTIONS.get(cat, [])
        lines.append(f"### 优先级 {i}: {name}")
        lines.append("")
        lines.append(f"- 代表题目: {', '.join(qids)}")
        lines.append(f"- 当前得分: {avg['overall']}/5")

        # 针对性建议
        if avg["structure"] <= 3:
            lines.append(f"- 需要 SKILL 提供: 固定的输出章节结构")
        if avg["consistency"] <= 3:
            lines.append(f"- 需要 SKILL 提供: 统一的判定标准和计算方法")
        if avg["hallucination"] <= 3:
            lines.append(f"- 需要 SKILL 提供: 明确的表名/列名引用规范")
        if avg["constraint"] <= 3:
            lines.append(f"- 需要 SKILL 提供: 行为约束清单（禁止做的操作）")
        lines.append("")

    # 附录
    lines.append("## 附录")
    lines.append("")
    lines.append("### 评分标准")
    lines.append("")
    lines.append("| 维度 | 5分 | 3分 | 1分 |")
    lines.append("|------|-----|-----|-----|")
    lines.append("| 结构 | 90%+ 章节覆盖 | 50-70% 覆盖 | <30% 覆盖 |")
    lines.append("| 一致性 | 80%+ 数值一致 | 40-60% 一致 | <20% 一致 |")
    lines.append("| 幻觉 | 无虚假引用 | 1-3 个 | >5 个 |")
    lines.append("| 约束 | 无违规 | 1 处 | 多处 |")
    lines.append("")

    # 写入文件
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成: {report_path}")

    return str(report_path)


def main():
    parser = argparse.ArgumentParser(description="基线评估报告生成器")
    parser.add_argument("--input", type=str, default="tests/baseline",
                        help="测试结果目录")
    parser.add_argument("--output", type=str, default="tests/baseline/evaluation_report.md",
                        help="报告输出路径")
    parser.add_argument("--runs", type=int, default=3,
                        help="每题运行次数")
    args = parser.parse_args()

    output_dir = Path(args.input)
    report_path = Path(args.output)

    if not output_dir.exists():
        print(f"ERROR: 测试目录不存在: {output_dir}")
        sys.exit(1)

    generate_report(output_dir, report_path, args.runs)


if __name__ == "__main__":
    main()
