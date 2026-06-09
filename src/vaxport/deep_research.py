"""Deep Research 三阶段流水线 — 结构化数据采集+聚合摘要+跨表综合

将复杂任务从串行 ReAct 循环逐条查表改为三阶段流水线：
1. 扫描定位（auto_plan 增强，输出结构化数据定位计划）
2. 聚合采集（并发执行聚合SQL，返回3-5行摘要而非5000行原始数据）
3. 跨表关联综合（Agent基于摘要做判断，按需回查缓存详细数据）

核心目标：步数从15-18降到5-7，上下文数据量从5000行降到40行，
Agent分析维度被数据范围锁死 → SKILL一致性提升。
"""

import json
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from vaxport.db import Database

logger = logging.getLogger(__name__)

# 缓存目录
RESEARCH_CACHE_DIR = Path("data/research_cache")


# ── 数据结构 ──────────────────────────────────────

@dataclass
class TablePlan:
    """单张表的采集计划"""
    schema: str
    table: str
    why: str = ""
    key_filter: str = ""
    aggregate_hint: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "TablePlan":
        return cls(
            schema=d.get("schema", ""),
            table=d.get("table", ""),
            why=d.get("why", ""),
            key_filter=d.get("key_filter", ""),
            aggregate_hint=d.get("aggregate_hint", ""),
        )


@dataclass
class DeepResearchPlan:
    """三阶段流水线的结构化数据定位计划"""
    tables_needed: list[TablePlan] = field(default_factory=list)
    join_paths: list[str] = field(default_factory=list)
    output_sections: list[str] = field(default_factory=list)
    task_id: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "DeepResearchPlan":
        tables = [TablePlan.from_dict(t) for t in d.get("tables_needed", [])]
        return cls(
            tables_needed=tables,
            join_paths=d.get("join_paths", []),
            output_sections=d.get("output_sections", []),
            task_id=d.get("task_id", str(uuid.uuid4())[:8]),
        )

    @classmethod
    def from_json(cls, json_str: str) -> Optional["DeepResearchPlan"]:
        try:
            data = json.loads(json_str)
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None


@dataclass
class TableResult:
    """单张表的采集结果"""
    schema: str
    table: str
    summary: str = ""
    row_count: int = 0
    error: str = ""
    raw_data: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "table": self.table,
            "summary": self.summary,
            "row_count": self.row_count,
            "error": self.error,
            "raw_data": self.raw_data,
        }


# ── 核心采集器 ──────────────────────────────────────

