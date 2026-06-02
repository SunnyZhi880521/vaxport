"""Schema 目录 — 持久化 PostgreSQL 元数据缓存，启动时自动增量更新"""

import json
import hashlib
import os
from pathlib import Path
from typing import Optional


class SchemaCatalog:
    """持久化的数据库 schema 目录。

    每次启动时对比 information_schema checksum，仅在结构变化时重新拉取。
    人工标注（business_description, key_joins）在自动刷新时保留。
    """

    CACHE_DIR = os.path.expanduser("~/.vaxport")
    CACHE_FILE = "schema_catalog.json"

    def __init__(self):
        self._catalog: dict = {}  # {full_table_name: table_info}
        self._checksums: dict = {}  # {db_name: md5_hash}

    # ── 公开接口 ──

    def load_or_refresh(self, databases: dict) -> dict:
        """加载缓存；数据库 schema 变化时自动重新拉取。

        Args:
            databases: {db_name: Database实例}
        Returns:
            完整的 catalog dict
        """
        cached = self._load_cache()
        current_checksums = self._compute_checksums(databases)

        if cached and cached.get("checksums") == current_checksums:
            self._catalog = cached["catalog"]
            self._checksums = current_checksums
            return self._catalog

        # 缓存过期或不存在 → 重新拉取
        new_catalog = self._fetch_full_schema(databases)

        # 保留旧缓存中的人工标注
        if cached and cached.get("catalog"):
            for table_name, old_info in cached["catalog"].items():
                if table_name in new_catalog:
                    if "business_description" in old_info:
                        new_catalog[table_name]["business_description"] = (
                            old_info["business_description"]
                        )
                    if "key_joins" in old_info:
                        new_catalog[table_name]["key_joins"] = (
                            old_info["key_joins"]
                        )

        self._catalog = new_catalog
        self._checksums = current_checksums
        self._save_cache({"checksums": current_checksums, "catalog": new_catalog})
        return self._catalog

    def get_table_list(self) -> list[str]:
        """返回所有表全限定名列表"""
        return sorted(self._catalog.keys())

    def get_table_info(self, table_name: str) -> Optional[dict]:
        """返回单表完整元数据（列清单 + PK + FK + 注释）"""
        return self._catalog.get(table_name)

    def build_context_section(self, max_tables: int = 80) -> str:
        """生成精简的系统 prompt 段（表清单 + 行数标记，不含全部列名）。

        Args:
            max_tables: 超过此数量时只列出 schema 级别摘要
        """
        if not self._catalog:
            return ""

        tables = sorted(self._catalog.items())

        if len(tables) > max_tables:
            schema_counts: dict[str, int] = {}
            for _name, info in tables:
                s = info["schema"]
                schema_counts[s] = schema_counts.get(s, 0) + 1
            lines = ["## 数据库表概况（仅列 schema 摘要）"]
            for s, count in sorted(schema_counts.items()):
                lines.append(f"  {s}: {count} 个表/视图")
            lines.append(
                f"  共 {len(tables)} 个表。"
                f"调用 get_table_info('schema.table') 获取单表详情。"
            )
            return "\n".join(lines)

        lines = [
            "## 数据库表概况",
            "[s]=≤100 [m]=100~1000 [l]=≥1000",
            "查询前调用 get_table_info('schema.table') 获取列清单和外键关系",
            "",
        ]

        # 按 schema 分组
        by_schema: dict[str, list] = {}
        for _name, info in tables:
            by_schema.setdefault(info["schema"], []).append(
                (info["table"], info)
            )

        for schema in sorted(by_schema.keys()):
            lines.append(f"### {schema}")
            sorted_tables = sorted(
                by_schema[schema],
                key=lambda x: -x[1].get("rows_estimate", 0),
            )
            for table, info in sorted_tables:
                n = info.get("rows_estimate", 0)
                tag = "s" if n <= 100 else "m" if n <= 1000 else "l"
                pk = info.get("primary_keys", [])
                pk_str = f" PK=({','.join(pk)})" if pk else ""
                fk_count = len(info.get("foreign_keys", []))
                fk_str = f" {fk_count}FK" if fk_count else ""
                desc = info.get("business_description", "")
                desc_str = f" — {desc}" if desc else ""
                lines.append(
                    f"  [{tag}~{n}] {table}{pk_str}{fk_str}{desc_str}"
                )
            lines.append("")

        lines.append(
            f"共 {len(tables)} 个表。"
            f"调用 get_table_info('schema.table') 获取列清单。"
        )
        return "\n".join(lines)

    # ── 内部方法 ──

    def _cache_path(self) -> Path:
        return Path(self.CACHE_DIR) / self.CACHE_FILE

    def _load_cache(self) -> Optional[dict]:
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def _save_cache(self, data: dict):
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _compute_checksums(self, databases: dict) -> dict:
        """为每个数据库计算 pg_tables 的 MD5 checksum"""
        checksums = {}
        for db_name, db in databases.items():
            if not db.is_connected:
                continue
            try:
                with db.cursor() as cur:
                    cur.execute("""
                        SELECT MD5(string_agg(
                            schemaname || '.' || tablename,
                            ',' ORDER BY schemaname, tablename
                        ))
                        FROM pg_tables
                        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                          AND schemaname NOT LIKE 'pg_%'
                    """)
                    row = cur.fetchone()
                    checksums[db_name] = row["md5"] if row and row["md5"] else ""
            except Exception:
                checksums[db_name] = ""
        return checksums

    def _fetch_full_schema(self, databases: dict) -> dict:
        """从 information_schema 拉取完整元数据。

        Returns:
            {full_table_name: {schema, table, db_name, columns,
             primary_keys, foreign_keys, rows_estimate, comment}}
        """
        catalog = {}

        for db_name, db in databases.items():
            if not db.is_connected:
                continue

            row_estimates = db.get_table_row_estimates()

            for full_name, est_info in row_estimates.items():
                schema, table = full_name.split(".", 1)

                columns = db._discover_columns(schema, table)
                pks = db._discover_pk(schema, table)
                fks = self._discover_fks(db, schema, table)
                comment = self._get_table_comment(db, schema, table)

                catalog[full_name] = {
                    "schema": schema,
                    "table": table,
                    "db_name": db_name,
                    "columns": [
                        {
                            "name": c["column_name"],
                            "type": c["data_type"],
                            "nullable": c["is_nullable"] == "YES",
                            "is_pk": c["column_name"] in pks,
                        }
                        for c in columns
                    ],
                    "primary_keys": pks,
                    "foreign_keys": fks,
                    "rows_estimate": est_info.get("rows_estimate", 0),
                    "comment": comment,
                }

        return catalog

    def _discover_fks(self, db, schema: str, table: str) -> list[dict]:
        """发现外键关系"""
        try:
            with db.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        kcu.column_name,
                        ccu.table_schema AS foreign_schema,
                        ccu.table_name AS foreign_table,
                        ccu.column_name AS foreign_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                        ON tc.constraint_name = ccu.constraint_name
                        AND tc.table_schema = ccu.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                        AND tc.table_schema = %s
                        AND tc.table_name = %s
                    ORDER BY kcu.ordinal_position
                    """,
                    (schema, table),
                )
                return [dict(row) for row in cur.fetchall()]
        except Exception:
            return []

    def _get_table_comment(self, db, schema: str, table: str) -> str:
        """获取表注释（COMMENT ON TABLE）"""
        try:
            with db.cursor() as cur:
                cur.execute(
                    """
                    SELECT obj_description(
                        (SELECT oid FROM pg_class
                         WHERE relname = %s
                           AND relnamespace = (
                               SELECT oid FROM pg_namespace WHERE nspname = %s
                           )),
                        'pg_class'
                    ) AS comment
                    """,
                    (table, schema),
                )
                row = cur.fetchone()
                return (row["comment"] or "") if row else ""
        except Exception:
            return ""