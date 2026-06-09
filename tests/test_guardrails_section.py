"""GuardRails per-section 限图测试"""

import pytest
from vaxport.ear.guard_rails import GuardRails, StepRecord, RegulationAction


def _sql_step(i):
    return StepRecord(tool_name="query_table", arguments={"sql": f"SELECT {i}"}, success=True, section="")


def _chart_step(section, i=0):
    return StepRecord(
        tool_name="generate_chart",
        arguments={"section": section, "data": f"{{x:[{i}]}}"},
        success=True,
        section=section,
    )


def _chart_step_no_section(i=0):
    return StepRecord(
        tool_name="generate_chart",
        arguments={"data": f"{{x:[{i}]}}"},
        success=True,
        section="",
    )


class TestPerSectionChartLimit:
    """per-section 图表上限测试"""

    def test_over_5_same_section_triggers_break(self):
        gr = GuardRails(max_charts_per_section=5, max_total_steps=30)
        history = [_sql_step(i) for i in range(6)] + [_chart_step("帕累托分析", i) for i in range(6)]
        action = gr.monitor_trajectory(history)
        assert action.action == "break_loop"
        assert "帕累托分析" in action.message

    def test_5_same_section_does_not_trigger(self):
        gr = GuardRails(max_charts_per_section=5, max_total_steps=30)
        history = [_sql_step(i) for i in range(6)] + [_chart_step("帕累托分析", i) for i in range(5)]
        action = gr.monitor_trajectory(history)
        assert action.action == "continue"

    def test_different_sections_each_5_does_not_trigger(self):
        gr = GuardRails(max_charts_per_section=5, max_total_steps=40, loop_window=10)
        history = []
        for i in range(10):
            history.append(_sql_step(i))
        for section in ["帕累托分析", "趋势分析"]:
            for i in range(5):
                history.append(_chart_step(section, i))
        # 2 sections × 5 charts = 10 total, per-section each 5 (not > 5), global 10 < 15
        action = gr.monitor_trajectory(history)
        assert action.action == "continue"

    def test_empty_section_not_counted_per_section(self):
        gr = GuardRails(max_charts_per_section=5, max_total_steps=30)
        history = [_sql_step(i) for i in range(6)] + [_chart_step_no_section(i) for i in range(5)]
        action = gr.monitor_trajectory(history)
        # per-section 不触发（section为空不计入），全局15也不触发（5<15）
        assert action.action == "continue"

    def test_global_15_limit_still_works(self):
        gr = GuardRails(max_charts_per_section=5, max_total_steps=30)
        history = [_sql_step(i) for i in range(5)]
        for i in range(15):
            history.append(_chart_step(f"section_{i}", i))
        action = gr.monitor_trajectory(history)
        assert action.action == "break_loop"
        assert "15" in action.message

    def test_per_section_triggered_before_global(self):
        """per-section 限图在全局15之前触发"""
        gr = GuardRails(max_charts_per_section=5, max_total_steps=30)
        history = [_sql_step(i) for i in range(5)]
        # 同一section 6 charts，全局只有6 < 15
        for i in range(6):
            history.append(_chart_step("偏差总览", i))
        action = gr.monitor_trajectory(history)
        assert action.action == "break_loop"
        assert "偏差总览" in action.message  # per-section 触发，不是全局

    def test_max_charts_per_section_customizable(self):
        gr = GuardRails(max_charts_per_section=3, max_total_steps=30)
        history = [_sql_step(i) for i in range(5)] + [_chart_step("数据准备", i) for i in range(4)]
        action = gr.monitor_trajectory(history)
        assert action.action == "break_loop"
        assert "数据准备" in action.message

    def test_section_from_step_record_not_arguments(self):
        """section 字段来自 StepRecord.section，而非 arguments dict"""
        gr = GuardRails(max_charts_per_section=5, max_total_steps=40, loop_window=10)
        # arguments 有 section 但 StepRecord.section 为空 → per-section 不计入
        history = [_sql_step(i) for i in range(10)] + [
            StepRecord(
                tool_name="generate_chart",
                arguments={"section": "帕累托分析", "data": f"{{x:[{i}]}}"},
                success=True,
                section="",  # StepRecord.section 为空 → per-section 不计数
            ) for i in range(6)
        ]
        action = gr.monitor_trajectory(history)
        # per-section 不触发（StepRecord.section 为空），全局6 < 15
        assert action.action == "continue"