class DeepResearchCollector:
    """阶段2: 按计划并发采集，聚合优先，摘要化结果"""

    def __init__(self, db: Database, max_workers: int = 5):
        self.db = db
        self.max_workers = max_workers
        self._cache_dir = RESEARCH_CACHE_DIR

    def collect(self, plan: DeepResearchPlan) -> dict[str, TableResult]:
        """并发执行聚合采集，返回 {schema.table: TableResult}。
        单表失败返回空摘要+error标记，不阻塞其他表。
        结果写入缓存文件供阶段3按需回查。
        """
        if not self.db or not self.db.is_connected:
            return {}

        RESEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        results: dict[str, TableResult] = {}

        # 生成聚合SQL
        sql_tasks = []
        for tp in plan.tables_needed:
            sql = self.build_aggregate_sql(tp)
            if sql:
                key = f"{tp.schema}.{tp.table}"
                sql_tasks.append((key, sql, tp))

        if not sql_tasks:
            return {}

        # 并发执行
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(sql_tasks))) as executor:
            futures = {}
            for key, sql, tp in sql_tasks:
                fut = executor.submit(self._execute_single, key, sql, tp)
                futures[fut] = key

            for fut in as_completed(futures, timeout=120):
                key = futures[fut]
                try:
                    result = fut.result()
                    results[key] = result
                except Exception as e:
                    results[key] = TableResult(
                        schema=key.split(".")[0],
                        table=key.split(".")[1],
                        error=f"并发执行异常: {e}",
                    )

        # 写入缓存文件
        if plan.task_id:
            cache_path = self._cache_dir / f"{plan.task_id}.json"
            try:
                cache_data = {k: v.to_dict() for k, v in results.items()}
                cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, default=str))
            except Exception as e:
                logger.warning(f"缓存写入失败: {e}")

        return results

    def build_aggregate_sql(self, tp: TablePlan) -> str:
        """根据 TablePlan 生成聚合SQL（GROUP BY + COUNT/AVG/STDDEV），
        而非全表扫描（SELECT *）。
        """
        full_table = f"{tp.schema}.{tp.table}"

        # 从 aggregate_hint 推断聚合方式
        hint = tp.aggregate_hint.lower()

        # GROUP BY 列推断
        group_cols = []
        agg_exprs = ["COUNT(*) as count"]

        # 从 hint 中提取 GROUP BY 列
        gb_match = re.search(r"group\s+by\s+([\w,\s]+)", hint)
        if gb_match:
            group_cols = [c.strip() for c in gb_match.group(1).split(",")]

        # 从 hint 中提取聚合函数
        if "avg" in hint:
            # 尝试提取 AVG 的目标列
            avg_match = re.search(r"avg\((\w+)\)", hint)
            if avg_match:
                agg_exprs.append(f"AVG({avg_match.group(1)}) as avg_{avg_match.group(1)}")
        if "stddev" in hint or "标准差" in hint:
            agg_exprs.append("STDDEV(potency) as stddev_potency")  # 通用默认
        if "min" in hint or "最早" in hint or "first" in hint:
            agg_exprs.append("MIN(created_date) as first_date")
        if "max" in hint or "最晚" in hint or "last" in hint:
            agg_exprs.append("MAX(created_date) as last_date")

        # 如果没有指定 GROUP BY，按表类型给默认分组
        if not group_cols:
            # 常见疫苗质量表的默认分组
            if "deviations" in tp.table:
                group_cols = ["severity", "status"]
            elif "capa" in tp.table:
                group_cols = ["status"]
            elif "qc" in tp.table or "final_product" in tp.table:
                group_cols = ["product_type"]
            elif "batch" in tp.table:
                group_cols = ["product_type"]
            elif "stability" in tp.table:
                group_cols = ["product_type", "study_condition"]
            else:
                group_cols = []  # 无分组，只算总量

        # 构建 WHERE
        where_clause = ""
        if tp.key_filter:
            # key_filter 可能是自然语言，尝试提取年份和产品
            year_match = re.search(r"(20\d{2})", tp.key_filter)
            product_match = re.search(r"产品[=为]\s*(\w+)|product\s*[=为]\s*(\w+)", tp.key_filter)
            filters = []
            if year_match:
                year = year_match.group(1)
                # 尝试日期列名
                for date_col in ["created_date", "reported_date", "batch_date", "test_date", "start_date"]:
                    filters.append(f"{date_col} >= '{year}-01-01'")
                    break  # 只用第一个匹配的日期列
            if product_match:
                product = product_match.group(1) or product_match.group(2)
                filters.append(f"product_type = '{product}'")
            if filters:
                where_clause = "WHERE " + " AND ".join(filters)

        # 最终SQL
        select_exprs = ", ".join(group_cols + agg_exprs) if group_cols else ", ".join(agg_exprs)
        group_by = f"\nGROUP BY {', '.join(group_cols)}" if group_cols else ""

        sql = f"SELECT {select_exprs}\nFROM {full_table}\n{where_clause}{group_by}"
        return sql.strip()

    def summarize_result(self, key: str, result: TableResult) -> str:
        """将采集结果压缩为3-5行统计摘要文本。"""
        if result.error:
            return f"{key}: ❌ {result.error}"

        if not result.raw_data:
            return f"{key}: 0条数据"

        rows = result.raw_data
        # 尝试构建自然语言摘要
        parts = []
        total = 0

        # 检查是否有 count 列
        for row in rows:
            count_val = row.get("count", 1)
            if isinstance(count_val, (int, float)):
                total += int(count_val)

            # 构建分组描述
            desc_parts = []
            for k, v in row.items():
                if k == "count":
                    continue
                if isinstance(v, (int, float)) and k not in ("count",):
                    desc_parts.append(f"{k}={v}")
                elif isinstance(v, str) and v:
                    desc_parts.append(str(v))

            count_str = str(int(count_val)) if isinstance(count_val, (int, float)) else "?"
            if desc_parts:
                parts.append(f"{', '.join(desc_parts)}: {count_str}条")
            else:
                parts.append(f"{count_str}条")

        summary_line = f"{key}: {total}条"
        if parts:
            summary_line += ", " + ", ".join(parts[:5])  # 最多5个分组

        return summary_line

    def retrieve_detail(self, task_id: str, schema: str, table: str,
                         filter_dict: dict = None) -> dict:
        """阶段3按需回查：从缓存文件中提取特定表的详细数据。"""
        cache_path = self._cache_dir / f"{task_id}.json"
        if not cache_path.exists():
            return {"error": f"缓存不可用: {task_id}"}

        try:
            cache_data = json.loads(cache_path.read_text())
        except Exception:
            return {"error": "缓存文件损坏"}

        key = f"{schema}.{table}"
        table_data = cache_data.get(key)
        if not table_data:
            return {"error": f"表 {key} 不在缓存中"}

        # 如果有过滤条件，在 raw_data 中筛选
        raw = table_data.get("raw_data", [])
        if filter_dict and raw:
            filtered = []
            for row in raw:
                match = True
                for k, v in filter_dict.items():
                    if str(row.get(k, "")) != str(v):
                        match = False
                        break
                if match:
                    filtered.append(row)
            raw = filtered

        return {"rows": raw, "row_count": len(raw)}

    # ── 内部方法 ──────────────────────────────────────

    def _execute_single(self, key: str, sql: str, tp: TablePlan) -> TableResult:
        """执行单条聚合SQL，失败时重试1次。"""
        schema, table = key.split(".", 1)
        max_retries = 1

        for attempt in range(max_retries + 1):
            try:
                data = self.db.execute_query(sql)
                rows = data.get("rows", [])
                row_count = data.get("row_count", len(rows))
                result = TableResult(
                    schema=schema, table=table,
                    row_count=row_count, raw_data=rows,
                )
                result.summary = self.summarize_result(key, result)
                return result
            except Exception as e:
                error_str = str(e)
                is_syntax = any(kw in error_str.lower()
                               for kw in ["syntax error", "42601", "does not exist"])
                if is_syntax or attempt == max_retries:
                    logger.warning(f"聚合采集失败 {key}: {error_str}")
                    return TableResult(
                        schema=schema, table=table,
                        error=error_str,
                    )

        return TableResult(schema=schema, table=table, error="未知错误")


def build_research_summary(results: dict[str, TableResult]) -> str:
    """将所有采集结果汇总为可注入 system message 的文本。"""
    if not results:
        return ""

    lines = ["## Deep Research 数据采集摘要\n"]
    for key, result in results.items():
        if result.error:
            lines.append(f"- {key}: ❌ {result.error}")
        elif result.summary:
            lines.append(f"- {result.summary}")
        else:
            lines.append(f"- {key}: {result.row_count}行数据")

    lines.append("\n以上为聚合摘要，如需特定表的详细数据请使用 retrieve_detail。")
    return "\n".join(lines)