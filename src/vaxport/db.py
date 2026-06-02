"""数据库连接与安全查询 — psycopg2 连接池 + 参数化查询 + SSH 隧道"""

import json
import os
import re
import signal
import subprocess
import time
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool, sql
from psycopg2.extras import RealDictCursor

from vaxport.config import Config


class Database:
    """PostgreSQL 数据库连接管理器（支持 SSH 隧道）"""

    def __init__(self, config: Config):
        self.config = config
        self._pool: Optional[pool.ThreadedConnectionPool] = None
        self._connected = False
        self._tunnel_process: Optional[subprocess.Popen] = None
        self._tunnel_shared = False  # True 表示隧道由外部管理，不自行关闭

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _start_tunnel(self) -> tuple[str, int]:
        """建立 SSH 隧道，返回 (host, port) 供数据库连接使用。"""
        jump_host = self.config.ssh_tunnel_jump_host
        jump_port = self.config.ssh_tunnel_jump_port
        db_host = self.config.ssh_tunnel_db_host
        db_port = self.config.ssh_tunnel_db_port
        local_port = self.config.ssh_tunnel_local_port

        cmd = [
            "ssh", "-N",
            "-o", "ServerAliveInterval=30",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-L", f"{local_port}:{db_host}:{db_port}",
            "-p", str(jump_port),
            jump_host,
        ]

        self._tunnel_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp,  # 独立进程组，防止 Ctrl+C 传递
        )

        # 等待隧道就绪（最多 10 秒）
        import socket
        deadline = time.time() + 10
        while time.time() < deadline:
            if self._tunnel_process.poll() is not None:
                raise ConnectionError(
                    f"SSH 隧道进程异常退出 (exitcode={self._tunnel_process.returncode})。\n"
                    f"命令: {' '.join(cmd)}"
                )
            try:
                s = socket.create_connection(("localhost", local_port), timeout=2)
                s.close()
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.3)
        else:
            self._stop_tunnel()
            raise ConnectionError(
                f"SSH 隧道超时未就绪，无法连接到 localhost:{local_port}\n"
                f"请确认 SSH 跳板机可达: ssh -p {jump_port} {jump_host}"
            )

        return "localhost", local_port

    def _stop_tunnel(self):
        """关闭 SSH 隧道进程（共享隧道不自行关闭）"""
        if self._tunnel_shared:
            return
        if self._tunnel_process and self._tunnel_process.poll() is None:
            try:
                os.killpg(os.getpgid(self._tunnel_process.pid), signal.SIGTERM)
                self._tunnel_process.wait(timeout=5)
            except Exception:
                try:
                    self._tunnel_process.kill()
                except Exception:
                    pass
        self._tunnel_process = None

    def connect(self, **overrides):
        """建立连接池。可传入 overrides 覆盖 config 中的连接参数。
        若配置了 SSH 隧道，自动建立隧道后连接。"""
        host = overrides.get("host", self.config.pg_host)
        port = overrides.get("port", self.config.pg_port)

        # SSH 隧道（仅当未通过 overrides 覆盖 host 时生效）
        if self.config.ssh_tunnel_enabled and "host" not in overrides:
            host, port = self._start_tunnel()

        kwargs = {
            "host": host,
            "port": port,
            "dbname": overrides.get("dbname", self.config.pg_database),
            "user": overrides.get("user", self.config.pg_user),
            "cursor_factory": RealDictCursor,
            "options": "-c statement_timeout=30s",
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
        }
        password = overrides.get("password", self.config.pg_password)
        if password:
            kwargs["password"] = password

        try:
            self._pool = pool.ThreadedConnectionPool(
                minconn=1, maxconn=3, **kwargs
            )
            self._connected = True
        except Exception as e:
            self._connected = False
            self._stop_tunnel()
            raise ConnectionError(f"无法连接到 PostgreSQL: {e}")

    def close(self):
        """关闭连接池和 SSH 隧道"""
        if self._pool:
            self._pool.closeall()
            self._connected = False
        self._stop_tunnel()

    @contextmanager
    def cursor(self):
        """获取数据库游标（上下文管理器）"""
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                yield cur
        finally:
            self._pool.putconn(conn)

    def list_all_schemas(self, exclude_schemas: Optional[list] = None) -> list[dict]:
        """列出数据库中所有 schema 及其表/视图统计（含空 schema）"""
        if exclude_schemas is None:
            exclude_schemas = self.config.pg_exclude_schemas

        system_schemas = {"pg_catalog", "information_schema"}
        system_schemas.update(exclude_schemas)

        with self.cursor() as cur:
            cur.execute(
                """
                SELECT
                    s.schema_name,
                    COALESCE(t.table_count, 0) AS table_count,
                    COALESCE(v.view_count, 0) AS view_count,
                    COALESCE(m.mv_count, 0) AS matview_count
                FROM information_schema.schemata s
                LEFT JOIN (
                    SELECT schemaname, COUNT(*) AS table_count
                    FROM pg_tables
                    GROUP BY schemaname
                ) t ON s.schema_name = t.schemaname
                LEFT JOIN (
                    SELECT schemaname, COUNT(*) AS view_count
                    FROM pg_views
                    GROUP BY schemaname
                ) v ON s.schema_name = v.schemaname
                LEFT JOIN (
                    SELECT schemaname, COUNT(*) AS mv_count
                    FROM pg_matviews
                    GROUP BY schemaname
                ) m ON s.schema_name = m.schemaname
                WHERE s.schema_name NOT IN %s
                    AND s.schema_name NOT LIKE 'pg_%%'
                ORDER BY s.schema_name
                """,
                (tuple(system_schemas),),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_table_row_estimates(self, exclude_schemas: Optional[list] = None) -> dict:
        """快速估算所有用户表的行数（用 pg_stat_user_tables, 毫秒级）。

        返回: {schema.table: {"rows_estimate": N, "columns": [...]}}
        注意: n_live_tup 是估算值, 对刚 ANALYZE 的表较准确。
        """
        if exclude_schemas is None:
            exclude_schemas = self.config.pg_exclude_schemas

        system_schemas = {"pg_catalog", "information_schema"}
        system_schemas.update(exclude_schemas)

        with self.cursor() as cur:
            cur.execute(
                """
                SELECT
                    t.schemaname AS schema_name,
                    t.tablename AS table_name,
                    COALESCE(s.n_live_tup, 0) AS rows_estimate
                FROM pg_tables t
                LEFT JOIN pg_stat_user_tables s
                    ON t.schemaname = s.schemaname AND t.tablename = s.relname
                WHERE t.schemaname NOT IN %s
                ORDER BY t.schemaname, t.tablename
                """,
                (tuple(system_schemas),),
            )
            table_rows = {f"{r['schema_name']}.{r['table_name']}": r["rows_estimate"]
                          for r in cur.fetchall()}

        # 补充列信息
        result = {}
        for full_name, row_est in table_rows.items():
            schema, table = full_name.split(".", 1)
            cols = self._discover_columns(schema, table)
            result[full_name] = {
                "rows_estimate": int(row_est),
                "columns": [{"name": c["column_name"], "data_type": c["data_type"]} for c in cols],
            }
        return result

    def discover_schema(self, exclude_schemas: Optional[list] = None) -> dict:
        """自动发现数据库 schema"""
        if exclude_schemas is None:
            exclude_schemas = self.config.pg_exclude_schemas

        system_schemas = {"pg_catalog", "information_schema"}
        system_schemas.update(exclude_schemas)

        result = {"tables": {}, "views": {}, "matviews": {}}

        try:
            with self.cursor() as cur:
                # 发现表
                cur.execute(
                    """
                    SELECT schemaname, tablename
                    FROM pg_tables
                    WHERE schemaname NOT IN %s
                    ORDER BY schemaname, tablename
                    """,
                    (tuple(system_schemas),),
                )
                for row in cur.fetchall():
                    schema = row["schemaname"]
                    table = row["tablename"]
                    key = f"{schema}.{table}"
                    result["tables"][key] = {
                        "schema": schema,
                        "table": table,
                        "columns": self._discover_columns(schema, table),
                    }

                # 发现视图
                cur.execute(
                    """
                    SELECT schemaname, viewname
                    FROM pg_views
                    WHERE schemaname NOT IN %s
                    ORDER BY schemaname, viewname
                    """,
                    (tuple(system_schemas),),
                )
                for row in cur.fetchall():
                    schema = row["schemaname"]
                    view = row["viewname"]
                    key = f"{schema}.{view}"
                    result["views"][key] = {
                        "schema": schema,
                        "view": view,
                        "columns": self._discover_columns(schema, view),
                    }

                # 发现物化视图
                cur.execute(
                    """
                    SELECT schemaname, matviewname
                    FROM pg_matviews
                    WHERE schemaname NOT IN %s
                    ORDER BY schemaname, matviewname
                    """,
                    (tuple(system_schemas),),
                )
                for row in cur.fetchall():
                    schema = row["schemaname"]
                    mv = row["matviewname"]
                    key = f"{schema}.{mv}"
                    result["matviews"][key] = {
                        "schema": schema,
                        "matview": mv,
                        "columns": self._discover_columns(schema, mv),
                    }

        except Exception as e:
            result["error"] = str(e)

        return result

    def _discover_columns(self, schema: str, table: str) -> list[dict]:
        """发现表的列信息"""
        with self.cursor() as cur:
            cur.execute(
                """
                SELECT
                    column_name, data_type, is_nullable,
                    ordinal_position
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema, table),
            )
            return [dict(row) for row in cur.fetchall()]

    def _discover_pk(self, schema: str, table: str) -> list[str]:
        """发现主键列"""
        with self.cursor() as cur:
            cur.execute(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                    AND tc.table_schema = %s
                    AND tc.table_name = %s
                ORDER BY kcu.ordinal_position
                """,
                (schema, table),
            )
            return [row["column_name"] for row in cur.fetchall()]

    def execute_query(self, query_template: str, params: tuple = (),
                      timeout_ms: int = 60000) -> dict:
        """执行参数化查询（安全），默认 60s 超时。

        使用 SET LOCAL 设置事务级超时，事务结束后自动恢复。
        """
        with self.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = %s", (str(timeout_ms),))
            cur.execute(query_template, params)
            rows = cur.fetchall()
            return {
                "rows": [dict(r) for r in rows],
                "row_count": len(rows),
            }

    def execute_safe_select(
        self, schema: str, table: str, columns: list[str] = None,
        filters: dict = None, limit: int = 1000
    ) -> dict:
        """安全执行 SELECT 查询（参数化 + 强制 LIMIT）"""
        col_str = ", ".join(columns) if columns else "*"
        query = f"SELECT {col_str} FROM {schema}.{table}"

        params = []
        if filters:
            conditions = []
            for col, val in filters.items():
                # 验证列名只含安全字符
                if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", col):
                    return {"error": f"非法列名: {col}", "rows": [], "row_count": 0}
                if isinstance(val, tuple) and len(val) == 2:
                    op, actual_val = val
                    if op in (">=", "<=", ">", "<", "!=", "LIKE", "ILIKE"):
                        conditions.append(f"{col} {op} %s")
                        params.append(actual_val)
                    else:
                        return {"error": f"不支持的操作符: {op}", "rows": [], "row_count": 0}
                else:
                    conditions.append(f"{col} = %s")
                    params.append(val)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)

        # 强制 LIMIT
        if "LIMIT" not in query.upper():
            query += f" LIMIT {limit}"

        with self.cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            truncated = len(rows) >= limit
            return {
                "rows": [dict(r) for r in rows],
                "row_count": len(rows),
                "truncated": truncated,
                "sql": cur.query.decode() if hasattr(cur, "query") else query,
            }


