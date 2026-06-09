"""Deep Research 单元测试 — 聚合SQL生成 + 摘要化 + 并发采集 + 容错"""

import json
import pytest
from unittest.mock import MagicMock, patch

from vaxport.deep_research import (
    DeepResearchPlan, DeepResearchCollector, TablePlan, TableResult,
    build_research_summary, RESEARCH_CACHE_DIR,
)


class TestDeepResearchPlan:
    def test_from_dict(self):
        d = {
            "tables_needed": [
                {"schema": "analog_quality", "table": "deviations",
                 "why": "偏差回顾", "key_filter": "2024年",
                 "aggregate_hint": "GROUP BY severity, status → COUNT"},
            ],
            "join_paths": ["deviations.batch_no ↔ final_product_qc.batch_no"],
            "output_sections": ["偏差总览", "趋势分析"],
            "task_id": "test1",
        }
        plan = DeepResearchPlan.from_dict(d)
        assert len(plan.tables_needed) == 1
        assert plan.tables_needed[0].schema == "analog_quality"
        assert plan.task_id == "test1"

    def test_from_json(self):
        json_str = json.dumps({
            "tables_needed": [
                {"schema": "analog_quality", "table": "deviations"},
            ],
        })
        plan = DeepResearchPlan.from_json(json_str)
        assert plan is not None
        assert len(plan.tables_needed) == 1

    def test_from_json_invalid(self):
        plan = DeepResearchPlan.from_json("not json")
        assert plan is None


class TestBuildAggregateSQL:
    def setup_method(self):
        self.db = MagicMock()
        self.collector = DeepResearchCollector(self.db)

    def test_deviations_aggregate(self):
        tp = TablePlan(
            schema="analog_quality", table="deviations",
            key_filter="2024年", aggregate_hint="GROUP BY severity, status → COUNT",
        )
        sql = self.collector.build_aggregate_sql(tp)
        assert "analog_quality.deviations" in sql
        assert "GROUP BY severity, status" in sql
        assert "COUNT(*)" in sql

    def test_qc_aggregate(self):
        tp = TablePlan(
            schema="analog_quality", table="final_product_qc",
            key_filter="产品=PEDV",
            aggregate_hint="按月份 AVG(potency), STDDEV(potency)",
        )
        sql = self.collector.build_aggregate_sql(tp)
        assert "analog_quality.final_product_qc" in sql
        assert "AVG" in sql or "STDDEV" in sql

    def test_default_group_by(self):
        tp = TablePlan(schema="analog_quality", table="deviations")
        sql = self.collector.build_aggregate_sql(tp)
        assert "GROUP BY severity, status" in sql

    def test_year_filter(self):
        tp = TablePlan(
            schema="analog_quality", table="deviations",
            key_filter="2024年",
        )
        sql = self.collector.build_aggregate_sql(tp)
        assert "2024" in sql
        assert "WHERE" in sql


class TestSummarizeResult:
    def setup_method(self):
        self.db = MagicMock()
        self.collector = DeepResearchCollector(self.db)

    def test_success_result(self):
        result = TableResult(
            schema="analog_quality", table="deviations",
            row_count=15,
            raw_data=[
                {"severity": "Critical", "status": "Closed", "count": 3},
                {"severity": "Major", "status": "Open", "count": 12},
            ],
        )
        summary = self.collector.summarize_result("analog_quality.deviations", result)
        assert "15条" in summary or "Critical" in summary

    def test_error_result(self):
        result = TableResult(
            schema="analog_quality", table="deviations",
            error="查询超时",
        )
        summary = self.collector.summarize_result("analog_quality.deviations", result)
        assert "❌" in summary

    def test_empty_result(self):
        result = TableResult(
            schema="analog_quality", table="deviations",
        )
        summary = self.collector.summarize_result("analog_quality.deviations", result)
        assert "0条" in summary


class TestBuildResearchSummary:
    def test_mixed_results(self):
        results = {
            "analog_quality.deviations": TableResult(
                schema="analog_quality", table="deviations",
                row_count=42,
                summary="analog_quality.deviations: 42条, Critical: 3条, Major: 12条",
            ),
            "analog_quality.capa_records": TableResult(
                schema="analog_quality", table="capa_records",
                error="表不存在",
            ),
        }
        text = build_research_summary(results)
        assert "Deep Research 数据采集摘要" in text
        assert "42条" in text
        assert "❌" in text

    def test_empty_results(self):
        text = build_research_summary({})
        assert text == ""


class TestCollect:
    def test_collect_with_db_error(self):
        db = MagicMock()
        db.is_connected = True
        db.execute_query.side_effect = Exception("PG连接断开")
        collector = DeepResearchCollector(db)
        plan = DeepResearchPlan(
            tables_needed=[TablePlan(schema="analog_quality", table="deviations")],
            task_id="err_test",
        )
        results = collector.collect(plan)
        key = "analog_quality.deviations"
        assert key in results
        assert results[key].error != ""