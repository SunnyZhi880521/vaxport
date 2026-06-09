"""SKILL 输出验证 — Layer 3 防御：最终输出的 SKILL 合规性验证"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ValidationIssue:
    """单个验证问题"""
    level: str  # "error" | "warning"
    category: str  # "missing_section" | "forbidden_pattern" | "missing_chart"
    message: str
    detail: str = ""


@dataclass
class ValidationResult:
    """验证结果"""
    passed: bool
    score: float  # 0.0 - 1.0
    issues: list[ValidationIssue] = field(default_factory=list)

    def summary(self) -> str:
        if self.passed:
            return f"✅ SKILL 合规 ({self.score:.0%})"
        errors = [i for i in self.issues if i.level == "error"]
        warnings = [i for i in self.issues if i.level == "warning"]
        return f"⚠️ SKILL 不合规 ({self.score:.0%}): {len(errors)} 错误, {len(warnings)} 警告"


class SkillValidator:
    """最终输出的 SKILL 合规性验证器

    对照 checklist.yaml 验证 Agent 输出是否满足：
    - 必需章节是否存在
    - 禁止模式是否出现
    - 必需图表是否生成
    """

    def __init__(self, checklist: dict):
        self.checklist = checklist

    def validate(self, output: str, tool_calls: list[dict] | None = None) -> ValidationResult:
        """验证输出是否满足 checklist"""
        issues: list[ValidationIssue] = []
        checks_total = 0
        checks_passed = 0

        output_lower = output.lower()

        required_sections = self.checklist.get("required_sections", [])
        for section in required_sections:
            checks_total += 1
            if isinstance(section, dict):
                name = section.get("name", "")
                aliases = section.get("aliases", [])
                candidates = [name] + aliases
            else:
                name = section
                candidates = [section]

            matched = any(c.lower() in output_lower for c in candidates)
            if matched:
                checks_passed += 1
            else:
                issues.append(ValidationIssue(
                    level="warning",
                    category="missing_section",
                    message=f"缺少必需章节: {name}",
                ))

        forbidden_patterns = self.checklist.get("forbidden_patterns", [])
        for fp in forbidden_patterns:
            checks_total += 1
            pattern = fp.get("pattern", "")
            try:
                if re.search(pattern, output):
                    issues.append(ValidationIssue(
                        level="error",
                        category="forbidden_pattern",
                        message=fp.get("message", f"触发禁止模式: {pattern}"),
                        detail=f"匹配到: {pattern}",
                    ))
                else:
                    checks_passed += 1
            except re.error:
                checks_passed += 1

        required_charts = self.checklist.get("required_charts", [])
        if tool_calls and required_charts:
            chart_calls = [tc for tc in tool_calls if tc.get("name") == "generate_chart"]
            for chart_name in required_charts:
                checks_total += 1
                found = any(
                    chart_name.lower() in str(tc.get("args", {})).lower()
                    for tc in chart_calls
                )
                if found:
                    checks_passed += 1
                else:
                    issues.append(ValidationIssue(
                        level="warning",
                        category="missing_chart",
                        message=f"缺少必需图表: {chart_name}",
                    ))

        score = checks_passed / max(checks_total, 1)
        has_errors = any(i.level == "error" for i in issues)

        return ValidationResult(
            passed=not has_errors and score >= 0.6,
            score=score,
            issues=issues,
        )

    def generate_fix_prompt(self, issues: list[ValidationIssue]) -> str:
        """根据验证问题生成修复提示"""
        if not issues:
            return ""

        lines = ["[SKILL 输出验证] 以下问题需要修正："]
        for issue in issues:
            prefix = "❌" if issue.level == "error" else "⚠️"
            lines.append(f"  {prefix} {issue.message}")

        lines.append("\n请在最终回答中补充缺失的内容。")
        return "\n".join(lines)
