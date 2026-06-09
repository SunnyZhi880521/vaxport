"""SKILL 过程监控 — Layer 2 防御：ReAct 循环中追踪 SKILL 步骤完成度"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class StepStatus:
    """单个 SKILL 步骤的状态"""
    name: str
    description: str
    keywords: list[str]
    completed: bool = False
    evidence: list[str] = field(default_factory=list)


class SkillMonitor:
    """ReAct 循环中的 SKILL 遵循度监控

    通过追踪工具调用和输出来判断 SKILL 步骤是否完成，
    在每轮结束时生成提醒（不阻断执行）。
    """

    def __init__(self, checklist: dict):
        self.checklist = checklist
        self.steps: list[StepStatus] = []
        self.tool_calls: list[dict] = []
        self.round_num = 0

        for step_def in checklist.get("required_steps", []):
            self.steps.append(StepStatus(
                name=step_def["name"],
                description=step_def.get("description", ""),
                keywords=step_def.get("keywords", []),
            ))

    def on_tool_call(self, tool_name: str, tool_args: dict):
        """记录工具调用，判断是否匹配某个 SKILL 步骤"""
        self.tool_calls.append({"name": tool_name, "args": tool_args})

        call_text = f"{tool_name} {str(tool_args)}".lower()
        for step in self.steps:
            if step.completed:
                continue
            if any(kw.lower() in call_text for kw in step.keywords):
                step.completed = True
                step.evidence.append(f"{tool_name}({list(tool_args.keys())})")

    def on_round_end(self, assistant_text: str) -> Optional[str]:
        """每轮结束时检查进度，返回提醒文本（如有缺失步骤）

        返回 None 表示无需提醒。
        """
        self.round_num += 1

        text_lower = assistant_text.lower()
        for step in self.steps:
            if step.completed:
                continue
            if any(kw.lower() in text_lower for kw in step.keywords):
                step.completed = True
                step.evidence.append(f"round_{self.round_num}_text")

        missing = self.get_missing_steps()
        if not missing:
            return None

        if self.round_num < 3:
            return None

        missing_names = [s.name for s in missing]
        return (
            f"[SKILL 进度提醒] 尚未完成的分析步骤: {', '.join(missing_names)}。"
            f"请确保在最终回答前覆盖这些步骤。"
        )

    def get_missing_steps(self) -> list[StepStatus]:
        """返回未完成的 SKILL 步骤"""
        return [s for s in self.steps if not s.completed]

    def get_completion_rate(self) -> float:
        """返回步骤完成率"""
        if not self.steps:
            return 1.0
        completed = sum(1 for s in self.steps if s.completed)
        return completed / len(self.steps)

    def get_summary(self) -> dict:
        """返回监控摘要"""
        return {
            "total_steps": len(self.steps),
            "completed_steps": sum(1 for s in self.steps if s.completed),
            "completion_rate": round(self.get_completion_rate(), 2),
            "missing_steps": [s.name for s in self.get_missing_steps()],
            "total_tool_calls": len(self.tool_calls),
        }
