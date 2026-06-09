"""语义召回层 — 复用 pgvector + DashScope embedding，补充 EAR 语义匹配缺口

SemanticMemory 存储+召回历史分析案例和异常模式，
与 EAR 程序性记忆（怎么做）互补，提供语义性记忆（参考什么）。

依赖: pgvector (PostgreSQL 扩展), dashscope embedding (复用 documents.py)
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from vaxport.db import Database
from vaxport.documents import _get_embedding, EMBEDDING_DIM, _ensure_rag_schema

logger = logging.getLogger(__name__)

# 语义召回冷启动阈值
COLD_START_THRESHOLD = 5
SIMILARITY_THRESHOLD = 0.75


class SemanticMemory:
    """语义记忆管理器 — 存储+召回历史分析案例和异常模式"""

    def __init__(self, db: Database):
        self.db = db
        self._schema_ready = False
        self._indexed = False  # 冷启动自动索引标记

    def _ensure_schema(self) -> bool:
        """创建 vaxport_rag.analysis_cases 表"""
        if self._schema_ready:
            return True

        # 先确保 vaxport_rag schema 存在（复用 documents.py）
        result = _ensure_rag_schema(self.db)
        if "error" in result:
            logger.warning(f"RAG schema 创建失败: {result['error']}")
            return False

        # 创建 analysis_cases 表
        setup_sql = """
        CREATE TABLE IF NOT EXISTS vaxport_rag.analysis_cases (
            id SERIAL PRIMARY KEY,
            case_type VARCHAR(30) NOT NULL,
            query_summary TEXT NOT NULL,
            conclusion TEXT NOT NULL,
            agent_type VARCHAR(30),
            tables_used TEXT,
            task_type VARCHAR(30),
            severity VARCHAR(10),
            metadata JSONB DEFAULT '{}',
            embedding VECTOR(1536),
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
        try:
            self.db.execute_simple(setup_sql)
            # 创建索引（可能因无数据而跳过）
            try:
                self.db.execute_simple(
                    "CREATE INDEX IF NOT EXISTS idx_analysis_cases_embedding "
                    "ON vaxport_rag.analysis_cases "
                    "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
                )
            except Exception:
                pass
            self._schema_ready = True
            return True
        except Exception as e:
            logger.warning(f"analysis_cases 表创建失败: {e}")
            # pgvector 不可用时标记，后续回退到关键词匹配
            self._schema_ready = False
            return False

    def store_analysis_case(self, task_id: str, query: str,
                             conclusion: str, agent_type: str,
                             tables_used: list[str], task_type: str) -> bool:
        """每次成功任务后，将查询+结论存入向量库。失败时记录 warning 不阻断。"""
        if not self._ensure_schema():
            logger.warning("analysis_cases 表不可用，跳过存储")
            return False

        # 生成 embedding
        embed_text = f"{query} {conclusion[:300]}"
        embedding = _get_embedding(embed_text)
        if not embedding:
            logger.warning("embedding 生成失败，跳过存储")
            return False

        tables_str = json.dumps(tables_used, ensure_ascii=False)
        try:
            embed_str = str(embedding).replace("[", "").replace("]", "")
            self.db.execute_simple(
                f"INSERT INTO vaxport_rag.analysis_cases "
                f"(case_type, query_summary, conclusion, agent_type, tables_used, "
                f"task_type, embedding) "
                f"VALUES ('analysis', %s, %s, %s, %s, %s, '[{embed_str}]'::vector)",
                (query[:500], conclusion[:500], agent_type, tables_str, task_type),
            )
            logger.info(f"语义记忆存储: {query[:50]} ({agent_type})")
            return True
        except Exception as e:
            logger.warning(f"语义记忆存储失败: {e}")
            return False

    def search_similar_cases(self, query: str, top_k: int = 3,
                              task_type: str = None) -> list[dict]:
        """语义搜索相似历史案例（cosine distance）。
        pgvector 不可用时回退到关键词匹配。"""
        if not self._ensure_schema():
            return self._keyword_search(query, top_k, task_type)

        embedding = _get_embedding(query)
        if not embedding:
            return self._keyword_search(query, top_k, task_type)

        embed_str = str(embedding).replace("[", "").replace("]", "")
        filter_clause = ""
        if task_type:
            filter_clause = f"AND task_type = '{task_type}'"

        try:
            result = self.db.execute_query(
                f"SELECT id, case_type, query_summary, conclusion, agent_type, "
                f"tables_used, task_type, severity, "
                f"1 - (embedding <=> '[{embed_str}]'::vector) as similarity "
                f"FROM vaxport_rag.analysis_cases "
                f"WHERE embedding IS NOT NULL {filter_clause} "
                f"ORDER BY embedding <=> '[{embed_str}]'::vector "
                f"LIMIT {top_k}"
            )
            rows = result.get("rows", [])
            return [self._format_case(row) for row in rows]
        except Exception as e:
            logger.warning(f"语义搜索失败: {e}, 回退到关键词匹配")
            return self._keyword_search(query, top_k, task_type)

    def store_anomaly_pattern(self, deviation_desc: str,
                               root_cause: str, capa_id: str,
                               severity: str) -> bool:
        """从 deviations 表索引异常模式。"""
        if not self._ensure_schema():
            return False

        embed_text = f"{deviation_desc} {root_cause}"
        embedding = _get_embedding(embed_text)
        if not embedding:
            return False

        try:
            embed_str = str(embedding).replace("[", "").replace("]", "")
            self.db.execute_simple(
                f"INSERT INTO vaxport_rag.analysis_cases "
                f"(case_type, query_summary, conclusion, severity, embedding, metadata) "
                f"VALUES ('anomaly', %s, %s, %s, '[{embed_str}]'::vector, "
                f"'{{\"capa_id\": \"{capa_id}\"}}'::jsonb)",
                (deviation_desc[:500], root_cause[:500], severity),
            )
            return True
        except Exception as e:
            logger.warning(f"异常模式存储失败: {e}")
            return False

    def build_context_section(self, query: str) -> str:
        """构建注入 system prompt 的相似案例段落。
        数据量 < COLD_START_THRESHOLD 时返回空字符串。"""
        # 冷启动检查
        if not self._ensure_schema():
            return ""

        try:
            result = self.db.execute_query(
                "SELECT COUNT(*) as count FROM vaxport_rag.analysis_cases"
            )
            count = result.get("rows", [{}])[0].get("count", 0)
            if count < COLD_START_THRESHOLD:
                # 冷启动期：尝试自动索引
                if not self._indexed:
                    self._auto_index_deviations()
                    self._indexed = True
                return ""
        except Exception:
            return ""

        cases = self.search_similar_cases(query, top_k=3)
        if not cases:
            return ""

        lines = ["## 相似历史案例"]
        for case in cases:
            sim = case.get("similarity", 0)
            if sim < SIMILARITY_THRESHOLD:
                continue
            date = case.get("created_at", "")
            summary = case.get("query_summary", "")
            conclusion = case.get("conclusion", "")
            lines.append(f"- [{date[:10]}] {summary}: {conclusion[:100]}")

        if len(lines) <= 1:  # 只有标题，无有效案例
            return ""

        return "\n".join(lines)

    def _auto_index_deviations(self):
        """首次启动时自动索引 analog_quality.deviations 表。"""
        if not self.db or not self.db.is_connected:
            return

        try:
            result = self.db.execute_query(
                "SELECT deviation_type, description, severity, dev_id "
                "FROM analog_quality.deviations LIMIT 200"
            )
            rows = result.get("rows", [])
            indexed = 0
            for row in rows:
                desc = str(row.get("description", ""))
                if not desc.strip():
                    continue
                severity = str(row.get("severity", ""))
                dev_id = str(row.get("dev_id", ""))
                if self.store_anomaly_pattern(
                    deviation_desc=desc,
                    root_cause=f"偏差类型: {row.get('deviation_type', '')}",
                    capa_id=dev_id,
                    severity=severity,
                ):
                    indexed += 1
            logger.info(f"冷启动自动索引: {indexed} 条偏差模式")
        except Exception as e:
            logger.warning(f"冷启动索引失败: {e}")

    def _keyword_search(self, query: str, top_k: int = 3,
                         task_type: str = None) -> list[dict]:
        """关键词搜索兜底（pgvector 不可用时使用）。"""
        if not self.db or not self.db.is_connected:
            return []

        # 提取关键词
        keywords = re.findall(r'\w+', query.lower())
        if not keywords:
            return []

        filter_parts = []
        for kw in keywords[:5]:
            filter_parts.append(
                f"(LOWER(query_summary) LIKE '%{kw}%' OR LOWER(conclusion) LIKE '%{kw}%')"
            )
        where = " AND ".join(filter_parts)
        if task_type:
            where += f" AND task_type = '{task_type}'"

        try:
            result = self.db.execute_query(
                f"SELECT id, case_type, query_summary, conclusion, agent_type, "
                f"tables_used, task_type, severity, created_at "
                f"FROM vaxport_rag.analysis_cases WHERE {where} "
                f"ORDER BY created_at DESC LIMIT {top_k}"
            )
            return [self._format_case(row) for row in result.get("rows", [])]
        except Exception:
            return []

    def _format_case(self, row: dict) -> dict:
        """格式化查询结果为统一格式。"""
        result = {
            "id": row.get("id"),
            "case_type": row.get("case_type", ""),
            "query_summary": row.get("query_summary", ""),
            "conclusion": row.get("conclusion", ""),
            "agent_type": row.get("agent_type", ""),
            "tables_used": row.get("tables_used", ""),
            "task_type": row.get("task_type", ""),
            "severity": row.get("severity", ""),
            "created_at": str(row.get("created_at", "")),
        }
        if "similarity" in row:
            sim = row.get("similarity")
            result["similarity"] = float(sim) if sim is not None else 0.0
        return result