def create_database(config: Config) -> Database:
    """工厂函数：创建数据库连接"""
    db = Database(config)
    db.connect()
    return db


class MultiDatabase:
    """多数据库连接管理器"""

    def __init__(self, config: Config):
        self.config = config
        self._dbs: dict[str, Database] = {}   # name → Database
        self.active_name: str = ""

    @property
    def is_connected(self) -> bool:
        return len(self._dbs) > 0 and any(d.is_connected for d in self._dbs.values())

    @property
    def names(self) -> list[str]:
        return list(self._dbs.keys())

    def get(self, name: str) -> Database | None:
        return self._dbs.get(name)

    def get_active(self) -> Database | None:
        return self._dbs.get(self.active_name)

    def connect_all(self):
        """连接所有配置的数据库（SSH 隧道仅建立一次，共享复用）"""
        db_list = self.config.db_configs

        # SSH 隧道：仅首次需要时建立
        tunnel_local_port = None
        if self.config.ssh_tunnel_enabled and db_list:
            tunnel_db = Database(self.config)
            try:
                tunnel_db._start_tunnel()
                tunnel_local_port = self.config.ssh_tunnel_local_port
            except Exception:
                pass  # 隧道建立失败，尝试直连

        for db_cfg in db_list:
            name = db_cfg["name"]
            db = Database(self.config)
            if tunnel_db and tunnel_db._tunnel_process:
                db._tunnel_process = tunnel_db._tunnel_process
                db._tunnel_shared = True
            # SSH 隧道启用时，所有数据库通过隧道连接
            if tunnel_local_port:
                db_cfg = dict(db_cfg)
                db_cfg["host"] = "localhost"
                db_cfg["port"] = tunnel_local_port
            try:
                db.connect(
                    host=db_cfg["host"],
                    port=db_cfg["port"],
                    dbname=db_cfg["database"],
                    user=db_cfg["user"],
                    password=db_cfg.get("password", ""),
                )
                self._dbs[name] = db
            except Exception:
                pass
        if self._dbs:
            self.active_name = list(self._dbs.keys())[0]

    def switch_to(self, name: str) -> bool:
        """切换当前工作数据库"""
        if name in self._dbs and self._dbs[name].is_connected:
            self.active_name = name
            return True
        return False

    def close_all(self):
        # 找出共享隧道进程（close 不会清理它们）
        shared_proc = None
        for db in self._dbs.values():
            if db._tunnel_process and db._tunnel_shared:
                shared_proc = db._tunnel_process
                break
        for db in self._dbs.values():
            db.close()
        self._dbs.clear()
        # 手动清理共享隧道
        if shared_proc and shared_proc.poll() is None:
            try:
                os.killpg(os.getpgid(shared_proc.pid), signal.SIGTERM)
                shared_proc.wait(timeout=5)
            except Exception:
                try:
                    shared_proc.kill()
                except Exception:
                    pass


def create_multi_database(config: Config) -> MultiDatabase:
    """工厂函数：创建多数据库连接"""
    mdb = MultiDatabase(config)
    mdb.connect_all()
    return mdb