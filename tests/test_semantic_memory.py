"""SemanticMemory 单元测试 — 存储 + 召回 + 上下文构建 + 冷启动"""

import pytest
from unittest.mock import MagicMock, patch

from vaxport.semantic_memory import SemanticMemory, COLD_START_THRESHOLD


class TestSemanticMemoryInit:
    def test_init_without_db(self):
        db = MagicMock()
        db.is_connected = False
        sm = SemanticMemory(db)
        assert sm._schema_ready is False
        assert sm._indexed is False


class TestEnsureSchema:
    def test_schema_creation_success(self):
        db = MagicMock()
        db.is_connected = True
        db.execute_simple = MagicMock()
        sm = SemanticMemory(db)

        with patch("vaxport.semantic_memory._ensure_rag_schema", return_value={"status": "ready"}):
            result = sm._ensure_schema()
            assert result is True
            assert sm._schema_ready is True

    def test_schema_creation_failure(self):
        db = MagicMock()
        db.is_connected = True
        db.execute_simple = MagicMock(side_effect=Exception("pgvector not available"))
        sm = SemanticMemory(db)

        with patch("vaxport.semantic_memory._ensure_rag_schema", return_value={"status": "ready"}):
            result = sm._ensure_schema()
            assert result is False


class TestBuildContextSection:
    def test_cold_start_returns_empty(self):
        db = MagicMock()
        db.is_connected = True
        db.execute_query = MagicMock(return_value={"rows": [{"count": 0}]})
        sm = SemanticMemory(db)
        sm._schema_ready = True

        with patch.object(sm, "search_similar_cases", return_value=[]):
            result = sm.build_context_section("PEDV效价偏低")
            assert result == ""

    def test_warm_start_returns_context(self):
        db = MagicMock()
        db.is_connected = True
        db.execute_query = MagicMock(return_value={"rows": [{"count": 10}]})
        sm = SemanticMemory(db)
        sm._schema_ready = True

        cases = [
            {
                "query_summary": "PEDV效价偏低分析",
                "conclusion": "根因培养温度波动",
                "similarity": 0.85,
                "created_at": "2025-03-15",
            },
        ]
        with patch.object(sm, "search_similar_cases", return_value=cases):
            result = sm.build_context_section("PEDV效价偏低")
            assert "相似历史案例" in result
            assert "PEDV效价偏低分析" in result

    def test_low_similarity_returns_empty(self):
        db = MagicMock()
        db.is_connected = True
        db.execute_query = MagicMock(return_value={"rows": [{"count": 10}]})
        sm = SemanticMemory(db)
        sm._schema_ready = True

        cases = [
            {
                "query_summary": "不相关案例",
                "conclusion": "不相关结论",
                "similarity": 0.3,
                "created_at": "2025-01-01",
            },
        ]
        with patch.object(sm, "search_similar_cases", return_value=cases):
            result = sm.build_context_section("PEDV效价偏低")
            assert result == ""


class TestStoreAnalysisCase:
    def test_store_success(self):
        db = MagicMock()
        db.is_connected = True
        db.execute_simple = MagicMock()
        sm = SemanticMemory(db)
        sm._schema_ready = True

        with patch("vaxport.semantic_memory._get_embedding", return_value=[0.1] * 1536):
            result = sm.store_analysis_case(
                task_id="t1", query="PEDV效价偏低",
                conclusion="根因培养温度波动", agent_type="analyze_reporter",
                tables_used=["analog_quality.deviations"], task_type="统计分析",
            )
            assert result is True

    def test_store_embedding_failure(self):
        db = MagicMock()
        db.is_connected = True
        sm = SemanticMemory(db)
        sm._schema_ready = True

        with patch("vaxport.semantic_memory._get_embedding", return_value=None):
            result = sm.store_analysis_case(
                task_id="t1", query="test", conclusion="test",
                agent_type="general", tables_used=[], task_type="查询",
            )
            assert result is False


class TestKeywordSearch:
    def test_keyword_search_fallback(self):
        db = MagicMock()
        db.is_connected = True
        db.execute_query = MagicMock(return_value={
            "rows": [
                {"id": 1, "query_summary": "效价偏低", "conclusion": "温度波动",
                 "agent_type": "analyze_reporter", "tables_used": "[]",
                 "task_type": "统计分析", "severity": "", "created_at": "2025-01-01"},
            ],
        })
        sm = SemanticMemory(db)
        sm._schema_ready = False  # 触发关键词回退

        results = sm.search_similar_cases("效价偏低", top_k=3)
        assert len(results) == 1
        assert "效价偏低" in results[0]["query_summary"]


class TestAutoIndexDeviations:
    def test_auto_index(self):
        db = MagicMock()
        db.is_connected = True
        db.execute_query = MagicMock(return_value={
            "rows": [
                {"deviation_type": "low_potency", "description": "效价偏低",
                 "severity": "Major", "dev_id": "DEV-001"},
            ],
        })
        sm = SemanticMemory(db)
        sm._schema_ready = True

        with patch.object(sm, "store_anomaly_pattern", return_value=True):
            sm._auto_index_deviations()
            # 验证 store_anomaly_pattern 被调用
            assert sm.store_anomaly_pattern.called