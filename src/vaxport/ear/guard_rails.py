"""Guard Rails — tool call前置校验 + 执行监控"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """校验结果"""
    blocked: bool
    reason: str = ""
    suggestion: str = ""
    auto_fix: Optional[dict] = None  # 自动修复的参数


@dataclass
class RegulationAction:
    """轨迹调控动作"""
    action: str  # continue, break_loop, escalate, force_conclude, warn_budget
    message: str = ""


@dataclass
class StepRecord:
    """单步执行记录"""
    tool_name: str
    arguments: dict
    success: bool
    token_usage: int = 0
    timestamp: float = field(default_factory=lambda: __import__("time").time())
    section: str = ""  # generate_chart 的章节归属，用于 per-section 限图


class GuardRails:
    """tool call校验 + 执行监控中间件"""

    def __init__(
        self,
        max_retries: int = 3,
        max_total_steps: int = 20,
        token_budget: int = 50000,
        loop_window: int = 5,
        max_charts_per_section: int = 5,
    ):
        self.max_retries = max_retries
        self.max_total_steps = max_total_steps
        self.token_budget = token_budget
        self.loop_window = loop_window
        self.max_charts_per_section = max_charts_per_section

        # SQL安全检查模式
        self.dangerous_patterns = [
            r"\bDROP\s+(TABLE|DATABASE|SCHEMA|VIEW|INDEX)\b",
            r"\bDELETE\s+FROM\b",
            r"\bTRUNCATE\b",
            r"\bALTER\s+TABLE\b",
            r"\bUPDATE\s+\S+\s+SET\b",
            r"\bINSERT\s+INTO\b",
            r"\bCREATE\s+(TABLE|DATABASE|SCHEMA|VIEW|INDEX)\b",
            r"\bGRANT\b",
            r"\bREVOKE\b",
        ]

    def validate_tool_call(self, tool_name: str, arguments: dict) -> ValidationResult:
        """执行前校验tool call"""

        # SQL相关工具校验
        if tool_name.startswith("query_") or tool_name in ["execute_sql", "run_statistics"]:
            sql = arguments.get("sql", "") or arguments.get("query", "")
            if sql:
                result = self._validate_sql(sql)
                if result.blocked:
                    return result

        # 图表工具校验
        if tool_name == "generate_chart":
            result = self._validate_chart(arguments)
            if result.blocked:
                return result

        # 文件路径校验
        if tool_name in ["read_document", "write_report", "save_chart"]:
            result = self._validate_path(arguments)
            if result.blocked:
                return result

        return ValidationResult(blocked=False)

    def _validate_sql(self, sql: str) -> ValidationResult:
        """SQL校验"""
        if not sql or not sql.strip():
            return ValidationResult(blocked=True, reason="SQL为空")

        # 1. 安全检查（防危险操作）
        sql_upper = sql.upper()
        for pattern in self.dangerous_patterns:
            if re.search(pattern, sql_upper, re.IGNORECASE):
                return ValidationResult(
                    blocked=True,
                    reason=f"SQL包含危险操作: {pattern}",
                    suggestion="只允许SELECT查询，禁止DDL/DML操作",
                )

        # 2. 基本语法检查（用sqlparse，不发到DB）
        try:
            import sqlparse
            parsed = sqlparse.parse(sql)
            if not parsed or not parsed[0].tokens:
                return ValidationResult(
                    blocked=True,
                    reason="SQL语法错误: 无法解析",
                    suggestion="检查SQL语法",
                )

            # 检查是否是SELECT语句
            first_token = parsed[0].tokens[0]
            if hasattr(first_token, "ttype") and first_token.ttype is not None:
                if first_token.value.upper() != "SELECT":
                    return ValidationResult(
                        blocked=True,
                        reason="只允许SELECT查询",
                        suggestion="将查询改为SELECT语句",
                    )
        except ImportError:
            logger.warning("sqlparse未安装，跳过SQL语法检查")
        except Exception as e:
            logger.warning(f"SQL解析失败: {e}")

        # 3. 常见错误模式检测
        # 例如：把vaccine_name写成product_name
        if "product_name" in sql and "vaccine_name" not in sql:
            return ValidationResult(
                blocked=False,  # 不阻止，但给出建议
                suggestion="提示: 是否应该使用vaccine_name而非product_name?",
            )

        return ValidationResult(blocked=False)

    def _validate_chart(self, arguments: dict) -> ValidationResult:
        """图表参数校验"""
        chart_type = arguments.get("type", "")
        valid_types = ["bar", "line", "pie", "scatter", "heatmap", "control_chart", "pareto", "trend"]

        if chart_type and chart_type not in valid_types:
            return ValidationResult(
                blocked=True,
                reason=f"不支持的图表类型: {chart_type}",
                suggestion=f"支持的类型: {', '.join(valid_types)}",
            )

        # 检查必要参数
        if not arguments.get("data") and not arguments.get("query"):
            return ValidationResult(
                blocked=True,
                reason="图表缺少数据源",
                suggestion="提供data参数或query参数",
            )

        return ValidationResult(blocked=False)

    def _validate_path(self, arguments: dict) -> ValidationResult:
        """文件路径校验"""
        path = arguments.get("path", "") or arguments.get("file_path", "")
        if not path:
            return ValidationResult(blocked=False)

        # 安全检查：防止路径穿越
        if ".." in path or path.startswith("/etc") or path.startswith("/root"):
            return ValidationResult(
                blocked=True,
                reason="路径不安全",
                suggestion="使用相对路径或用户目录下的路径",
            )

        return ValidationResult(blocked=False)

    def monitor_trajectory(self, history: list[StepRecord]) -> RegulationAction:
        """每步执行后检查轨迹健康度"""

        if not history:
            return RegulationAction(action="continue")

        # 1. 步数预算
        if len(history) >= self.max_total_steps:
            return RegulationAction(
                action="force_conclude",
                message=f"已达{self.max_total_steps}步上限，请基于现有结果给出结论",
            )

        # 1.5 per-section 图表上限（SKILL 收敛约束）
        from collections import Counter
        chart_sections = Counter(
            s.section for s in history
            if s.tool_name == "generate_chart" and s.section
        )
        for section, count in chart_sections.items():
            if count > self.max_charts_per_section:
                return RegulationAction(
                    action="break_loop",
                    message=f"章节'{section}'已生成{count}张图表，超出每章节{self.max_charts_per_section}张上限。请停止为该章节生成新图表，进入下一个分析步骤或撰写结论。",
                )

        # 1.6 图表总量上限（全局兜底）
        chart_count = sum(1 for s in history if s.tool_name == "generate_chart")
        if chart_count >= 15:
            return RegulationAction(
                action="break_loop",
                message=f"已生成{chart_count}张图表，请停止生成新图表并开始撰写分析结论",
            )

        # 2. 死循环检测（最近loop_window步内）
        recent = history[-self.loop_window:]
        if len(recent) >= 3 and self._is_looping(recent):
            return RegulationAction(
                action="break_loop",
                message="检测到重复查询，建议换一种查询方式或缩小范围",
            )

        # 3. 无效重试检测
        retry_count = self._count_same_failures(history)
        if retry_count >= self.max_retries:
            return RegulationAction(
                action="escalate",
                message=f"工具调用失败{retry_count}次，建议向用户确认需求或换方案",
            )

        # 4. Token预算预警
        used_tokens = sum(s.token_usage for s in history)
        if used_tokens > self.token_budget * 0.8:
            return RegulationAction(
                action="warn_budget",
                message=f"已使用{used_tokens}/{self.token_budget} tokens，请精简输出",
            )

        return RegulationAction(action="continue")

    def _is_looping(self, recent: list[StepRecord]) -> bool:
        """检测最近几步是否在循环"""
        if len(recent) < 3:
            return False

        # 检查是否有连续相同的tool call
        signatures = []
        for step in recent:
            # 用tool_name + 参数摘要作为签名
            sig = f"{step.tool_name}:{json.dumps(step.arguments, sort_keys=True)[:100]}"
            signatures.append(sig)

        # 如果最近3步签名相同，认为在循环
        if len(set(signatures[-3:])) == 1:
            return True

        # 或者：最近5步中有3个相同签名
        from collections import Counter
        counts = Counter(signatures)
        if any(c >= 3 for c in counts.values()):
            return True

        return False

    def _count_same_failures(self, history: list[StepRecord]) -> int:
        """统计最近连续失败次数"""
        count = 0
        for step in reversed(history):
            if not step.success:
                count += 1
            else:
                break
        return count
